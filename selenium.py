# %%
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
import os
from pathlib import Path

def download_pdf_headless(url, download_folder=None):
    """
    Downloads a PDF from a given URL using a headless Chrome browser.

    :param url: The URL of the PDF to download.
    :param download_folder: The folder to save the downloaded file in. Defaults to a 'downloads' subfolder in the current working directory.
    :return: The full path to the downloaded file, or None if the download fails.
    """
    print("--- Starting Selenium Headless Download ---")

    if download_folder is None:
        download_folder = str(Path.cwd() / "downloads")
    
    Path(download_folder).mkdir(exist_ok=True)

    # --- Selenium download ---
    # Setup chrome options to download PDF instead of opening it
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") # Use the new headless mode
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    # Clear the downloads folder before starting
    print(f"Clearing download folder: {download_folder}")
    for f in os.listdir(download_folder):
        try:
            os.remove(os.path.join(download_folder, f))
        except OSError as e:
            print(f"Error removing file {f}: {e}")

    prefs = {
        "download.default_directory": download_folder,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    # Initialize the driver and get the URL
    print("Initializing Chrome driver...")
    driver = webdriver.Chrome(options=chrome_options)

    print(f"Navigating to {url}")
    driver.get(url)

    # Wait for the download to complete
    print(f"Waiting for download to complete in {download_folder}...")
    downloaded_file = None
    timeout = 60  # seconds
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Check for a fully downloaded PDF file that is not a temporary download file
        downloaded_files = [f for f in os.listdir(download_folder) if f.endswith('.pdf') and '.crdownload' not in f]
        if downloaded_files:
            # Check if file size is stable
            file_path = os.path.join(download_folder, downloaded_files[0])
            initial_size = os.path.getsize(file_path)
            time.sleep(2) # wait a moment to see if the file is still growing
            if initial_size == os.path.getsize(file_path):
                downloaded_file = downloaded_files[0]
                break
        time.sleep(1)

    driver.quit()
    print("Driver quit.")

    if downloaded_file:
        full_path = os.path.join(download_folder, downloaded_file)
        print(f"Download complete. File: {full_path}")
        return full_path
    else:
        print("Download failed or timed out.")
        return None

if __name__ == "__main__":
    openaccess_url = "https://www.mdpi.com/2072-6643/14/7/1428/pdf"
    downloaded_file_path = download_pdf_headless(openaccess_url)
    if downloaded_file_path:
        print(f"\nSuccessfully downloaded file to: {downloaded_file_path}")
    else:
        print("\nFailed to download the file.")


