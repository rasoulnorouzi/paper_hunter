"""Headless-browser fallback for Sci-Hub, powered by ``nodriver``.

The downloader first tries the fast, browserless path (``requests`` + the Altcha
proof-of-work solver in :mod:`paper_hunter.altcha`).  Only when that fails do we fall
back to a real Chrome instance driven by `nodriver`, the successor of
undetected-chromedriver.  A real browser defeats JavaScript challenges, Cloudflare
interstitials and other bot gates that plain HTTP cannot.

Design notes
------------
* ``nodriver`` is **optional**.  If it (or Chrome) is missing, this module degrades
  gracefully and the rest of the app keeps working.
* Browsers are expensive to start, so a single :class:`BrowserSession` is kept
  alive in a dedicated background thread with its own asyncio event loop and
  reused for every DOI in the run.
* ``nodriver`` is fully asynchronous but the rest of the project is synchronous,
  hence the thread + ``run_coroutine_threadsafe`` bridge.
* The browser is used to *resolve* the PDF URL (and to get past the gate); the
  bytes are then fetched with ``requests`` using the browser's cookies.  This is
  more reliable than Chrome's own download path, which uses a cross-origin
  ``fetch`` that is blocked by CORS on some Sci-Hub CDN hosts.
"""

from __future__ import annotations

import atexit
import asyncio
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .net import human_delay

logger = logging.getLogger(__name__)

# Browser args that make headless Chrome usable inside containers/CI as well.
_DEFAULT_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
]

_CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
)


def _import_nodriver():
    try:
        import nodriver  # noqa: F401

        return nodriver
    except Exception as exc:  # pragma: no cover - depends on environment
        logger.info("nodriver is not available (%s); browser fallback disabled.", exc)
        return None


def find_chrome() -> Optional[str]:
    """Locate a Chromium-based executable, or ``None`` if there is none."""
    for name in _CHROME_NAMES:
        path = shutil.which(name)
        if path:
            return path
    candidates = [
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    # Windows per-user installs.
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        for rel in (
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
        ):
            candidate = Path(local_appdata) / rel
            if candidate.exists():
                return str(candidate)
    return None


def browser_available() -> bool:
    """Whether the browser fallback can actually run here."""
    return _import_nodriver() is not None and find_chrome() is not None


class BrowserSession:
    """A reusable, lazily started nodriver browser living on its own thread."""

    _instance: "Optional[BrowserSession]" = None
    _lock = threading.Lock()

    def __init__(
        self,
        headless: bool = True,
        browser_args: Optional[list[str]] = None,
        browser_executable_path: Optional[str] = None,
    ) -> None:
        self.headless = headless
        self.browser_args = list(browser_args or _DEFAULT_BROWSER_ARGS)
        self.browser_executable_path = browser_executable_path or find_chrome()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._browser = None
        self._tab = None
        self._failed = False
        self._api_locks = threading.Lock()

    # -- singleton access ---------------------------------------------------
    @classmethod
    def get(cls, **kwargs) -> "BrowserSession":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance = None

    # -- thread / loop plumbing --------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="nodriver-loop", daemon=True
        )
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro, timeout: float = 120):
        """Run a coroutine on the browser thread and return its result."""
        if self._failed:
            # Close the never-started coroutine to avoid an un-awaited warning.
            try:
                coro.close()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("browser session is unavailable")
        self._ensure_thread()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _start_browser(self):
        if self._browser is not None:
            return
        nodriver = _import_nodriver()
        if nodriver is None:
            self._failed = True
            raise RuntimeError("nodriver is not installed")
        self._browser = await nodriver.start(
            headless=self.headless,
            browser_args=self.browser_args,
            browser_executable_path=self.browser_executable_path,
        )
        self._tab = await self._browser.get("about:blank")
        logger.info(
            "nodriver: browser started (headless=%s, exe=%s)",
            self.headless,
            self.browser_executable_path,
        )

    async def _resolve(self, url: str, wait: float):
        """Load the article page and return (pdf_url, user_agent, cookies)."""
        await self._start_browser()
        tab = self._tab
        await tab.get(url)
        await tab.sleep(wait)

        # Best-effort: click an Altcha widget if a JS gate is present.  The
        # Python solver normally handles this, but a real click helps when the
        # challenge differs.
        try:
            widget = await tab.select("altcha-widget", timeout=1)
            if widget:
                await widget.click()
                await tab.sleep(3)
        except Exception:  # noqa: BLE001 - purely best effort
            pass

        html = await tab.get_content() or ""

        from .sources import extract_pdf_url  # local import avoids cycles

        soup = BeautifulSoup(html, "lxml")
        pdf_url = extract_pdf_url(soup, getattr(tab.target, "url", url) or url)

        user_agent = None
        try:
            user_agent = await tab.evaluate("navigator.userAgent", return_by_value=True)
        except Exception:  # noqa: BLE001
            user_agent = None

        cookies: dict[str, str] = {}
        try:
            from nodriver import cdp

            for cookie in await tab.send(cdp.network.get_all_cookies()):
                cookies[cookie.name] = cookie.value
        except Exception:  # noqa: BLE001
            cookies = {}

        return pdf_url, user_agent, cookies

    async def _browser_download(self, pdf_url: str, dest: Path) -> bool:
        """Secondary path: let Chrome download the file itself."""
        tab = self._tab
        await tab.set_download_path(dest.parent)
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        await tab.download_file(pdf_url, dest.name)

        waited = 0.0
        while waited < 30:
            if dest.exists() and dest.stat().st_size > 1000:
                with open(dest, "rb") as fh:
                    if fh.read(4) == b"%PDF":
                        return True
            await tab.sleep(0.5)
            waited += 0.5
        return False

    def fetch_pdf(
        self,
        url: str,
        dest: Path,
        wait: float = 4.0,
        download_timeout: float = 30.0,
        headers: Optional[dict] = None,
    ) -> Optional[Path]:
        """Resolve the PDF behind ``url`` with the browser, then save it."""
        with self._api_locks:
            try:
                pdf_url, user_agent, cookies = self.run(
                    self._resolve(url, wait), timeout=wait + 60
                )
            except Exception as exc:  # noqa: BLE001
                # Transient failure: keep the session alive so the next DOI can
                # retry (only a missing nodriver/Chrome disables it permanently).
                logger.warning("nodriver: browser fetch failed for %s (%s)", url, exc)
                return None

            if not pdf_url:
                return None

            # Primary: download the bytes with requests, reusing browser cookies.
            hdrs = dict(headers or {})
            if user_agent:
                hdrs["User-Agent"] = user_agent
            hdrs.setdefault("Referer", url)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                resp = requests.get(
                    pdf_url,
                    headers=hdrs,
                    cookies=cookies,
                    timeout=download_timeout,
                    allow_redirects=True,
                )
                if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                    dest.write_bytes(resp.content)
                    return dest
                logger.info(
                    "nodriver: requests download of %s failed (status %s); "
                    "trying in-browser download",
                    pdf_url,
                    resp.status_code,
                )
            except requests.RequestException as exc:
                logger.info("nodriver: requests download errored (%s)", exc)

            # Secondary: let Chrome download it (works for same-origin PDFs).
            try:
                ok = self.run(self._browser_download(pdf_url, dest), timeout=60)
            except Exception as exc:  # noqa: BLE001
                logger.info("nodriver: in-browser download errored (%s)", exc)
                ok = False
            return dest if ok else None

    def stop(self) -> None:
        try:
            if self._loop is not None and self._browser is not None:
                asyncio.run_coroutine_threadsafe(self._stop_browser(), self._loop).result(20)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._tab = None

    async def _stop_browser(self) -> None:
        try:
            if self._browser is not None:
                self._browser.stop()
        finally:
            self._browser = None
            self._tab = None


atexit.register(BrowserSession.shutdown)


def browser_enabled() -> bool:
    """Honour the ``PAPER_HUNTER_BROWSER`` environment switch (default: on)."""
    return os.getenv("PAPER_HUNTER_BROWSER", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def headless_enabled() -> bool:
    """Honour the ``PAPER_HUNTER_HEADLESS`` environment switch (default: on)."""
    return os.getenv("PAPER_HUNTER_HEADLESS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def fetch_pdf_with_browser(
    doi: str,
    mirrors: list[str],
    download_dir: Path,
    filename: str,
    headless: Optional[bool] = None,
    headers: Optional[dict] = None,
) -> Optional[Path]:
    """Try each mirror through a headless nodriver browser until one yields a PDF.

    Returns the path to a saved PDF, or ``None`` when the browser path also
    fails (or the browser is unavailable).
    """
    if not browser_available():
        logger.info("nodriver/Chrome unavailable; skipping browser fallback.")
        return None

    session = BrowserSession.get(
        headless=headless_enabled() if headless is None else headless
    )
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    dest = download_dir / filename

    for index, mirror in enumerate(mirrors):
        if index:
            human_delay(0.5, 1.5)
        url = mirror + doi
        logger.info("nodriver: trying %s", url)
        result = session.fetch_pdf(url, dest, headers=headers)
        if result is not None:
            logger.info("nodriver: success for %s -> %s", doi, result)
            return result
    return None
