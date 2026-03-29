"""NZ Herald adapter — sitemap-based discovery + Playwright extraction (React SPA).

Sitemap index (Arc Publishing):
  https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-index/?outputType=xml&_website=nzh
  - Daily sitemaps from Feb 2024 onward (~100-130K article URLs)
  - ~228 articles per daily sitemap

Paywalled articles are retried via archive.is.
"""

import re
import logging
from .base import SiteAdapter, Article

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

SITEMAP_INDEX_URL = "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-index/?outputType=xml&_website=nzh"
ARCHIVE_IS_URL = "https://archive.is/newest/{url}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=30)

# NZ Herald article URL pattern
ARTICLE_RE = re.compile(r'https://www\.nzherald\.co\.nz/[a-z-]+/[a-z0-9-]+/[A-Z0-9]+/')


class NZHeraldAdapter(SiteAdapter):
    name = "nzherald"
    domain = "nzherald.co.nz"
    needs_playwright = True

    _all_article_urls: list[str] | None = None

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        """Get article URLs from sitemaps. Falls back to Playwright author page scraping."""
        # Try sitemaps first (cached)
        if self._all_article_urls is None:
            self._all_article_urls = await self._load_sitemap_urls()

        urls = list(self._all_article_urls) if self._all_article_urls else []

        # Supplement with Playwright author page if available
        if author_slug and async_playwright:
            author_urls = await self._get_author_page_urls_playwright(author_slug)
            urls.extend(author_urls)

        # Deduplicate
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            clean = u.split("?")[0].split("#")[0]
            if clean not in seen and "nzherald.co.nz" in clean:
                seen.add(clean)
                deduped.append(clean)

        return deduped

    async def _load_sitemap_urls(self) -> list[str]:
        """Fetch Arc Publishing sitemap index and all daily sub-sitemaps."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(SITEMAP_INDEX_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"NZHerald sitemap index returned {resp.status}")
                        return []
                    xml = await resp.text()
            except Exception as e:
                log.warning(f"NZHerald sitemap index fetch failed: {e}")
                return []

            # Extract sub-sitemap URLs
            sub_sitemaps = re.findall(r'<loc>([^<]+)</loc>', xml)
            # Filter to only daily article sitemaps (skip video, google-news, etc.)
            daily_sitemaps = [u for u in sub_sitemaps if '/sitemap/' in u or '/sitemap2/' in u or '/sitemap3/' in u]
            log.info(f"NZHerald: found {len(daily_sitemaps)} daily sitemaps")

            all_urls: list[str] = []
            for sitemap_url in daily_sitemaps:
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

            log.info(f"NZHerald: {len(all_urls)} total article URLs from sitemaps")
            return all_urls

    async def _get_author_page_urls_playwright(self, author_slug: str) -> list[str]:
        """Scrape author page using Playwright (JS rendering required)."""
        author_url = f"https://www.nzherald.co.nz/author/{author_slug}/"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(author_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

                links = await page.eval_on_selector_all(
                    'a[href*="nzherald.co.nz"]',
                    """els => els.map(e => e.href).filter(h =>
                        !h.includes('/author/') &&
                        !h.includes('/section/') &&
                        h.split('/').length > 5
                    )"""
                )
            except Exception as e:
                log.error(f"[NZHerald] Failed to scrape author page {author_url}: {e}")
                links = []
            finally:
                await browser.close()

        return [u.split("?")[0].split("#")[0] for u in links]

    async def _fetch_from_archive(self, url: str) -> str | None:
        """Try to fetch a paywalled article from archive.is."""
        archive_url = ARCHIVE_IS_URL.format(url=url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(archive_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
                        if extracted and len(extracted) >= 200:
                            log.info(f"[NZHerald] archive.is hit for {url}")
                            return extracted
        except Exception as e:
            log.debug(f"[NZHerald] archive.is failed for {url}: {e}")
        return None

    async def extract_article(self, url: str) -> Article | None:
        """Use Playwright to render the JS SPA, then Trafilatura to extract text.
        Falls back to archive.is if the article is paywalled."""
        if not async_playwright:
            return None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                html = await page.content()
            except Exception as e:
                log.error(f"[NZHerald] Failed to load {url}: {e}")
                await browser.close()
                return None
            await browser.close()

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)

        # If paywalled (short or no text), try archive.is
        if not extracted or len(extracted) < 200:
            log.info(f"[NZHerald] Possible paywall, trying archive.is: {url}")
            extracted = await self._fetch_from_archive(url)

        if not extracted or len(extracted) < 200:
            return None

        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata else ""
        author = metadata.author if metadata else ""
        date = metadata.date if metadata else ""

        if not author:
            author_match = re.search(r'"author":\s*\{[^}]*"name":\s*"([^"]+)"', html)
            if author_match:
                author = author_match.group(1)

        return Article(
            url=url,
            title=title or "",
            author=author or "Unknown",
            publish_date=date or "",
            outlet="NZ Herald",
            text=extracted,
        )
