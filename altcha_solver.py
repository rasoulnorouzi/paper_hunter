"""Browserless solver for the Altcha proof-of-work gate used by Sci-Hub mirrors.

Several Sci-Hub mirrors (sci-hub.ru, and sometimes sci-hub.st / sci-hub.su) put a
small robot check in front of the article page.  The check is a standard
`Altcha <https://altcha.org>`_ proof of work:

1. The gate page contains ``<altcha-widget challengeurl="/captcha/challenge/<id>">``.
2. ``GET /captcha/challenge/<id>`` returns a JSON challenge with a ``salt`` and a
   target SHA-256 ``challenge`` hash.
3. The client searches for the smallest integer ``n`` such that
   ``sha256(salt + n) == challenge``.
4. The solution is POSTed back as ``{"captcha": <base64 payload>}`` to
   ``/captcha/solution/<id>``, which sets a session cookie.

Because the proof of work is just hashing, it can be solved with plain
``requests`` and ``hashlib`` - no browser required.  This keeps the fast,
browserless path working even on the mirrors that gate requests.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

# Regex for the Altcha widget on the gate page.
#
# A real gate uses ``challengeurl="/captcha/challenge/<id>"``.  Article pages
# can also embed an Altcha widget for their "report article" dialog, but that
# one points at ``/captcha/challenge`` with no id, so requiring the id avoids
# mistaking the report widget for a bot gate.
_CHALLENGE_URL_RE = re.compile(
    r"challengeurl\s*=\s*[\"']([^\"']*/captcha/challenge/[^\"'/?]+)[\"']",
    re.IGNORECASE,
)


def find_challenge_url(html: str, base_url: str) -> Optional[str]:
    """Return the absolute Altcha challenge URL if the page carries a gate."""
    if not html or "<altcha-widget" not in html.lower():
        return None
    match = _CHALLENGE_URL_RE.search(html)
    if not match:
        return None
    return urljoin(base_url, match.group(1))


def _solution_url(challenge_url: str) -> str:
    return challenge_url.replace("/captcha/challenge", "/captcha/solution")


def solve_challenge(challenge: dict) -> Optional[int]:
    """Brute-force the Altcha number for a single challenge payload."""
    salt = challenge.get("salt")
    target = challenge.get("challenge")
    if not salt or not target:
        return None
    max_number = challenge.get("maxNumber") or challenge.get("maxnumber")
    try:
        max_number = int(max_number)
    except (TypeError, ValueError):
        max_number = 1_000_000
    # Safety cap so a hostile/broken challenge cannot spin forever.
    max_number = min(max_number, 5_000_000)

    target = str(target).lower()
    prefix = f"{salt}"
    for number in range(max_number + 1):
        digest = hashlib.sha256(f"{prefix}{number}".encode()).hexdigest()
        if digest == target:
            return number
    return None


def build_payload(challenge: dict, number: int) -> str:
    """Encode the Altcha solution payload exactly as the widget does."""
    payload = {
        "algorithm": challenge.get("algorithm", "SHA-256"),
        "challenge": challenge.get("challenge"),
        "number": number,
        "salt": challenge.get("salt"),
        "signature": challenge.get("signature"),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


def solve_gate(
    session: requests.Session,
    html: str,
    base_url: str,
    headers: Optional[dict] = None,
    timeout: int = 20,
) -> bool:
    """Solve the Altcha gate for ``base_url`` using an existing HTTP session.

    Returns ``True`` when the challenge was solved and the server accepted the
    solution (a subsequent request should then see the real article page).
    """
    challenge_url = find_challenge_url(html, base_url)
    if not challenge_url:
        return False

    try:
        resp = session.get(challenge_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        challenge = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Altcha: could not fetch challenge %s (%s)", challenge_url, exc)
        return False

    number = solve_challenge(challenge)
    if number is None:
        logger.warning("Altcha: no solution found for challenge %s", challenge_url)
        return False

    payload = build_payload(challenge, number)
    solution_url = _solution_url(challenge_url)
    try:
        post = session.post(
            solution_url,
            headers={**(headers or {}), "Content-Type": "application/json"},
            json={"captcha": payload},
            timeout=timeout,
        )
        post.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Altcha: failed to submit solution to %s (%s)", solution_url, exc)
        return False

    logger.info("Altcha: solved proof-of-work (n=%s) for %s", number, base_url)
    return True
