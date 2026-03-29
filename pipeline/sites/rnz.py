"""RNZ adapter — sitemap-based discovery with Trafilatura extraction.

Sitemap index: https://www.rnz.co.nz/sitemap/sitemap.xml.gz (gzipped)
  - 24 sub-sitemaps with ~1.16M URLs total (~410K news articles)
  - News articles: /news/{category}/{id}/{slug}

Falls back to author pages if sitemap fails.
"""

import gzip
import re
import logging
from io import BytesIO
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://www.rnz.co.nz"
SITEMAP_INDEX_URL = "https://www.rnz.co.nz/sitemap/sitemap.xml.gz"
AUTHOR_PAGE_URL = "https://www.rnz.co.nz/authors/{slug}"
POLITICS_RSS_URL = "https://www.rnz.co.nz/rss/political.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=30)

# Only fetch news article URLs from sitemaps (skip programme schedules, audio, etc.)
NEWS_URL_RE = re.compile(r'https://www\.rnz\.co\.nz/news/[a-z-]+/\d+/[a-z0-9-]+')


class RNZAdapter(SiteAdapter):
    name = "rnz"
    domain = "rnz.co.nz"
    needs_playwright = False

    _all_news_urls: list[str] | None = None

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        # Try sitemap-based discovery first
        if self._all_news_urls is None:
            self._all_news_urls = await self._load_sitemap_news_urls()

        urls = list(self._all_news_urls) if self._all_news_urls else []

        # If sitemap failed or for author-specific queries, also try author page
        if author_slug:
            author_urls = await self._get_author_page_urls(author_slug)
            urls.extend(author_urls)

        # Fallback to RSS if nothing else worked
        if not urls:
            urls = await self._get_rss_urls()

        # Deduplicate
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        return deduped

    async def _load_sitemap_news_urls(self) -> list[str]:
        """Fetch gzipped sitemap index and all sub-sitemaps, filtering for news URLs only."""
        async with aiohttp.ClientSession() as session:
            # Fetch the gzipped sitemap index
            try:
                async with session.get(SITEMAP_INDEX_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"RNZ sitemap index returned {resp.status}")
                        return []
                    data = await resp.read()
            except Exception as e:
                log.warning(f"RNZ sitemap index fetch failed: {e}")
                return []

            try:
                xml = gzip.decompress(data).decode("utf-8")
            except Exception as e:
                log.warning(f"RNZ sitemap index decompress failed: {e}")
                return []

            # Extract sub-sitemap URLs
            sub_sitemaps = re.findall(r'<loc>(https://www\.rnz\.co\.nz/sitemap/sitemap\d+\.xml\.gz)</loc>', xml)
            log.info(f"RNZ: found {len(sub_sitemaps)} sub-sitemaps")

            # Fetch each sub-sitemap (they're gzipped too)
            all_news_urls: list[str] = []
            for sitemap_url in sub_sitemaps:
                try:
                    async with session.get(sitemap_url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                except Exception:
                    continue

                try:
                    sub_xml = gzip.decompress(data).decode("utf-8")
                except Exception:
                    continue

                # Extract only news article URLs (skip programme schedules, audio, etc.)
                urls = re.findall(r'<loc>([^<]+)</loc>', sub_xml)
                news_urls = [u for u in urls if NEWS_URL_RE.match(u)]
                all_news_urls.extend(news_urls)
                log.debug(f"RNZ: {len(news_urls)} news URLs from {sitemap_url.split('/')[-1]}")

            log.info(f"RNZ: {len(all_news_urls)} total news URLs from sitemaps")
            return all_news_urls

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        author_url = AUTHOR_PAGE_URL.format(slug=author_slug)
        log.debug(f"RNZ: fetching author page {author_url}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
            except Exception:
                return []

        raw = re.findall(r'href="(/news/[a-z-]+/\d{5,}/[^"#?]+)"', html)
        raw += re.findall(r'href="(/national/\d{5,}/[^"#?]+)"', html)

        seen: set[str] = set()
        urls: list[str] = []
        for path in raw:
            full = BASE_URL + path
            if full not in seen:
                seen.add(full)
                urls.append(full)

        log.info(f"RNZ: found {len(urls)} URLs on author page for {author_slug}")
        return urls

    async def _get_rss_urls(self) -> list[str]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(POLITICS_RSS_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    xml = await resp.text()
            except Exception:
                return []
        return re.findall(r"<link>(https://www\.rnz\.co\.nz/news/[^<]+)</link>", xml)

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"RNZ: failed to fetch {url}: {e}")
                return None

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted:
            return None

        metadata = trafilatura.extract_metadata(html)
        author = metadata.author if metadata else ""
        if not author:
            return None

        return Article(
            url=url,
            title=metadata.title if metadata else "",
            author=author,
            publish_date=metadata.date if metadata else "",
            outlet="RNZ",
            text=extracted,
        )
