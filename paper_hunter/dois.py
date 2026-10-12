"""DOI parsing: extract and clean DOIs, split lists, and load DOIs from CSV files."""
import csv
import io
import logging
import re
from pathlib import Path

import pandas as pd
import requests

from .net import headers as _default_headers

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


def doi_to_filename(doi: str) -> str:
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


def read_doi_table(source) -> pd.DataFrame:
    """Read a CSV of DOIs from a path, URL or file-like object.

    The separator (``,``, ``;``, tab or ``|``) is detected automatically, and a
    file without a header row (DOIs on the first line) is handled too.
    """
    if hasattr(source, "read"):
        if hasattr(source, "seek"):
            source.seek(0)
        raw = source.read()
    elif str(source).startswith(("http://", "https://")):
        response = requests.get(str(source), headers=_default_headers, timeout=30)
        response.raise_for_status()
        raw = response.content
    else:
        raw = Path(source).read_bytes()

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    else:
        text = raw

    try:
        sep = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ","

    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str)
    # No header row: the first DOI was used as a column name, so re-read.
    if any(extract_doi(col) for col in df.columns):
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, header=None)
        df.columns = [f"column {i + 1}" for i in range(df.shape[1])]
    return df


def find_doi_column(df: pd.DataFrame):
    """Return the column of *df* that holds the DOIs.

    Columns whose name contains "doi" (any case) are preferred; among the
    candidates the one with the most DOI-looking values wins. If no such column
    contains DOIs, every column is scanned.
    """
    def doi_count(column) -> int:
        return sum(extract_doi(v) is not None for v in df[column].dropna())

    named = [c for c in df.columns if "doi" in str(c).lower()]
    for candidates in (named, list(df.columns)):
        counts = {c: doi_count(c) for c in candidates}
        best = max(counts, key=counts.get, default=None)
        if best is not None and counts[best] > 0:
            return best
    raise ValueError(f"No column containing DOIs found. Columns: {list(df.columns)}")


def load_dois_from_csv(source, column=None) -> list[str]:
    """Load the DOIs from a CSV file (path, URL or file-like object).

    The DOI column is detected automatically unless *column* is given. Empty or
    whitespace-only rows, non-DOI values and duplicates are skipped.
    """
    df = read_doi_table(source)
    if column is None:
        column = find_doi_column(df)
    elif column not in df.columns:
        raise ValueError(f"Column '{column}' not found. Columns: {list(df.columns)}")
    dois = clean_dois(df[column])
    logging.info(f"Loaded {len(dois)} DOI(s) from column '{column}'.")
    return dois
