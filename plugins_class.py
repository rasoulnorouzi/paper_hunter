from __future__ import annotations

import logging
import random

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable, Tuple
from urllib.parse import urljoin
from utility import headers, generate_random_email, _sanitize_doi, scihub_mirrors
import requests
from bs4 import BeautifulSoup
import pandas as pd



# --- base class with shared helpers (DRY) ---
@dataclass
class PDFDownloader:
    headers: dict
    download_dir: Path
    timeout: int = 15

    def _get(self, url: str, timeout: Optional[int] = None) -> Optional[requests.Response]:
        try:
            return requests.get(url, headers=self.headers, timeout=timeout or self.timeout)
        except requests.RequestException as e:
            logging.warning(f"GET failed: {url} ({e})")
            return None

    def _save_pdf(self, doi: str, content: bytes) -> Path:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        path = self.download_dir / f"{_sanitize_doi(doi)}.pdf"
        path.write_bytes(content)
        return path

    def try_download(self, doi: str) -> Optional[Path]:
        raise NotImplementedError


# --- Unpaywall strategy ---
class UnpaywallDownloader(PDFDownloader):
    api = "https://api.unpaywall.org/v2/"

    def try_download(self, doi: str) -> Optional[Path]:
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
        if pdf and pdf.status_code == 200 and pdf.content:
            return self._save_pdf(doi, pdf.content)
        return None


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

        # 3) crawl linked pages for .pdf anchors
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
            if not page_url:
                continue
            
            # Avoid re-downloading a URL if it was already tried as a direct PDF
            if page_url.lower().endswith(".pdf"):
                continue

            page = self._get(page_url)
            if not page or page.status_code != 200:
                continue
            soup = BeautifulSoup(page.content, "html.parser")
            for a in soup.select('a[href]'):
                href = a["href"]
                if href.lower().endswith(".pdf"):
                    pdf_url = href if href.startswith("http") else urljoin(page_url, href)
                    path = self._try_pdf(doi, pdf_url)
                    if path:
                        return path

        return None

    def _try_pdf(self, doi: str, url: str) -> Optional[Path]:
        pdf = self._get(url, timeout=30)
        if pdf and pdf.status_code == 200 and pdf.content:
            return self._save_pdf(doi, pdf.content)
        return None


# --- Sci-Hub strategy ---
class SciHubDownloader(PDFDownloader):
    def __init__(self, headers: dict, download_dir: Path, mirrors: list):
        super().__init__(headers, download_dir)
        self.mirrors = mirrors

    def try_download(self, doi: str) -> Optional[Path]:
        logging.info(f"Sci-Hub: trying mirrors for {doi}")
        shuffled_mirrors = self.mirrors.copy()
        random.shuffle(shuffled_mirrors)

        for mirror in shuffled_mirrors:
            scihub_url = mirror + doi
            logging.info(f"Trying mirror: {mirror}")
            
            try:
                response = requests.get(scihub_url, headers=self.headers, timeout=15)
                response.raise_for_status()
            except requests.RequestException as e:
                logging.warning(f"Request to mirror {mirror} failed: {e}")
                continue

            soup = BeautifulSoup(response.content, 'lxml')
            embed_tag = soup.find('embed', id='pdf')
            
            if embed_tag and embed_tag.get('src'):
                pdf_url = embed_tag.get('src')
                
                if pdf_url.startswith('//'):
                    pdf_url = 'https:' + pdf_url
                elif not pdf_url.startswith('http'):
                    pdf_url = urljoin(response.url, pdf_url)

                logging.info(f"Found PDF URL: {pdf_url}")
                pdf = self._get(pdf_url, timeout=30)

                if pdf and pdf.status_code == 200 and pdf.content:
                    return self._save_pdf(doi, pdf.content)
                else:
                    status = pdf.status_code if pdf else "N/A"
                    logging.warning(f"Failed to download PDF from {pdf_url}. Status: {status}")
            else:
                logging.warning("Could not find embed tag with PDF source on the page.")
        
        return None


# --- manager that tries strategies in order ---
class PDFDownloadManager:
    def __init__(self, strategies: Iterable[PDFDownloader], download_dir: Path):
        self.strategies = list(strategies)
        self.download_dir = download_dir
        self.results = []

    def download(self, doi: str) -> Tuple[str, str]:
        logging.info(f"--- Starting download process for DOI: {doi} ---")
        for s in self.strategies:
            logging.info(f"Trying strategy: {s.__class__.__name__}")
            try:
                path = s.try_download(doi)
                if path:
                    logging.info(f"SUCCESS with {s.__class__.__name__}. Saved to: {path}")
                    self.results.append({"doi": doi, "success": True})
                    return doi, "success"
                else:
                    logging.warning(f"FAIL with {s.__class__.__name__}. PDF not found.")
            except Exception as e:
                logging.error(f"ERROR during {s.__class__.__name__} strategy: {e}", exc_info=True)

        logging.error(f"No PDF found for {doi} after all strategies.")
        self.results.append({"doi": doi, "success": False})
        return doi, "fail"

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
        CrossrefDownloader(headers=headers, download_dir=download_dir),
        SciHubDownloader(headers=headers, download_dir=download_dir, mirrors=scihub_mirrors),
    ]
    manager = PDFDownloadManager(strategies=strategies, download_dir=download_dir)
    
    if dois:
        for doi in dois:
            manager.download(doi)
        manager.save_results_to_csv()
# --- end of file ---
