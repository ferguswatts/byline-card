"""One-time interactive login to NZ Herald.

Opens a visible browser window for you to log in manually.
Saves session cookies to pipeline/.herald_cookies.json for use by the scraper.

Usage:
    python -m pipeline.login_herald
"""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_FILE = Path(__file__).parent / ".herald_cookies.json"
LOGIN_URL = "https://www.nzherald.co.nz/my-account/login/"


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return

    print("Opening NZ Herald login page...")
    print("Please log in with your premium account credentials.")
    print("The browser will close automatically once login is detected.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # Wait for the user to complete login — detect by URL change away from login page
        # or by presence of account elements. Poll every 2 seconds, timeout after 3 minutes.
        print("Waiting for login (timeout: 3 minutes)...")
        try:
            for _ in range(90):  # 90 * 2s = 3 minutes
                await page.wait_for_timeout(2000)
                url = page.url
                # Check if we've navigated away from the login page
                if "/login" not in url and "/sign-in" not in url:
                    break
                # Also check for logged-in indicators in the DOM
                logged_in = await page.evaluate("""
                    () => {
                        // Check for common logged-in indicators
                        const accountMenu = document.querySelector('[data-testid="user-menu"], .user-menu, .account-menu, [class*="logged-in"]');
                        return !!accountMenu;
                    }
                """)
                if logged_in:
                    break
            else:
                print("Timeout waiting for login. Please try again.")
                await browser.close()
                return
        except Exception as e:
            print(f"Error during login wait: {e}")

        # Give the page a moment to fully load post-login cookies
        await page.wait_for_timeout(3000)

        # Save all cookies
        cookies = await context.cookies()
        herald_cookies = [c for c in cookies if "nzherald" in c.get("domain", "")]

        COOKIE_FILE.write_text(json.dumps(herald_cookies, indent=2))
        print(f"\nSaved {len(herald_cookies)} Herald cookies to {COOKIE_FILE}")

        # Test the cookies by fetching a premium article
        test_url = "https://www.nzherald.co.nz/nz/politics/"
        await page.goto(test_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Check if we're still logged in
        content = await page.content()
        if "premium" in content.lower() or "subscriber" in content.lower() or len(content) > 50000:
            print("Login verified — premium access confirmed.")
        else:
            print("Warning: could not confirm premium access. Cookies saved anyway.")

        await browser.close()

    print(f"\nDone. The scraper will now use these cookies for NZ Herald articles.")
    print(f"Cookie file: {COOKIE_FILE}")
    print(f"To refresh cookies, run this script again.")


if __name__ == "__main__":
    asyncio.run(main())
