import logging
import os
import random
import re
import string
import time


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Randomised delay bounds (seconds) used between network attempts to look more
# human and avoid getting rate-limited or blocked.  Set both to 0 to disable.
MIN_DELAY_SECONDS = _env_float("PAPER_HUNTER_MIN_DELAY", 1.0)
MAX_DELAY_SECONDS = _env_float("PAPER_HUNTER_MAX_DELAY", 3.0)


def human_delay(min_seconds: float | None = None, max_seconds: float | None = None) -> float:
    """Sleep for a random, human-looking interval and return the duration.

    Defaults come from ``PAPER_HUNTER_MIN_DELAY`` / ``PAPER_HUNTER_MAX_DELAY``
    (1 and 3 seconds).  Passing explicit bounds overrides them; setting the
    upper bound to 0 disables the pause.
    """
    low = MIN_DELAY_SECONDS if min_seconds is None else min_seconds
    high = MAX_DELAY_SECONDS if max_seconds is None else max_seconds
    if high <= 0:
        return 0.0
    if high < low:
        high = low
    seconds = random.uniform(low, high)
    time.sleep(seconds)
    return seconds

def generate_random_email():
    """Generate a random valid email address with common domains."""
    domains = ['gmail.com', 'outlook.com', 'live.com', 'yahoo.com']
    
    # Generate random username (5-12 characters)
    username_length = random.randint(5, 12)
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=username_length))
    
    # Select random domain
    domain = random.choice(domains)
    
    return f"{username}@{domain}"

DOI_PATTERN = re.compile(r'10\.\d{4,9}/[^\s]+')


def extract_doi(value) -> str | None:
    """Return the DOI found in *value*, or None for blank / non-DOI values.

    Accepts bare DOIs as well as ``doi:`` prefixes and ``https://doi.org/`` URLs,
    and ignores surrounding whitespace.
    """
    if value is None:
        return None
    match = DOI_PATTERN.search(str(value).strip())
    if not match:
        return None
    return match.group(0).rstrip('.,;')


def clean_dois(values) -> list[str]:
    """Extract DOIs from *values*, skipping blanks, non-DOIs and duplicates.

    Order is preserved; duplicates are detected case-insensitively because DOIs
    are case-insensitive.
    """
    dois, seen, skipped = [], set(), 0
    for value in values:
        doi = extract_doi(value)
        if doi is None:
            if value is not None and str(value).strip() and str(value).strip().lower() != 'nan':
                skipped += 1
            continue
        if doi.lower() not in seen:
            seen.add(doi.lower())
            dois.append(doi)
    if skipped:
        logging.warning(f"Skipped {skipped} value(s) that do not look like DOIs.")
    return dois


def split_dois(dois: list, n_parts: int) -> list[list]:
    """Split *dois* into *n_parts* contiguous chunks whose sizes differ by at most one.

    *n_parts* is clamped to ``[1, len(dois)]`` so no chunk is ever empty.
    """
    n_parts = max(1, min(int(n_parts), len(dois))) if dois else 1
    size, extra = divmod(len(dois), n_parts)
    parts, start = [], 0
    for i in range(n_parts):
        end = start + size + (1 if i < extra else 0)
        parts.append(list(dois[start:end]))
        start = end
    return parts


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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


# Sci-Hub mirror list (last verified 2026-10).
#
# Every mirror here answered with a real article page during verification.
# Mirrors that gate traffic behind a proof-of-work check are still usable
# because the Altcha solver in ``altcha_solver.py`` handles them without a
# browser (see ``SciHubDownloader``).  The list is shuffled at runtime, so order
# is only a hint of relative reliability.  Sci-Hub domains come and go, so
# refresh this list if downloads start failing.
scihub_mirrors = [
        "https://sci-hub.st/",
        "https://sci-hub.su/",
        "https://sci-hub.ru/",
        "https://sci-hub.al/",
        "https://sci-hub.mk/",
        "https://sci-hub.ee/",
    ]