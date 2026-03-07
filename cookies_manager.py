"""
Headless browser session for Google/YouTube login and cookie export.
Uses Playwright with stealth flags to avoid bot detection.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

COOKIES_FILE = os.environ.get("COOKIES_FILE", "/cookies/cookies.txt")

# Injected into every page before any script runs.
# Hides the signals Google uses to detect headless/automated browsers.
_STEALTH_JS = """
(() => {
  // 1. Hide webdriver flag
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // 2. Fake plugins (empty in headless)
  Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
  });

  // 3. Fake languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['ru-RU', 'ru', 'en-US', 'en'],
  });

  // 4. Restore window.chrome that headless removes
  window.chrome = { runtime: {} };

  // 5. Fix Notification.permission (headless returns 'denied' by default)
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(params);
})();
"""


def _to_netscape(cookies: list[dict]) -> str:
    lines = ["# Netscape HTTP Cookie File\n"]
    for c in cookies:
        domain = c["domain"]
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires") or 0)
        if expires < 0:
            expires = 0
        lines.append(
            f"{domain}\t{flag}\t{c.get('path', '/')}\t"
            f"{secure}\t{expires}\t{c['name']}\t{c['value']}\n"
        )
    return "".join(lines)


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-extensions",
                "--disable-plugins-discovery",
                "--window-size=1280,800",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        await self._context.add_init_script(_STEALTH_JS)
        self.page = await self._context.new_page()

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await self._settle()

    async def type_text(self, text: str) -> None:
        await self.page.keyboard.type(text, delay=60)

    async def press(self, key: str) -> None:
        await self.page.keyboard.press(key)
        await self._settle()

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(type="jpeg", quality=80)

    @property
    def url(self) -> str:
        return self.page.url

    async def save_cookies(self, path: str = COOKIES_FILE) -> int:
        cookies = await self._context.cookies()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(_to_netscape(cookies))
        logger.info("Saved %d cookies to %s", len(cookies), path)
        return len(cookies)

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None
        self.page = None

    async def _settle(self) -> None:
        """Wait for network to quiet down after navigation/input."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass  # Timeout is fine — just continue
