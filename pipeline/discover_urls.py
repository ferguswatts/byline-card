"""URL discovery script — fetches sitemaps/feeds from all outlets, stores in discovered_urls.

Run: python -m pipeline.discover_urls [--outlet OUTLET]

Runs each outlet sequentially with WAL checkpoint after each to prevent corruption.
Tags URLs to journalists where the author can be inferred from the URL structure.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from pipeline.db import get_connection, init_db, migrate_db

log = logging.getLogger(__name__)


def _tag_journalist(conn, url: str, outlet: str) -> int | None:
    """Try to match a URL to a journalist in the DB. Returns journalist_id or None."""
    journalists = conn.execute(
        "SELECT id, slug, name FROM journalists WHERE outlet = ?", (outlet,)
    ).fetchall()

    url_lower = url.lower()

    for j in journalists:
        slug = j["slug"]
        # Strip outlet suffix from slug for URL matching
        # e.g. "barry-soper-newstalkzb" → "barry-soper"
        name_slug = slug.rsplit("-", 1)[0] if "-" in slug else slug
        # More careful: strip known outlet suffixes
        for suffix in ["-nzherald", "-newstalkzb", "-rnz", "-stuff", "-1news", "-tvnz", "-thespinoff", "-newsroom"]:
            if slug.endswith(suffix):
                name_slug = slug[: -len(suffix)]
                break

        if name_slug in url_lower:
            return j["id"]

    return None


async def discover_newstalkzb(conn) -> int:
    """Newstalk ZB: sitemap-based, author in URL for opinion pieces."""
    from pipeline.sites.newstalkzb import NewstalkZBAdapter

    adapter = NewstalkZBAdapter()
    author_urls = await adapter.get_all_urls_by_author()

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for author_slug, urls in author_urls.items():
        # Try to find journalist by matching slug
        journalist_id = None
        if author_slug != "_opinion_unattributed":
            row = conn.execute(
                "SELECT id FROM journalists WHERE slug LIKE ?",
                (f"%{author_slug}%",),
            ).fetchone()
            if row:
                journalist_id = row["id"]

        for url in urls:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                    (url, journalist_id, "Newstalk ZB", "sitemap", now),
                )
                count += 1
            except Exception:
                pass

    conn.commit()
    tagged = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='Newstalk ZB' AND journalist_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='Newstalk ZB'"
    ).fetchone()[0]
    log.info(f"Newstalk ZB: {total} URLs stored ({tagged} tagged to journalists)")
    return total


async def discover_stuff(conn) -> int:
    """Stuff: Wayback CDX + author pages."""
    from pipeline.sites.stuff import StuffAdapter

    adapter = StuffAdapter()
    urls = await adapter.get_article_urls()

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for url in urls:
        journalist_id = _tag_journalist(conn, url, "Stuff")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                (url, journalist_id, "Stuff", "wayback+author", now),
            )
            count += 1
        except Exception:
            pass

    conn.commit()
    tagged = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='Stuff' AND journalist_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='Stuff'"
    ).fetchone()[0]
    log.info(f"Stuff: {total} URLs stored ({tagged} tagged to journalists)")
    return total


async def discover_rnz(conn) -> int:
    """RNZ: gzipped sitemaps."""
    from pipeline.sites.rnz import RNZAdapter

    adapter = RNZAdapter()
    urls = await adapter.get_article_urls()

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for url in urls:
        journalist_id = _tag_journalist(conn, url, "RNZ")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                (url, journalist_id, "RNZ", "sitemap", now),
            )
            count += 1
        except Exception:
            pass

    conn.commit()
    tagged = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='RNZ' AND journalist_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='RNZ'"
    ).fetchone()[0]
    log.info(f"RNZ: {total} URLs stored ({tagged} tagged to journalists)")
    return total


async def discover_nzherald(conn) -> int:
    """NZ Herald: Arc Publishing sitemaps."""
    from pipeline.sites.nzherald import NZHeraldAdapter

    adapter = NZHeraldAdapter()
    urls = await adapter.get_article_urls()

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for url in urls:
        # Herald URLs don't contain author names — all untagged
        try:
            conn.execute(
                "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                (url, None, "NZ Herald", "sitemap", now),
            )
            count += 1
        except Exception:
            pass

    conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='NZ Herald'"
    ).fetchone()[0]
    log.info(f"NZ Herald: {total} URLs stored (0 tagged — author not in URL)")
    return total


async def discover_1news(conn) -> int:
    """1News: politics section + RSS + Google News backfill per journalist."""
    from pipeline.sites.onenews import OneNewsAdapter

    adapter = OneNewsAdapter()

    # Get general recent URLs first
    urls = await adapter.get_article_urls()

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for url in urls:
        journalist_id = _tag_journalist(conn, url, "1News")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                (url, journalist_id, "1News", "section+rss", now),
            )
            count += 1
        except Exception:
            pass

    # Also do backfill per journalist
    journalists = conn.execute(
        "SELECT id, slug, name FROM journalists WHERE outlet IN ('1News', 'TVNZ')"
    ).fetchall()

    for j in journalists:
        # Strip outlet suffix for search
        name_slug = j["slug"]
        for suffix in ["-1news", "-tvnz"]:
            if name_slug.endswith(suffix):
                name_slug = name_slug[: -len(suffix)]
                break

        backfill_urls = await adapter.get_article_urls(
            author_slug=name_slug, backfill=True
        )
        for url in backfill_urls:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                    (url, j["id"], "1News", "gnews-backfill", now),
                )
            except Exception:
                pass

    conn.commit()
    tagged = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='1News' AND journalist_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='1News'"
    ).fetchone()[0]
    log.info(f"1News: {total} URLs stored ({tagged} tagged to journalists)")
    return total


async def discover_spinoff(conn) -> int:
    """The Spinoff: sitemap index + author pages."""
    from pipeline.sites.spinoff import SpinoffAdapter

    adapter = SpinoffAdapter()

    # Get all sitemap URLs
    urls = await adapter.get_article_urls()

    count = 0
    now = datetime.now(timezone.utc).isoformat()

    for url in urls:
        journalist_id = _tag_journalist(conn, url, "The Spinoff")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                (url, journalist_id, "The Spinoff", "sitemap+author", now),
            )
            count += 1
        except Exception:
            pass

    # Also try author pages for our journalists
    journalists = conn.execute(
        "SELECT id, slug, name FROM journalists WHERE outlet = 'The Spinoff'"
    ).fetchall()
    for j in journalists:
        name_slug = j["slug"]
        for suffix in ["-thespinoff"]:
            if name_slug.endswith(suffix):
                name_slug = name_slug[: -len(suffix)]
                break

        author_urls = await adapter.get_article_urls(author_slug=name_slug)
        for url in author_urls:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                    (url, j["id"], "The Spinoff", "author-page", now),
                )
            except Exception:
                pass

    conn.commit()
    tagged = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='The Spinoff' AND journalist_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='The Spinoff'"
    ).fetchone()[0]
    log.info(f"The Spinoff: {total} URLs stored ({tagged} tagged to journalists)")
    return total


async def discover_newsroom(conn) -> int:
    """Newsroom: author pages only (WordPress, no public sitemaps)."""
    from pipeline.sites.newsroom import NewsroomAdapter

    adapter = NewsroomAdapter()
    count = 0
    now = datetime.now(timezone.utc).isoformat()

    journalists = conn.execute(
        "SELECT id, slug, name FROM journalists WHERE outlet = 'Newsroom'"
    ).fetchall()

    for j in journalists:
        name_slug = j["slug"]
        for suffix in ["-newsroom"]:
            if name_slug.endswith(suffix):
                name_slug = name_slug[: -len(suffix)]
                break

        urls = await adapter.get_article_urls(author_slug=name_slug)
        for url in urls:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO discovered_urls (url, journalist_id, outlet, source, discovered_at) VALUES (?, ?, ?, ?, ?)",
                    (url, j["id"], "Newsroom", "author-page", now),
                )
                count += 1
            except Exception:
                pass

    conn.commit()
    tagged = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='Newsroom' AND journalist_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE outlet='Newsroom'"
    ).fetchone()[0]
    log.info(f"Newsroom: {total} URLs stored ({tagged} tagged to journalists)")
    return total


OUTLETS = {
    "newstalkzb": discover_newstalkzb,
    "stuff": discover_stuff,
    "rnz": discover_rnz,
    "nzherald": discover_nzherald,
    "1news": discover_1news,
    "spinoff": discover_spinoff,
    "newsroom": discover_newsroom,
}


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    conn = get_connection()
    init_db(conn)
    migrate_db(conn)

    # Filter to specific outlet if requested
    outlet_filter = None
    if "--outlet" in sys.argv:
        idx = sys.argv.index("--outlet")
        if idx + 1 < len(sys.argv):
            outlet_filter = sys.argv[idx + 1].lower()

    outlets_to_run = OUTLETS if not outlet_filter else {outlet_filter: OUTLETS[outlet_filter]}

    grand_total = 0
    for name, discover_fn in outlets_to_run.items():
        log.info(f"=== Starting discovery for {name} ===")
        try:
            total = await discover_fn(conn)
            grand_total += total
        except Exception as e:
            log.error(f"Discovery failed for {name}: {e}")
            import traceback
            traceback.print_exc()

        # Checkpoint WAL after each outlet to prevent corruption
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        log.info(f"=== Checkpointed after {name} ===\n")

    # Final summary
    rows = conn.execute(
        "SELECT outlet, COUNT(*) as total, SUM(CASE WHEN journalist_id IS NOT NULL THEN 1 ELSE 0 END) as tagged FROM discovered_urls GROUP BY outlet ORDER BY total DESC"
    ).fetchall()

    log.info("=== DISCOVERY SUMMARY ===")
    for r in rows:
        log.info(f"  {r['outlet']}: {r['total']:,} URLs ({r['tagged']:,} tagged)")
    log.info(f"  TOTAL: {conn.execute('SELECT COUNT(*) FROM discovered_urls').fetchone()[0]:,} URLs")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
