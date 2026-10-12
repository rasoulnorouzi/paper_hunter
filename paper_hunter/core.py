# %%
import csv
import io
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import logging
import pandas as pd
import requests
from plugins_class import (
    UnpaywallDownloader,
    OpenAlexDownloader,
    CrossrefDownloader,
    SciHubDownloader,
    PDFDownloadManager,
)
from utility import (
    headers as _global_headers,
    scihub_mirrors as _global_mirrors,
    clean_dois,
    extract_doi,
    split_dois,
    _sanitize_doi,
)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# %%

def download_pdf_from_doi(doi: str, mirrors: list, headers: dict) -> tuple[str, str]:
    """
    Downloads a PDF for a given DOI using the Sci-Hub strategy.

    Delegates to :class:`SciHubDownloader`, which tries the fast browserless
    path (requests + Altcha proof-of-work solving) across the mirrors and then
    falls back to a headless nodriver browser if needed.

    Returns a tuple of (doi, status) where status is 'success' or 'fail'.
    """
    downloader = SciHubDownloader(
        headers=headers, download_dir=Path("scihub_downloads"), mirrors=mirrors
    )
    path = downloader.try_download(doi)
    if path:
        logging.info(f"Successfully saved PDF to {path}")
        return doi, "success"
    logging.error(f"All mirrors failed for DOI: {doi}")
    return doi, "fail"


def run_bulk_download(
    dois: list[str] | str,
    download_dir: str | Path = "fulldownloads",
    headers: dict | None = None,
    mirrors: list[str] | None = None,
    use_browser: bool = True,
) -> pd.DataFrame:
    """High-level helper wrapping the plugin-based PDFDownloadManager.

    Keeps notebook usage minimal by:
    - Accepting a list or single DOI (blank rows, non-DOI values and duplicates
      are skipped; ``doi:`` prefixes and ``https://doi.org/`` URLs are accepted)
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
    dois_list = clean_dois([dois] if isinstance(dois, str) else dois)

    headers = headers or _global_headers
    mirrors = mirrors or _global_mirrors
    download_path = Path(download_dir)

    strategies = [
        UnpaywallDownloader(headers=headers, download_dir=download_path),
        OpenAlexDownloader(headers=headers, download_dir=download_path),
        CrossrefDownloader(headers=headers, download_dir=download_path),
        SciHubDownloader(headers=headers, download_dir=download_path, mirrors=mirrors, use_browser=use_browser),
    ]
    manager = PDFDownloadManager(strategies=strategies, download_dir=download_path)
    manager.download(dois_list)
    manager.save_results_to_csv()
    return pd.DataFrame(manager.results)


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
        response = requests.get(str(source), headers=_global_headers, timeout=30)
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


def zip_downloads_split(
    download_dir: str | Path = "fulldownloads",
    n_splits: int = 1,
    zip_name: str = "papers_zip",
    dois: list[str] | None = None,
    out_dir: str | Path | None = None,
    extra_files=(),
) -> list[Path]:
    """Split the results into *n_splits* ZIP files.

    The DOIs (in input order) are divided into *n_splits* parts of nearly equal
    size; each ZIP holds the PDFs of its part plus a ``download_summary.csv``
    covering exactly those DOIs (including the failed ones).

    Parameters
    ----------
    download_dir : str | Path
        Directory containing the downloaded PDFs.
    n_splits : int
        Number of ZIP files to create (clamped to the number of DOIs).
    zip_name : str
        Base name; parts are named ``<zip_name>_part1_of_N.zip`` and so on,
        or ``<zip_name>.zip`` when there is a single part.
    dois : list[str] | None
        DOIs to split. Defaults to the DOIs in ``download_summary.csv``.
    out_dir : str | Path | None
        Where to write the ZIPs (defaults to the parent of *download_dir*).
    extra_files : iterable of paths
        Files added to every part, e.g. a full report.

    Returns
    -------
    list[Path]
        Paths of the created ZIP files, in order.
    """
    download_path = Path(download_dir)
    if not download_path.exists():
        raise FileNotFoundError(f"Download directory not found: {download_path}")

    status = {}
    summary_csv = download_path / "download_summary.csv"
    if summary_csv.exists():
        summary = pd.read_csv(summary_csv, dtype={"doi": str})
        status = dict(zip(summary["doi"], summary["success"].astype(bool)))
    if dois is None:
        if not status:
            raise ValueError("No download_summary.csv found; pass the DOIs with dois=.")
        dois = list(status)

    parts = split_dois(clean_dois(dois), n_splits)
    out_path = Path(out_dir) if out_dir is not None else download_path.parent
    out_path.mkdir(parents=True, exist_ok=True)

    zip_paths = []
    for i, part in enumerate(parts, start=1):
        name = f"{zip_name}.zip" if len(parts) == 1 else f"{zip_name}_part{i}_of_{len(parts)}.zip"
        zip_path = out_path / name
        rows = []
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
            for doi in part:
                pdf = download_path / f"{_sanitize_doi(doi)}.pdf"
                ok = bool(status.get(doi, True)) and pdf.exists()
                if ok:
                    zf.write(pdf, pdf.name)
                rows.append({"doi": doi, "success": ok})
            zf.writestr("download_summary.csv", pd.DataFrame(rows, columns=["doi", "success"]).to_csv(index=False))
            for extra in extra_files:
                zf.write(extra, Path(extra).name)
        zip_paths.append(zip_path)
        logging.info(f"Created zip archive at {zip_path} ({sum(r['success'] for r in rows)} PDFs)")
    return zip_paths

if __name__ == '__main__':
    test_dois = load_dois_from_csv("sample_doi.csv")

    # Uses the same priority order as the app: Unpaywall -> Crossref -> Sci-Hub.
    results = run_bulk_download(test_dois, download_dir="fulldownloads")

    total = len(results)
    success = int(results["success"].sum()) if total else 0
    print(f"Success rate: {success}/{total}")
    print(f"Failed rate: {total - success}/{total}")
