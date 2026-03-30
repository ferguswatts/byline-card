#!/usr/bin/env python3
"""Long-running author tagger — run overnight to tag all discovered URLs.

Usage:
    python pipeline/tag_all_authors.py

Processes all outlets in order of efficiency:
  1. Newstalk ZB  — instant (URL parsing)
  2. Stuff         — fast (JSON API, no HTML)
  3. The Spinoff   — HTML fetch, 32K URLs
  4. RNZ           — HTML fetch, political articles only (21K)
  5. NZ Herald     — streaming 16KB per page, ~985K URLs
  6. 1News         — HTML fetch, 104 URLs

Progress is printed every 5,000 URLs. Checkpoints WAL after each outlet
and every 10,000 URLs within large outlets.

Estimated runtime: 8-12 hours (mostly Herald).
Disk: no significant growth — just fills in author_name TEXT column.

Ctrl+C is safe — progress is committed in batches.
"""

import asyncio
import logging
import re
import signal
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
import trafilatura

from pipeline.db import get_connection, init_db, migrate_db
from pipeline.tag_authors import (
    _build_name_lookup,
    _match_author,
    tag_newstalkzb,
    tag_stuff,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tag-all")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Graceful shutdown on Ctrl+C
_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    if _shutdown:
        log.warning("Force quit")
        sys.exit(1)
    log.info("Shutting down gracefully after current batch... (Ctrl+C again to force)")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ─── Herald streaming tagger (16KB per page) ──────────────────────────────

async def _herald_get_author(session, sem, url):
    """Stream first 16KB of Herald page to extract author from JSON-LD."""
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    return None
                buf = b""
                async for chunk in resp.content.iter_chunked(8192):
                    buf += chunk
                    if len(buf) > 16384:
                        break
                text = buf.decode("utf-8", errors="ignore")
                m = re.search(r'"author"[^}]*?"name"\s*:\s*"([^"]+)"', text)
                return m.group(1) if m else None
        except Exception:
            return None


# ─── Generic HTML tagger ──────────────────────────────────────────────────

async def _html_get_author(session, sem, url):
    """Fetch full HTML page and extract author via trafilatura or JSON-LD."""
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except Exception:
            return None

    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.author:
            return metadata.author
    except Exception:
        pass

    ld = re.search(r'"author"[^}]*?"name"\s*:\s*"([^"]+)"', html)
    if ld:
        return ld.group(1)

    meta = re.search(r'<meta[^>]*name="author"[^>]*content="([^"]+)"', html)
    return meta.group(1) if meta else None


# ─── Batch processor ─────────────────────────────────────────────────────

async def process_outlet(
    conn,
    lookup,
    outlet: str,
    fetch_fn,
    concurrency: int = 10,
    batch_size: int = 50,
    checkpoint_every: int = 10000,
    where_extra: str = "",
):
    """Generic batch processor for any outlet."""
    query = f"""
        SELECT id, url FROM discovered_urls
        WHERE outlet = ? AND author_name IS NULL {where_extra}
    """
    rows = conn.execute(query, (outlet,)).fetchall()
    total = len(rows)
    if total == 0:
        log.info(f"{outlet}: nothing to process")
        return

    log.info(f"{outlet}: {total:,} untagged URLs to process")

    tagged = 0
    authors_found = 0
    checked = 0
    sem = asyncio.Semaphore(concurrency)
    start = datetime.now()

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for i in range(0, total, batch_size):
            if _shutdown:
                log.info(f"{outlet}: shutdown requested, stopping at {checked:,}/{total:,}")
                break

            batch = rows[i : i + batch_size]
            tasks = [fetch_fn(session, sem, r["url"]) for r in batch]
            results = await asyncio.gather(*tasks)

            for row, author in zip(batch, results):
                checked += 1
                if author:
                    authors_found += 1
                    jid = _match_author(author, lookup)
                    conn.execute(
                        "UPDATE discovered_urls SET author_name = ?, journalist_id = ? WHERE id = ?",
                        (author, jid, row["id"]),
                    )
                    if jid:
                        tagged += 1

            conn.commit()

            # Periodic checkpoint to prevent WAL bloat
            if checked % checkpoint_every < batch_size:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            # Progress report
            if checked % 5000 < batch_size:
                elapsed = (datetime.now() - start).total_seconds()
                rate = checked / elapsed if elapsed > 0 else 0
                eta_mins = (total - checked) / rate / 60 if rate > 0 else 0
                log.info(
                    f"{outlet}: {checked:,}/{total:,} "
                    f"({checked/total*100:.1f}%) | "
                    f"authors: {authors_found:,} | tracked: {tagged:,} | "
                    f"{rate:.0f} URLs/s | ETA: {eta_mins:.0f}min"
                )

            await asyncio.sleep(0.1)

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    elapsed = (datetime.now() - start).total_seconds()
    log.info(
        f"{outlet} DONE: {checked:,} checked, "
        f"{authors_found:,} authors found, {tagged:,} tracked "
        f"({elapsed/60:.1f} min)"
    )


def _print_summary(conn):
    """Print per-outlet and per-journalist summary."""
    rows = conn.execute("""
        SELECT outlet, COUNT(*) as total,
               SUM(CASE WHEN author_name IS NOT NULL THEN 1 ELSE 0 END) as with_author,
               SUM(CASE WHEN journalist_id IS NOT NULL THEN 1 ELSE 0 END) as tracked,
               COUNT(DISTINCT author_name) as unique_authors
        FROM discovered_urls GROUP BY outlet ORDER BY total DESC
    """).fetchall()

    log.info("=" * 70)
    log.info("OUTLET SUMMARY")
    log.info(f"{'Outlet':<20} {'Total':>10} {'Authors':>10} {'Tracked':>10} {'Unique':>8}")
    log.info("-" * 70)
    for r in rows:
        log.info(
            f"{r['outlet']:<20} {r['total']:>10,} {r['with_author']:>10,} "
            f"{r['tracked']:>10,} {r['unique_authors']:>8,}"
        )
    total = conn.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0]
    total_author = conn.execute("SELECT COUNT(*) FROM discovered_urls WHERE author_name IS NOT NULL").fetchone()[0]
    total_tracked = conn.execute("SELECT COUNT(*) FROM discovered_urls WHERE journalist_id IS NOT NULL").fetchone()[0]
    log.info("-" * 70)
    log.info(f"{'TOTAL':<20} {total:>10,} {total_author:>10,} {total_tracked:>10,}")

    log.info("")
    log.info("PER-JOURNALIST")
    j_rows = conn.execute("""
        SELECT j.name, j.outlet, COUNT(d.id) as urls
        FROM journalists j JOIN discovered_urls d ON d.journalist_id = j.id
        GROUP BY j.id ORDER BY urls DESC
    """).fetchall()
    for r in j_rows:
        log.info(f"  {r['name']} ({r['outlet']}): {r['urls']:,}")

    log.info("")
    log.info("TOP 25 UNTRACKED AUTHORS (candidates for adding)")
    top = conn.execute("""
        SELECT author_name, outlet, COUNT(*) as cnt
        FROM discovered_urls
        WHERE author_name IS NOT NULL AND journalist_id IS NULL
        AND author_name NOT IN ('RNZ News', 'Stuff reporters', 'Stuff', 'TVNZ',
                                '1News Reporters', 'Associated Press', 'AAP',
                                'NZ Herald', 'Newstalk ZB', 'Morningreport',
                                'Local Democracy Reporting')
        GROUP BY author_name
        ORDER BY cnt DESC LIMIT 25
    """).fetchall()
    for r in top:
        log.info(f"  {r['author_name']} ({r['outlet']}): {r['cnt']:,}")


async def main():
    conn = get_connection()
    init_db(conn)
    migrate_db(conn)
    lookup = _build_name_lookup(conn)
    log.info(f"Loaded {len(lookup)} name variants for {len(set(lookup.values()))} journalists")

    overall_start = datetime.now()

    # 1. Newstalk ZB — instant
    if not _shutdown:
        log.info("=" * 70)
        await tag_newstalkzb(conn, lookup)

    # 2. Stuff — JSON API
    if not _shutdown:
        log.info("=" * 70)
        await tag_stuff(conn, lookup, batch_size=50)

    # 3. 1News — small, HTML
    if not _shutdown:
        log.info("=" * 70)
        await process_outlet(conn, lookup, "1News", _html_get_author, concurrency=5, batch_size=20)

    # 4. The Spinoff — HTML, 32K
    if not _shutdown:
        log.info("=" * 70)
        await process_outlet(conn, lookup, "The Spinoff", _html_get_author, concurrency=8, batch_size=30)

    # 5. RNZ — HTML, political articles first, then rest
    if not _shutdown:
        log.info("=" * 70)
        await process_outlet(
            conn, lookup, "RNZ", _html_get_author,
            concurrency=10, batch_size=50,
            where_extra="AND url LIKE '%/news/political/%'",
        )

    # 6. NZ Herald — streaming 16KB, the big one
    if not _shutdown:
        log.info("=" * 70)
        await process_outlet(
            conn, lookup, "NZ Herald", _herald_get_author,
            concurrency=15, batch_size=100, checkpoint_every=20000,
        )

    # Final summary
    _print_summary(conn)

    elapsed = (datetime.now() - overall_start).total_seconds()
    log.info(f"\nTotal runtime: {elapsed/3600:.1f} hours")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
