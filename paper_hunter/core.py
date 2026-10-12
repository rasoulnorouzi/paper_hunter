"""High-level API: download a list of DOIs and package the results as ZIP files."""
import logging
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pandas as pd

from .dois import clean_dois, doi_to_filename, split_dois
from .net import headers as default_headers, scihub_mirrors as default_mirrors
from .sources import (
    CrossrefDownloader,
    OpenAlexDownloader,
    PDFDownloadManager,
    SciHubDownloader,
    UnpaywallDownloader,
)

# Show progress logs in notebooks and terminals (no-op if logging is already configured).
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def make_manager(
    download_dir: str | Path,
    headers: dict | None = None,
    mirrors: list[str] | None = None,
    use_browser: bool = True,
) -> PDFDownloadManager:
    """Build a manager that tries Unpaywall, OpenAlex, Crossref, then Sci-Hub."""
    headers = headers or default_headers
    mirrors = mirrors or default_mirrors
    download_path = Path(download_dir)
    strategies = [
        UnpaywallDownloader(headers=headers, download_dir=download_path),
        OpenAlexDownloader(headers=headers, download_dir=download_path),
        CrossrefDownloader(headers=headers, download_dir=download_path),
        SciHubDownloader(headers=headers, download_dir=download_path, mirrors=mirrors, use_browser=use_browser),
    ]
    return PDFDownloadManager(strategies=strategies, download_dir=download_path)


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
    - Trying Unpaywall, OpenAlex, Crossref, then Sci-Hub for each DOI
    - Running manager.download and returning a DataFrame of results
    - Persisting the CSV summary (download_summary.csv) in the target directory

    Parameters
    ----------
    dois : list[str] | str
        DOIs to attempt downloading.
    download_dir : str | Path
        Output directory for PDFs and summary CSV.
    headers : dict | None
        Optional override for HTTP headers (defaults to net.headers).
    mirrors : list[str] | None
        Optional override for Sci-Hub mirrors (defaults to net.scihub_mirrors).
    use_browser : bool
        Allow the headless-browser fallback for Sci-Hub (needs Chrome/Chromium).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: doi, success (bool), source (which site
        delivered the PDF), file (PDF file name).
    """
    dois_list = clean_dois([dois] if isinstance(dois, str) else dois)
    manager = make_manager(download_dir, headers=headers, mirrors=mirrors, use_browser=use_browser)
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
    download_path = Path(download_dir)
    if not download_path.exists():
        raise FileNotFoundError(f"Download directory not found: {download_path}")

    zip_path = download_path.parent / f"{zip_name}.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
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
                pdf = download_path / f"{doi_to_filename(doi)}.pdf"
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
