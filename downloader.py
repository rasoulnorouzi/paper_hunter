# %%
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from utility import _sanitize_doi
import random
from urllib.parse import urljoin
import logging
import numpy as np
import pandas as pd

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
