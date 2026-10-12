from __future__ import annotations

import logging
import random
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable
from urllib.parse import urljoin
from utility import headers, generate_random_email, _sanitize_doi, scihub_mirrors, human_delay
from altcha_solver import find_challenge_url, solve_gate
from browser_fetcher import (
    browser_available,
    browser_enabled,
    fetch_pdf_with_browser,
    headless_enabled,
)
import requests
from bs4 import BeautifulSoup
import pandas as pd



# --- base class with shared helpers (DRY) ---
@dataclass
class PDFDownloader:
    headers: dict
    download_dir: Path
    timeout: int = 15

    def _get(self, url: str, timeout: Optional[int] = None, **kwargs) -> Optional[requests.Response]:
        try:
            return requests.get(url, headers=self.headers, timeout=timeout or self.timeout, **kwargs)
        except requests.RequestException as e:
            logging.warning(f"GET failed: {url} ({e})")
            return None

    def _save_pdf(self, doi: str, content: bytes) -> Path:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        path = self.download_dir / f"{_sanitize_doi(doi)}.pdf"
        path.write_bytes(content)
        return path

    def _try_pdf(self, doi: str, url: str) -> Optional[Path]:
        """Download *url* and save it only if it is a real PDF."""
        pdf = self._get(url, timeout=30)
        if pdf and pdf.status_code == 200 and self._is_pdf(pdf.content):
            return self._save_pdf(doi, pdf.content)
        return None

    def _crawl_for_pdf(self, doi: str, page_url: str) -> Optional[Path]:
        """Fetch an HTML page and try the publisher PDF links found on it."""
        page = self._get(page_url)
        if not page or page.status_code != 200:
            return None
        soup = BeautifulSoup(page.content, "html.parser")

        # Publishers flag the canonical PDF with this meta tag.
        meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
        if meta and meta.get("content"):
            path = self._try_pdf(doi, urljoin(page_url, meta["content"]))
            if path:
                return path

        for a in soup.select("a[href]"):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                pdf_url = href if href.startswith("http") else urljoin(page_url, href)
                path = self._try_pdf(doi, pdf_url)
                if path:
                    return path
        return None

    @staticmethod
    def _is_pdf(content: bytes) -> bool:
        """Return True when *content* really starts with a PDF header.

        Publishers and APIs frequently answer with an HTML consent/error page,
        so a 200 response alone is not enough; rely on the ``%PDF`` magic bytes.
        """
        return bool(content) and b"%PDF" in content[:1024]

    def try_download(self, doi: str, **kwargs) -> Optional[Path]:
        # Accept extra kwargs to allow passing optional params (e.g., email) to specific strategies.
        raise NotImplementedError


# --- Unpaywall strategy ---
class UnpaywallDownloader(PDFDownloader):
    api = "https://api.unpaywall.org/v2/"

    def try_download(self, doi: str, email: Optional[str] = None, **kwargs) -> Optional[Path]:
        # Email is provided by the caller (PDFDownloadManager). Fall back to random generation if needed.
        if not email:
            email = generate_random_email()
        api_url = f"{self.api}{doi}?email={email}"
        logging.info(f"Unpaywall: querying API at {api_url}")
        resp = self._get(api_url)
        if not resp or resp.status_code != 200:
            status = resp.status_code if resp else "N/A"
            logging.warning(f"Unpaywall API request failed. Status: {status}")
            return None

        data = resp.json() or {}
        loc = data.get("best_oa_location") or {}
        pdf_url = loc.get("url_for_pdf")
        if not pdf_url:
            logging.info("Unpaywall: No direct OA PDF URL found.")
            return None

        pdf = self._get(pdf_url, timeout=30)
        if pdf and pdf.status_code == 200 and self._is_pdf(pdf.content):
            return self._save_pdf(doi, pdf.content)
        logging.info("Unpaywall: response was not a valid PDF.")
        return None


# --- OpenAlex strategy ---
class OpenAlexDownloader(PDFDownloader):
    """OpenAlex strategy: free, legal open-access locations.

    OpenAlex (https://openalex.org) is a fully open bibliographic database and
    exposes the same open-access locations Unpaywall uses, so it is tried right
    after Unpaywall and before the publisher link-crawler.
    """

    api = "https://api.openalex.org/works/doi:"

    def try_download(self, doi: str, email: Optional[str] = None, **kwargs) -> Optional[Path]:
        params = {"mailto": email} if email else None
        api_url = f"{self.api}{doi}"
        logging.info(f"OpenAlex: querying API for {doi}")
        resp = self._get(api_url, params=params)
        if not resp or resp.status_code != 200:
            status = resp.status_code if resp else "N/A"
            logging.warning(f"OpenAlex API request failed. Status: {status}")
            return None

        candidates = self._candidate_urls(resp.json() or {})
        if not candidates:
            logging.info("OpenAlex: no open-access locations found.")
            return None

        # Try direct PDF links first, then crawl landing pages. Cap the number of
        # pages fetched so one DOI cannot trigger a long chain of requests.
        for candidate in candidates[:8]:
            if candidate.lower().endswith(".pdf"):
                path = self._try_pdf(doi, candidate)
            else:
                path = self._crawl_for_pdf(doi, candidate)
            if path:
                return path
        return None

    @staticmethod
    def _candidate_urls(data: dict) -> list[str]:
        """Collect open-access URLs: direct PDFs first, then landing pages."""
        seen: set[str] = set()
        candidates: list[str] = []

        def add(url: Optional[str]) -> None:
            if url and url not in seen:
                seen.add(url)
                candidates.append(url)

        best = data.get("best_oa_location") or {}
        locations = data.get("locations") or []
        add(best.get("pdf_url"))
        for loc in locations:
            if loc.get("is_oa", True):
                add(loc.get("pdf_url"))
        add((data.get("open_access") or {}).get("oa_url"))
        add(best.get("landing_page_url"))
        for loc in locations:
            if loc.get("is_oa", True):
                add(loc.get("landing_page_url"))
        return candidates


# --- Crossref strategy ---
class CrossrefDownloader(PDFDownloader):
    api = "https://api.crossref.org/works/"

    def try_download(self, doi: str) -> Optional[Path]:
        logging.info(f"Crossref: querying API for {doi}")
        resp = self._get(f"{self.api}{doi}")
        if not resp or resp.status_code != 200:
            status = resp.status_code if resp else "N/A"
            logging.warning(f"Crossref API request failed. Status: {status}")
            return None

        links = (resp.json().get("message") or {}).get("link", []) or []

        # 1) direct PDF links (from metadata or URL)
        for link in links:
            url = link.get("URL")
            if not url:
                continue
            
            # Check for explicit PDF content-type or if URL ends with .pdf
            if link.get("content-type") == "application/pdf" or url.lower().endswith(".pdf"):
                path = self._try_pdf(doi, url)
                if path:
                    return path

        # 2) Handle specific publisher patterns (e.g., MDPI)
        for link in links:
            url = link.get("URL")
            if url and "mdpi.com" in url and "/htm" in url:
                # MDPI often provides a direct PDF link by replacing the /htm part with /pdf
                pdf_url = url.replace("/htm", "/pdf")
                path = self._try_pdf(doi, pdf_url)
                if path:
                    return path

        # 3) crawl linked pages for PDF links
        # Prioritize pages intended for reading or text mining
        crawl_links = [
            link for link in links if link.get("URL") and (
                link.get("content-type") == "text/html" or
                link.get("intended-application") == "text-mining"
            )
        ]
        # Add any remaining links that haven't been tried
        for link in links:
            if link not in crawl_links and link.get("URL"):
                crawl_links.append(link)

        for link in crawl_links:
            page_url = link.get("URL")
            if not page_url or page_url.lower().endswith(".pdf"):
                continue
            path = self._crawl_for_pdf(doi, page_url)
            if path:
                return path

        return None


# --- Sci-Hub strategy ---
def extract_pdf_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Extract the PDF URL from a Sci-Hub article page.

    Sci-Hub's markup has changed repeatedly, so several strategies are tried in
    order of reliability.  The current layout (sci-hub.st / .su / .ru) serves the
    PDF through an ``<object data=...>`` element and a ``div.download`` anchor,
    which earlier versions of this code missed.
    """
    pdf_url = None

    # Method 1: <object data="...pdf"> (current Sci-Hub layout)
    if not pdf_url:
        for obj in soup.find_all("object"):
            data = obj.get("data") or obj.get("src") or ""
            if data and ".pdf" in data.lower():
                pdf_url = data
                logging.info("Found PDF via <object data> tag")
                break

    # Method 2: the download button anchor
    if not pdf_url:
        download = soup.select_one("div.download a[href]")
        if download and ".pdf" in download["href"].lower():
            pdf_url = download["href"]
            logging.info("Found PDF via div.download anchor")

    # Method 3: embed tag with id='pdf'
    if not pdf_url:
        embed_tag = soup.find('embed', id='pdf')
        if embed_tag and embed_tag.get('src'):
            pdf_url = embed_tag.get('src')
            logging.info("Found PDF via embed#pdf tag")

    # Method 4: any embed tag with PDF src
    if not pdf_url:
        for embed in soup.find_all('embed'):
            src = embed.get('src', '')
            if src and ('.pdf' in src.lower() or '/pdf/' in src.lower() or '/downloads/' in src.lower()):
                pdf_url = src
                logging.info("Found PDF via embed tag with PDF src")
                break

    # Method 5: iframe (with or without id='pdf') pointing at a PDF
    if not pdf_url:
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if src and ('.pdf' in src.lower() or '/pdf/' in src.lower() or '/downloads/' in src.lower()):
                pdf_url = src
                logging.info("Found PDF via iframe tag")
                break

    # Method 6: direct PDF link in buttons or onclick handlers
    if not pdf_url:
        for button in soup.find_all('button'):
            onclick = button.get('onclick', '')
            match = re.search(r'location\.href\s*=\s*[\'"]([^\'"]+\.pdf[^\'"]*)[\'"]', onclick, re.IGNORECASE)
            if match:
                pdf_url = match.group(1)
                logging.info("Found PDF via button onclick")
                break

    # Method 7: anchor tags with a PDF href
    if not pdf_url:
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '.pdf' in href.lower() and ('sci-hub' in href.lower() or href.startswith('/') or href.startswith('//')):
                pdf_url = href
                logging.info("Found PDF via anchor tag")
                break

    # Method 8: any tag with a PDF-ish src attribute
    if not pdf_url:
        for tag in soup.find_all(src=True):
            src = tag['src']
            if '.pdf' in src.lower() or '/downloads/' in src.lower():
                pdf_url = src
                logging.info("Found PDF via %s src attribute", tag.name)
                break

    # Method 9: regex sweep over the raw page content
    if not pdf_url:
        page_text = str(soup)
        patterns = [
            r'"(/(?:storage|downloads|pdf)/[^"\s]+\.pdf[^"\s]*)"',
            r'(//[^"\s]+\.pdf(?:\?[^"\s]*)?)',
            r'(https?://[^"\s]+\.pdf(?:\?[^"\s]*)?)',
            r'(/downloads/[^"\s]+)',
            r'(/pdf/[^"\s]+\.pdf)',
        ]
        for pattern in patterns:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                pdf_url = match.group(1)
                logging.info("Found PDF via regex pattern in page content")
                break

    if pdf_url:
        if pdf_url.startswith('//'):
            pdf_url = 'https:' + pdf_url
        elif not pdf_url.startswith('http'):
            pdf_url = urljoin(base_url, pdf_url)
        return pdf_url

    return None


class SciHubDownloader(PDFDownloader):
    """Sci-Hub strategy: browserless requests first, headless browser as fallback.

    The page is fetched with ``requests`` and, when a mirror presents an Altcha
    proof-of-work gate, the challenge is solved directly (see
    :mod:`altcha_solver`).  Only if every mirror fails does the strategy fall
    back to a real headless browser driven by ``nodriver``.
    """

    def __init__(
        self,
        headers: dict,
        download_dir: Path,
        mirrors: list,
        use_browser: bool = True,
        headless: Optional[bool] = None,
    ):
        super().__init__(headers, download_dir)
        self.mirrors = mirrors
        self.use_browser = use_browser and browser_enabled()
        self.headless = headless_enabled() if headless is None else headless

    def _request_article(self, session: requests.Session, mirror: str, doi: str):
        """GET an article page, transparently solving an Altcha gate if present."""
        from urllib.parse import quote

        encoded_doi = quote(doi, safe='')
        for doi_variant in (doi, encoded_doi):
            scihub_url = mirror + doi_variant
            logging.info("Trying mirror: %s with DOI: %s", mirror, doi_variant)
            try:
                response = session.get(
                    scihub_url, headers=self.headers, timeout=20, allow_redirects=True
                )
            except requests.RequestException as e:
                logging.warning("Request to mirror %s failed: %s", mirror, e)
                continue

            if response.status_code >= 400:
                logging.warning(
                    "Mirror %s returned status %s", mirror, response.status_code
                )
                continue

            # A proof-of-work gate: solve it and re-request the article page.
            if find_challenge_url(response.text, response.url):
                logging.info("Altcha gate detected on %s; solving...", mirror)
                if solve_gate(
                    session, response.text, response.url, headers=self.headers, timeout=20
                ):
                    try:
                        response = session.get(
                            scihub_url,
                            headers=self.headers,
                            timeout=20,
                            allow_redirects=True,
                        )
                    except requests.RequestException as e:
                        logging.warning("Re-request to %s failed: %s", mirror, e)
                        continue
                else:
                    logging.warning("Could not solve Altcha gate on %s", mirror)
                    continue

            return response
        return None

    def _try_requests(self, doi: str, shuffled_mirrors: list) -> Optional[Path]:
        """Fast, browserless path: requests (+ Altcha solving) across all mirrors."""
        session = requests.Session()
        for index, mirror in enumerate(shuffled_mirrors):
            if index:
                human_delay(0.4, 1.2)
            response = self._request_article(session, mirror, doi)
            if response is None:
                continue

            content_type = response.headers.get('Content-Type', '').lower()
            if 'application/pdf' in content_type:
                logging.info("Direct PDF response from %s", mirror)
                if response.content and len(response.content) > 1000:
                    return self._save_pdf(doi, response.content)

            soup = BeautifulSoup(response.content, 'lxml')

            error_indicators = soup.find_all(
                string=re.compile(
                    r'article not found|not found in database|unavailable', re.IGNORECASE
                )
            )
            if error_indicators:
                logging.warning("Paper not found on %s", mirror)
                continue

            pdf_url = extract_pdf_url(soup, response.url)
            if not pdf_url:
                logging.warning("Could not find PDF URL on the page from %s", mirror)
                continue

            logging.info("Found PDF URL: %s", pdf_url)
            try:
                pdf = session.get(
                    pdf_url, headers=self.headers, timeout=30, allow_redirects=True
                )
            except requests.RequestException as e:
                logging.warning("Failed to download PDF: %s", e)
                continue

            if pdf and pdf.status_code == 200 and pdf.content:
                if pdf.content[:4] == b'%PDF' or len(pdf.content) > 10000:
                    return self._save_pdf(doi, pdf.content)
                logging.warning("Downloaded content doesn't appear to be a valid PDF")
            else:
                status = pdf.status_code if pdf else "N/A"
                logging.warning("Failed to download PDF from %s. Status: %s", pdf_url, status)

        return None

    def _try_browser(self, doi: str, shuffled_mirrors: list) -> Optional[Path]:
        """Fallback: drive a real headless browser to get past JS/bot gates."""
        if not self.use_browser:
            return None
        if not browser_available():
            logging.info("Browser fallback requested but nodriver/Chrome is unavailable.")
            return None

        filename = f"{_sanitize_doi(doi)}.pdf"
        logging.info("Sci-Hub: falling back to headless browser for %s", doi)
        return fetch_pdf_with_browser(
            doi,
            shuffled_mirrors,
            self.download_dir,
            filename,
            headless=self.headless,
            headers=self.headers,
        )

    def try_download(self, doi: str) -> Optional[Path]:
        logging.info("Sci-Hub: trying mirrors for %s", doi)
        shuffled_mirrors = self.mirrors.copy()
        random.shuffle(shuffled_mirrors)

        path = self._try_requests(doi, shuffled_mirrors)
        if path:
            return path

        path = self._try_browser(doi, shuffled_mirrors)
        if path:
            return path

        logging.error("Sci-Hub: all mirrors failed for %s", doi)
        return None


# --- manager that tries strategies in order ---
class PDFDownloadManager:
    def __init__(self, strategies: Iterable[PDFDownloader], download_dir: Path):
        self.strategies = list(strategies)
        self.download_dir = download_dir
        self.results = []

    def download(self, dois, progress: bool = True) -> Optional[object]:
        """
        Accept a single DOI (str) or an iterable/list of DOIs.
        Regenerates the email every 50 downloads and passes the email only to UnpaywallDownloader.
        Returns a tuple (doi, "success"|"fail") for a single DOI input, or a list of such tuples for many.

        With ``progress`` on, a running count (done / succeeded / failed /
        remaining) is logged before each DOI and a final report at the end.
        """
        # Normalize input to an iterable and detect single vs multiple
        single_input = isinstance(dois, str)
        if single_input:
            dois_iter = [dois]
        else:
            dois_iter = list(dois)

        results_list = []
        total = len(dois_iter)
        n_ok = n_fail = 0
        # initial email for the first batch
        current_email = generate_random_email()
        logging.info(f"Starting download for {len(dois_iter)} DOI(s). Initial email generated.")

        for idx, doi in enumerate(dois_iter):
            # Pause between downloads so a burst of DOIs looks less like a bot.
            if idx > 0:
                human_delay()

            # regenerate every 50 downloads (after the first batch)
            if idx > 0 and idx % 50 == 0:
                current_email = generate_random_email()
                logging.info(f"Regenerated email for batch starting at index {idx}.")

            if progress:
                logging.info(
                    f"[{idx + 1}/{total}] succeeded {n_ok} | failed {n_fail} | remaining {total - idx}"
                )
            logging.info(f"--- Starting download process for DOI: {doi} ---")
            doi_result = ("", "fail")
            for s in self.strategies:
                logging.info(f"Trying strategy: {s.__class__.__name__}")
                try:
                    # Strategies that benefit from a contact email.
                    if isinstance(s, (UnpaywallDownloader, OpenAlexDownloader)):
                        path = s.try_download(doi, email=current_email)
                    else:
                        path = s.try_download(doi)

                    if path:
                        logging.info(f"SUCCESS with {s.__class__.__name__}. Saved to: {path}")
                        self.results.append({"doi": doi, "success": True})
                        doi_result = (doi, "success")
                        break
                    else:
                        logging.warning(f"FAIL with {s.__class__.__name__}. PDF not found.")
                except Exception as e:
                    logging.error(f"ERROR during {s.__class__.__name__} strategy: {e}", exc_info=True)

            if doi_result[1] == "fail":
                logging.error(f"No PDF found for {doi} after all strategies.")
                self.results.append({"doi": doi, "success": False})
                n_fail += 1
            else:
                n_ok += 1

            results_list.append(doi_result)

        if progress and total:
            failed = [d for d, status in zip(dois_iter, results_list) if status[1] == "fail"]
            logging.info(
                f"Finished {total} DOI(s): succeeded {n_ok} ({n_ok / total:.1%}) | failed {n_fail}"
                + (f" -> {', '.join(failed)}" if failed else "")
            )

        # return single tuple for single input, else list
        return results_list[0] if single_input and results_list else results_list

    def save_results_to_csv(self):
        """Saves the download results to a CSV file in the download directory."""
        if not self.results:
            logging.warning("No results to save.")
            return

        results_df = pd.DataFrame(self.results)
        output_path = self.download_dir / "download_summary.csv"
        results_df.to_csv(output_path, index=False)
        logging.info(f"Download summary saved to {output_path}")


# --- example usage ---
if __name__ == "__main__":
    # Configure logging for detailed output
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    download_dir = Path("./fulldownloads")
    try:
        dois = pd.read_csv("sample_doi.csv")["doi"].tolist()
    except FileNotFoundError:
        logging.error("'sample_doi.csv' not found. Please create it with a 'doi' column.")
        dois = []

    strategies = [
        UnpaywallDownloader(headers=headers, download_dir=download_dir),
        OpenAlexDownloader(headers=headers, download_dir=download_dir),
        CrossrefDownloader(headers=headers, download_dir=download_dir),
        SciHubDownloader(headers=headers, download_dir=download_dir, mirrors=scihub_mirrors),
    ]
    manager = PDFDownloadManager(strategies=strategies, download_dir=download_dir)
    
    if dois:
        # single call handles list of DOIs and will regenerate email every 50 downloads
        manager.download(dois)
        manager.save_results_to_csv()
# --- end of file ---
