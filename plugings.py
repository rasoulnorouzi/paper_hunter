import requests
from pathlib import Path
from bs4 import BeautifulSoup
from utility import _sanitize_doi, generate_random_email, headers, scihub_mirrors
import random
from urllib.parse import urljoin
import logging



logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



def scihub_download(doi: str, mirrors: list, headers: dict, download_dir: str) -> tuple[str, str]:
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
                        download_path = Path(download_dir)
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

# unpaywall_download function 

def unpaywall_download(doi: str, headers: dict, download_dir: str) -> tuple[str, str]:
    """
    Tries to download a PDF for a given DOI using Unpaywall API.
    Returns a tuple of (doi, status) where status is 'success' or 'fail'.
    """
    user_mail = generate_random_email()
    base_url = "https://api.unpaywall.org/v2/"
    api_url = f"{base_url}{doi}?email={user_mail}"

    try:
        logging.info(f"Querying Unpaywall for DOI: {doi} with email: {user_mail}")
        response = requests.get(api_url, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()
            if 'best_oa_location' in data and data['best_oa_location'] and 'url_for_pdf' in data['best_oa_location']:
                pdf_url = data['best_oa_location']['url_for_pdf']
                if pdf_url:
                    logging.info(f"Found PDF URL on Unpaywall: {pdf_url}")
                    pdf_response = requests.get(pdf_url, headers=headers, timeout=30)

                    if pdf_response.status_code == 200 and pdf_response.content:
                        download_path = Path(download_dir)
                        download_path.mkdir(parents=True, exist_ok=True)
                        file_path = download_path / (_sanitize_doi(doi) + ".pdf")
                        file_path.write_bytes(pdf_response.content)
                        logging.info(f"Successfully saved PDF to {file_path}")
                        return doi, "success"
                    else:
                        logging.warning(f"Failed to download PDF from {pdf_url}. Status: {pdf_response.status_code}")
                else:
                    logging.warning("No PDF URL found in best_oa_location.")
            else:
                logging.warning("No open access location found for this DOI.")
        else:
            logging.warning(f"Failed to get a 200 response from Unpaywall API. Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Request to Unpaywall failed for DOI {doi}: {e}")
    return doi, "fail"




def crossref_download(doi: str, headers: dict, download_dir: str) -> tuple[str, str]:
    """
    Tries to download a PDF for a given DOI using CrossRef API.
    It first fetches metadata and then looks for a direct PDF link.
    Returns a tuple of (doi, status) where status is 'success' or 'fail'.
    """
    base_crossref_url = "https://api.crossref.org/works/"
    crossref_url = f"{base_crossref_url}{doi}"

    try:
        logging.info(f"Querying CrossRef for DOI: {doi}")
        response = requests.get(crossref_url, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()
            logging.info(f"Successfully retrieved metadata for DOI: {doi}")

            links = data.get('message', {}).get('link', [])
            for link in links:
                if link.get('content-type') == 'application/pdf' and link.get('URL'):
                    pdf_url = link.get('URL')
                    logging.info(f"Found direct PDF URL on CrossRef: {pdf_url}")
                    try:
                        pdf_response = requests.get(pdf_url, headers=headers, timeout=30)
                        if pdf_response.status_code == 200 and pdf_response.content:
                            download_path = Path(download_dir)
                            download_path.mkdir(parents=True, exist_ok=True)
                            file_path = download_path / (_sanitize_doi(doi) + ".pdf")
                            file_path.write_bytes(pdf_response.content)
                            logging.info(f"Successfully saved PDF to {file_path}")
                            return doi, "success"
                        else:
                            logging.warning(f"Failed to download PDF from {pdf_url}. Status: {pdf_response.status_code}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"Request to download PDF from CrossRef link failed for {pdf_url}: {e}")
            
            logging.info("No direct PDF link found. Searching landing pages for PDF links.")
            for link in links:
                if link.get('URL'):
                    page_url = link.get('URL')
                    try:
                        page_response = requests.get(page_url, headers=headers, timeout=15)
                        if page_response.status_code == 200:
                            soup = BeautifulSoup(page_response.content, 'lxml')
                            for a_tag in soup.find_all('a', href=True):
                                if a_tag['href'].lower().endswith('.pdf'):
                                    pdf_url = a_tag['href']
                                    if not pdf_url.startswith('http'):
                                        pdf_url = urljoin(page_url, pdf_url)
                                    
                                    logging.info(f"Found potential PDF link on {page_url}: {pdf_url}")
                                    try:
                                        pdf_response = requests.get(pdf_url, headers=headers, timeout=30)
                                        if pdf_response.status_code == 200 and pdf_response.content:
                                            download_path = Path(download_dir)
                                            download_path.mkdir(parents=True, exist_ok=True)
                                            file_path = download_path / (_sanitize_doi(doi) + ".pdf")
                                            file_path.write_bytes(pdf_response.content)
                                            logging.info(f"Successfully saved PDF to {file_path}")
                                            return doi, "success"
                                        else:
                                            logging.warning(f"Failed to download PDF from {pdf_url}. Status: {pdf_response.status_code}")
                                    except requests.exceptions.RequestException as e:
                                        logging.error(f"Failed to download PDF from {pdf_url}: {e}")
                    except requests.exceptions.RequestException as e:
                        logging.warning(f"Could not access page {page_url}: {e}")

            logging.warning("No downloadable PDF link found in CrossRef metadata or on linked pages.")
        else:
            logging.warning(f"Failed to get a 200 response from CrossRef API. Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Request to CrossRef failed for DOI {doi}: {e}")
    return doi, "fail"

if __name__ == '__main__':
    # List of Sci-Hub mirrors
    
    import pandas as pd
    test_dois = pd.read_csv("sample_doi.csv")["doi"].tolist()
    for doi in test_dois:
        # result = scihub_download(doi, scihub_mirrors, headers, "scihub_downloads")
        # logging.info(f"Sci-Hub download result for {doi}: {result}")

        # result = unpaywall_download(doi, headers, "unpaywall_downloads")
        # logging.info(f"Unpaywall download result for {doi}: {result}")

        result = crossref_download(doi, headers, "crossref_downloads")
        logging.info(f"CrossRef download result for {doi}: {result}")