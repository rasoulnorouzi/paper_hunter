"""Paper Hunter: bulk download academic papers (PDFs) from a list of DOIs.

    from paper_hunter import load_dois_from_csv, run_bulk_download, zip_downloads_split

    dois = load_dois_from_csv("dois.csv")          # DOI column detected automatically
    results = run_bulk_download(dois, "papers")    # DataFrame: doi, success, source, file
    zip_downloads_split("papers", n_splits=5)      # papers_part1_of_5.zip ... part5
"""
from .core import make_manager, run_bulk_download, zip_downloads, zip_downloads_split
from .dois import clean_dois, doi_to_filename, extract_doi, find_doi_column, load_dois_from_csv, read_doi_table, split_dois

__version__ = "0.2.0"

__all__ = [
    "clean_dois",
    "doi_to_filename",
    "extract_doi",
    "find_doi_column",
    "load_dois_from_csv",
    "make_manager",
    "read_doi_table",
    "run_bulk_download",
    "split_dois",
    "zip_downloads",
    "zip_downloads_split",
]
