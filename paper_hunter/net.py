"""Network settings shared by all sources: HTTP headers, Sci-Hub mirrors, polite delays."""
import os
import random
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

headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


# Sci-Hub mirror list (last verified 2026-10).
#
# Every mirror here answered with a real article page during verification.
# Mirrors that gate traffic behind a proof-of-work check are still usable
# because the Altcha solver in ``altcha.py`` handles them without a
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