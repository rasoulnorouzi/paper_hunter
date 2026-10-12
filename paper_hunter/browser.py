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

import asyncio
import atexit
import base64
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .net import human_delay

logger = logging.getLogger(__name__)

# Browser args that make headless Chrome usable inside containers/CI as well.
# The security sandbox is controlled separately (see ``sandbox_enabled``) so it
# stays on for desktop users and can be turned off only where it is required.
_DEFAULT_BROWSER_ARGS = [
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

# CSS selector that matches the elements Sci-Hub uses to serve a PDF.  Used with
# ``Tab.wait_for`` so we wait on *real* content instead of a fixed sleep.
_PDF_HINT_SELECTOR = (
    'object[data*=".pdf"], object[src*=".pdf"], '
    'div.download a[href*=".pdf"], '
    'embed#pdf, embed[src*=".pdf"], '
    'iframe[src*=".pdf"]'
)


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment switch (any of ``0/false/no/off`` disables)."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


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


def _safe_name(value: str) -> str:
    """Turn a DOI or URL into a short, filesystem-safe label."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value))[:120]


class BrowserSession:
    """A reusable, lazily started nodriver browser living on its own thread."""

    _instance: "Optional[BrowserSession]" = None
    _lock = threading.Lock()

    def __init__(
        self,
        headless: bool = True,
        browser_args: Optional[list[str]] = None,
        browser_executable_path: Optional[str] = None,
        sandbox: bool = True,
        lang: str = "en-US",
        user_data_dir: Optional[str] = None,
        cookies_file: Optional[str] = None,
    ) -> None:
        self.headless = headless
        self.browser_args = list(browser_args or _DEFAULT_BROWSER_ARGS)
        self.browser_executable_path = browser_executable_path or find_chrome()
        self.sandbox = sandbox
        self.lang = lang
        self.user_data_dir = user_data_dir
        self.cookies_file = cookies_file

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
            sandbox=self.sandbox,
            lang=self.lang,
            user_data_dir=self.user_data_dir,
        )
        self._tab = await self._browser.get("about:blank")
        await self._load_cookies()
        logger.info(
            "nodriver: browser started (headless=%s, sandbox=%s, exe=%s)",
            self.headless,
            self.sandbox,
            self.browser_executable_path,
        )

    async def _load_cookies(self) -> None:
        """Restore a saved session cookie file (e.g. a library login)."""
        if not self.cookies_file:
            return
        path = Path(self.cookies_file)
        if not path.is_file():
            return
        try:
            await self._browser.cookies.load(str(path))
            logger.info("nodriver: loaded cookies from %s", path)
        except Exception as exc:  # noqa: BLE001 - a stale file must not break us
            logger.warning("nodriver: could not load cookies from %s (%s)", path, exc)

    async def _save_cookies(self) -> None:
        """Persist the session cookies so a login survives the next run."""
        if not self.cookies_file:
            return
        if self._browser is None:
            return
        path = Path(self.cookies_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await self._browser.cookies.save(str(path))
            logger.info("nodriver: saved cookies to %s", path)
        except Exception as exc:  # noqa: BLE001 - never let cleanup raise
            logger.warning("nodriver: could not save cookies to %s (%s)", path, exc)

    async def _find_pdf_in_dom(self, tab) -> Optional[str]:
        """Find a PDF link in the *rendered* DOM (covers JS-built pages).

        ``extract_pdf_url`` works on the raw HTML; pages that build their links
        with JavaScript need this second pass.  ``get_all_urls`` covers the
        usual ``a/link/img/script/meta`` tags, and a final ``evaluate`` sweep
        also checks ``object``/``embed``/``iframe`` sources.
        """
        base = getattr(tab.target, "url", "") or ""

        def _absolute(candidate: str) -> str:
            if candidate.startswith("//"):
                return "https:" + candidate
            return urljoin(base, candidate)

        try:
            for candidate in await tab.get_all_urls(absolute=True):
                if ".pdf" in candidate.lower():
                    logger.info("nodriver: PDF link found via get_all_urls")
                    return candidate
        except Exception:  # noqa: BLE001 - best effort
            pass

        try:
            raw = await tab.evaluate(
                "JSON.stringify(Array.from(document.querySelectorAll("
                "'a[href],link[href],iframe[src],embed[src],object[data]'))"
                ".map(e=>e.href||e.src||e.getAttribute('data')).filter(Boolean))",
                return_by_value=True,
            )
            if raw:
                for candidate in json.loads(raw):
                    if ".pdf" in str(candidate).lower():
                        logger.info("nodriver: PDF link found via DOM sweep")
                        return _absolute(str(candidate))
        except Exception:  # noqa: BLE001 - best effort
            pass

        return None

    async def _screenshot_failure(
        self, tab, screenshots_dir: Path, label: str
    ) -> None:
        """Capture the current page so a failed DOI can be inspected later."""
        try:
            screenshots_dir = Path(screenshots_dir)
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = screenshots_dir / f"{_safe_name(label)}_{stamp}.jpg"
            await tab.save_screenshot(path, format="jpeg")
            logger.info("nodriver: saved failure screenshot %s", path)
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            logger.debug("nodriver: could not save screenshot (%s)", exc)

    async def _resolve(
        self,
        url: str,
        wait: float,
        screenshots_dir: Optional[Path] = None,
        label: str = "",
    ):
        """Load the article page and return (pdf_url, user_agent, cookies)."""
        await self._start_browser()
        tab = self._tab
        await tab.get(url)

        # Wait for the page to settle into one of two states: a PDF is offered,
        # or an Altcha gate appears.  Waiting on real content is faster and
        # steadier than the fixed sleep this used to perform.
        try:
            await tab.wait_for(
                _PDF_HINT_SELECTOR + ", altcha-widget",
                timeout=wait,
            )
        except Exception:  # noqa: BLE001 - nothing matched; read what loaded
            pass

        # Best-effort: click an Altcha widget if a JS gate is present, then wait
        # for the PDF to appear.  The Python solver normally handles this, but a
        # real click helps when the challenge differs.
        try:
            widget = await tab.select("altcha-widget", timeout=1)
        except Exception:  # noqa: BLE001 - purely best effort
            widget = None
        if widget:
            try:
                await widget.click()
                await tab.wait_for(_PDF_HINT_SELECTOR, timeout=wait)
            except Exception:  # noqa: BLE001 - purely best effort
                pass

        html = await tab.get_content() or ""

        from .sources import extract_pdf_url  # local import avoids cycles

        soup = BeautifulSoup(html, "lxml")
        pdf_url = extract_pdf_url(soup, getattr(tab.target, "url", url) or url)

        if not pdf_url:
            pdf_url = await self._find_pdf_in_dom(tab)

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

        if not pdf_url and screenshots_dir is not None:
            await self._screenshot_failure(tab, screenshots_dir, label or url)

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

    async def _print_to_pdf(self, dest: Path) -> bool:
        """Last resort: render the current HTML page to a PDF via CDP.

        Only useful for HTML-only articles; gated behind ``print_to_pdf``.
        """
        try:
            from nodriver import cdp

            data, _stream = await self._tab.send(
                cdp.page.print_to_pdf(print_background=True)
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("nodriver: print_to_pdf failed (%s)", exc)
            return False
        if not data:
            return False
        try:
            pdf_bytes = base64.b64decode(data)
        except (ValueError, TypeError):
            return False
        if pdf_bytes[:4] != b"%PDF":
            return False
        dest.write_bytes(pdf_bytes)
        return True

    def fetch_pdf(
        self,
        url: str,
        dest: Path,
        wait: float = 4.0,
        download_timeout: float = 30.0,
        headers: Optional[dict] = None,
        screenshots_dir: Optional[Path] = None,
        print_to_pdf: bool = False,
        label: str = "",
    ) -> Optional[Path]:
        """Resolve the PDF behind ``url`` with the browser, then save it."""
        with self._api_locks:
            try:
                pdf_url, user_agent, cookies = self.run(
                    self._resolve(url, wait, screenshots_dir, label),
                    timeout=wait + 60,
                )
            except Exception as exc:  # noqa: BLE001
                # Transient failure: keep the session alive so the next DOI can
                # retry (only a missing nodriver/Chrome disables it permanently).
                logger.warning("nodriver: browser fetch failed for %s (%s)", url, exc)
                return None

            if not pdf_url:
                if print_to_pdf:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if self.run(self._print_to_pdf(dest), timeout=60):
                            logger.info("nodriver: saved HTML article as PDF: %s", dest)
                            return dest
                    except Exception as exc:  # noqa: BLE001
                        logger.info("nodriver: print_to_pdf errored (%s)", exc)
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

            if not ok and screenshots_dir is not None:
                try:
                    self.run(
                        self._screenshot_failure(self._tab, screenshots_dir, label or url),
                        timeout=30,
                    )
                except Exception:  # noqa: BLE001
                    pass

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
                await self._save_cookies()
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


def sandbox_enabled() -> bool:
    """Honour the ``PAPER_HUNTER_SANDBOX`` switch (default: on).

    Chrome's security sandbox stays on for normal desktop use; set this to
    ``0`` only where Chrome cannot start with it (e.g. some containers).
    nodriver also disables it automatically when running as root.
    """
    return os.getenv("PAPER_HUNTER_SANDBOX", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def profile_dir() -> Optional[str]:
    """Persistent Chrome profile directory, or ``None`` for a throwaway one.

    Set ``PAPER_HUNTER_PROFILE_DIR`` to a folder to keep the browser profile on
    disk so a library sign-in survives between runs.
    """
    value = os.getenv("PAPER_HUNTER_PROFILE_DIR", "").strip()
    return value or None


def cookies_file() -> Optional[str]:
    """Path for a saved-cookies file, or ``None`` to disable persistence."""
    value = os.getenv("PAPER_HUNTER_COOKIES_FILE", "").strip()
    return value or None


def screenshots_enabled() -> bool:
    """Whether failed pages are captured to ``debug_screenshots`` (default: on)."""
    return _env_flag("PAPER_HUNTER_SAVE_SCREENSHOTS", default=True)


def print_to_pdf_enabled() -> bool:
    """Whether HTML-only pages are rendered to PDF as a last resort (default: off)."""
    return _env_flag("PAPER_HUNTER_PRINT_TO_PDF", default=False)


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
        headless=headless_enabled() if headless is None else headless,
        sandbox=sandbox_enabled(),
        lang="en-US",
        user_data_dir=profile_dir(),
        cookies_file=cookies_file(),
    )
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    dest = download_dir / filename

    screenshots_dir = download_dir / "debug_screenshots" if screenshots_enabled() else None

    for index, mirror in enumerate(mirrors):
        if index:
            human_delay(0.5, 1.5)
        url = mirror + doi
        logger.info("nodriver: trying %s", url)
        result = session.fetch_pdf(
            url,
            dest,
            headers=headers,
            screenshots_dir=screenshots_dir,
            print_to_pdf=print_to_pdf_enabled(),
            label=doi,
        )
        if result is not None:
            logger.info("nodriver: success for %s -> %s", doi, result)
            return result
    return None
