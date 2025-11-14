# %%
import requests
from pathlib import Path
# Ensure BeautifulSoup is available; attempt to install if missing.
try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:
    import sys, subprocess
    print("Missing dependency 'bs4' (beautifulsoup4). Attempting to install via pip...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4", "lxml"])
    from bs4 import BeautifulSoup
from utility import _sanitize_doi
import random
from urllib.parse import urljoin
import logging
import numpy as np
import pandas as pd
from plugins_class import (
    UnpaywallDownloader,
    CrossrefDownloader,
    SciHubDownloader,
    PDFDownloadManager,
)
from utility import headers as _global_headers, scihub_mirrors as _global_mirrors

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# %%

def download_pdf_from_doi(doi: str, mirrors: list, headers: dict) -> tuple[str, str]:
    """
    Tries to download a PDF for a given DOI from a list of Sci-Hub mirrors.
    It shuffles the mirrors and tries them one by one until it succeeds.
    Returns a tuple of (doi, status) where status is 'success' or 'fail'.
    """
    shuffled_mirrors = mirrors.copy()
    random.shuffle(shuffled_mirrors)
    
    for mirror in shuffled_mirrors:
        try:
            scihub_url = mirror + doi
            logging.info(f"Trying mirror: {mirror} for DOI: {doi}")
            response = requests.get(scihub_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'lxml')
                embed_tag = soup.find('embed', id='pdf')
                
                if embed_tag and embed_tag.get('src'):
                    pdf_url = embed_tag.get('src')
                    
                    if pdf_url.startswith('//'):
                        pdf_url = 'https:' + pdf_url
                    elif not pdf_url.startswith('http'):
                        pdf_url = urljoin(response.url, pdf_url)

                    logging.info(f"Found PDF URL: {pdf_url}")
                    pdf_response = requests.get(pdf_url, headers=headers, timeout=30)

                    if pdf_response.status_code == 200 and pdf_response.content:
                        download_path = Path("scihub_downloads")
                        download_path.mkdir(parents=True, exist_ok=True)
                        file_path = download_path / (_sanitize_doi(doi) + ".pdf")
                        file_path.write_bytes(pdf_response.content)
                        logging.info(f"Successfully saved PDF to {file_path}")
                        return doi, "success"
                    else:
                        logging.warning(f"Failed to download PDF from {pdf_url}. Status: {pdf_response.status_code}")
                else:
                    logging.warning("Could not find embed tag with PDF source on the page.")
            else:
                logging.warning(f"Failed to get a 200 response from {scihub_url}. Status: {response.status_code}")

        except requests.exceptions.RequestException as e:
            logging.error(f"Request failed for mirror {mirror}: {e}")
            continue # Try next mirror
            
    logging.error(f"All mirrors failed for DOI: {doi}")
    return doi, "fail"


def run_bulk_download(
    dois: list[str] | str,
    download_dir: str | Path = "fulldownloads",
    headers: dict | None = None,
    mirrors: list[str] | None = None,
) -> pd.DataFrame:
    """High-level helper wrapping the plugin-based PDFDownloadManager.

    Keeps notebook usage minimal by:
    - Accepting a list or single DOI
    - Constructing strategy instances (Unpaywall, Crossref, Sci-Hub)
    - Running manager.download and returning a DataFrame of results
    - Persisting CSV summary in the target directory

    Parameters
    ----------
    dois : list[str] | str
        DOIs to attempt downloading.
    download_dir : str | Path
        Output directory for PDFs and summary CSV.
    headers : dict | None
        Optional override for HTTP headers (defaults to utility.headers).
    mirrors : list[str] | None
        Optional override for Sci-Hub mirrors (defaults to utility.scihub_mirrors).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: doi, success (bool).
    """
    if isinstance(dois, str):
        dois_list = [dois]
    else:
        dois_list = list(dois)

    headers = headers or _global_headers
    mirrors = mirrors or _global_mirrors
    download_path = Path(download_dir)

    strategies = [
        UnpaywallDownloader(headers=headers, download_dir=download_path),
        CrossrefDownloader(headers=headers, download_dir=download_path),
        SciHubDownloader(headers=headers, download_dir=download_path, mirrors=mirrors),
    ]
    manager = PDFDownloadManager(strategies=strategies, download_dir=download_path)
    manager.download(dois_list)
    manager.save_results_to_csv()
    return pd.DataFrame(manager.results)


def zip_downloads(download_dir: str | Path = "fulldownloads", zip_name: str = "papers_zip") -> Path:
    """Create a zip archive of all PDFs in the download directory.

    Parameters
    ----------
    download_dir : str | Path
        Directory containing downloaded PDF files.
    zip_name : str
        Base name (without .zip) for the archive.

    Returns
    -------
    Path
        Path to the created zip file.
    """
    from zipfile import ZipFile

    download_path = Path(download_dir)
    if not download_path.exists():
        raise FileNotFoundError(f"Download directory not found: {download_path}")

    zip_path = download_path.parent / f"{zip_name}.zip"
    with ZipFile(zip_path, "w") as zf:
        for pdf in download_path.glob("*.pdf"):
            zf.write(pdf, pdf.name)

        # include summary CSV if present
        summary_csv = download_path / "download_summary.csv"
        if summary_csv.exists():
            zf.write(summary_csv, summary_csv.name)

    logging.info(f"Created zip archive at {zip_path}")
    return zip_path

if __name__ == '__main__':
    # List of Sci-Hub mirrors
    scihub_mirrors = [
        "https://sci-hub.se/",
        "https://sci-hub.st/",
        "https://sci-hub.red/",
        "https://sci-hub.box/",
        "https://sci-hub.ru/",
    ]

    test_dois = pd.read_csv("sample_doi.csv")["doi"].tolist()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    successful_dois = []
    failed_dois = []
    for test_doi in test_dois:
        doi, status = download_pdf_from_doi(test_doi, scihub_mirrors, headers)
        logging.info(f"Final status for DOI {doi}: {status}")
        print(f"******** Finished processing DOI: {doi} with status: {status} ********\n")
        if status == "fail":
            failed_dois.append(doi)
        else:
            successful_dois.append(doi)
        
    print(f"Successfully rate of downloads: {len(successful_dois)}/{len(test_dois)}")
    print(f"Failed DOIs rate: {len(failed_dois)}/{len(test_dois)}")
