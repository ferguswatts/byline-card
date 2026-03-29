"""The Spinoff adapter — sitemap-based discovery with Trafilatura extraction.

Sitemap index: https://thespinoff.co.nz/sitemap.xml
  - 165 monthly post sitemaps: /api/sitemap/posts/YYYY-MM.xml (2014-2026)
  - Author sitemap: /api/sitemap/authors.xml
  - Category sitemap: /api/sitemap/categories.xml

Articles: /{category}/{DD-MM-YYYY}/{slug}
"""

import re
import logging
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://thespinoff.co.nz"
SITEMAP_INDEX_URL = "https://thespinoff.co.nz/sitemap.xml"
FEED_URL = "https://thespinoff.co.nz/feed"
AUTHOR_PAGE_URL = "https://thespinoff.co.nz/author/{slug}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=30)

# Article URL pattern: /{category}/{DD-MM-YYYY}/{slug}
ARTICLE_RE = re.compile(r'https?://thespinoff\.co\.nz/[a-z-]+/\d{2}-\d{2}-\d{4}/[a-z0-9-]+')


class SpinoffAdapter(SiteAdapter):
    name = "thespinoff"
    domain = "thespinoff.co.nz"
    needs_playwright = False

    _all_article_urls: list[str] | None = None

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        # Load all URLs from sitemaps (cached)
        if self._all_article_urls is None:
            self._all_article_urls = await self._load_sitemap_urls()

        # For author-specific queries, we can't filter by URL alone
        # (author not in URL). Return all and let pipeline filter by extracted author.
        # But first supplement with author page and feed for better coverage.
        urls = list(self._all_article_urls)

        if author_slug:
            # Also try Atom feed filtered by author
            feed_urls = await self._get_feed_urls_for_author(author_slug)
            urls.extend(feed_urls)

            # Also try author page
            page_urls = await self._get_author_page_urls(author_slug)
            urls.extend(page_urls)

        # Deduplicate
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        return deduped

    async def _load_sitemap_urls(self) -> list[str]:
        """Fetch sitemap index, then all monthly post sitemaps."""
        async with aiohttp.ClientSession() as session:
            # Get sitemap index
            try:
                async with session.get(SITEMAP_INDEX_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"Spinoff sitemap index returned {resp.status}")
                        return []
                    xml = await resp.text()
            except Exception as e:
                log.warning(f"Spinoff sitemap index fetch failed: {e}")
                return []

            # Extract monthly post sitemap URLs
            sub_sitemaps = re.findall(r'<loc>(https://thespinoff\.co\.nz/api/sitemap/posts/[^<]+)</loc>', xml)
            log.info(f"Spinoff: found {len(sub_sitemaps)} monthly post sitemaps")

            # Fetch each sub-sitemap and collect article URLs
            all_urls: list[str] = []
            for sitemap_url in sub_sitemaps:
                try:
                    async with session.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                        if resp.status != 200:
                            continue
                        sub_xml = await resp.text()
                except Exception:
                    continue

                urls = re.findall(r'<loc>([^<]+)</loc>', sub_xml)
                article_urls = [u for u in urls if ARTICLE_RE.match(u)]
                all_urls.extend(article_urls)

            log.info(f"Spinoff: {len(all_urls)} total article URLs from sitemaps")
            return all_urls

    async def _get_feed_urls_for_author(self, author_slug: str) -> list[str]:
        """Parse Atom feed and filter entries by author slug."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(FEED_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    xml = await resp.text()
            except Exception:
                return []

        urls: list[str] = []
        entries = re.findall(r'<entry>(.*?)</entry>', xml, re.DOTALL)
        for entry in entries:
            author_uris = re.findall(r'<uri>([^<]+)</uri>', entry)
            if any(author_slug in uri for uri in author_uris):
                links = re.findall(r'<link[^>]*href="([^"]+)"', entry)
                for link in links:
                    if ARTICLE_RE.match(link):
                        urls.append(link)

        return urls

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        author_url = AUTHOR_PAGE_URL.format(slug=author_slug)
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
            except Exception:
                return []

        return ARTICLE_RE.findall(html)

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"Spinoff: failed to fetch {url}: {e}")
                return None

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted:
            return None

        metadata = trafilatura.extract_metadata(html)
        author = metadata.author if metadata else ""
        if not author:
            # Try JSON-LD
            ld_match = re.search(r'"author":\s*\{[^}]*"name":\s*"([^"]+)"', html)
            if ld_match:
                author = ld_match.group(1)

        if not author:
            return None

        return Article(
            url=url,
            title=metadata.title if metadata else "",
            author=author,
            publish_date=metadata.date if metadata else "",
            outlet="The Spinoff",
            text=extracted,
        )
