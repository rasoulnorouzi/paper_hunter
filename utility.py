import random
import re

import string

def generate_random_email():
    """Generate a random valid email address with common domains."""
    domains = ['gmail.com', 'outlook.com', 'live.com', 'yahoo.com']
    
    # Generate random username (5-12 characters)
    username_length = random.randint(5, 12)
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=username_length))
    
    # Select random domain
    domain = random.choice(domains)
    
    return f"{username}@{domain}"

def _sanitize_doi(doi: str) -> str:
    """
    Extracts a DOI from a string (e.g., a URL) and sanitizes it for use as a filename.
    It handles DOIs provided as full URLs or just the DOI string itself.
    """
    # Regex to find a DOI pattern, case-insensitive
    doi_regex = r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)'
    
    # Search for the DOI pattern in the input string
    match = re.search(doi_regex, doi, re.IGNORECASE)
    
    if match:
        # Extract the DOI from the matched group
        extracted_doi = match.group(1)
        # Sanitize the extracted DOI for filesystem-safe name
        return extracted_doi.replace('/', '_').replace(':', '_')
    else:
        # If no DOI pattern is found, perform a simple sanitization on the original string as a fallback
        return doi.replace('/', '_').replace(':', '_')
    

headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }


scihub_mirrors = [
        "https://sci-hub.se/",
        "https://sci-hub.st/",
        "https://sci-hub.red/",
        "https://sci-hub.ru/",
    ]