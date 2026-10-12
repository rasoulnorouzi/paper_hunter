"""Command line: ``paper-hunter`` (or ``python -m paper_hunter``).

    paper-hunter                            # open the web UI in the browser
    paper-hunter ui --port 8800             # same, on a chosen port
    paper-hunter download dois.csv -o out   # bulk download from a CSV, no UI
"""
import argparse
import os
import sys
from pathlib import Path


def _run_ui(args: argparse.Namespace) -> int:
    from .server import serve

    serve(port=args.port, open_browser=not args.no_open, runs_dir=args.runs_dir)
    return 0


def _run_download(args: argparse.Namespace) -> int:
    if args.no_browser:
        os.environ["PAPER_HUNTER_BROWSER"] = "0"

    from .core import run_bulk_download, zip_downloads_split
    from .dois import load_dois_from_csv

    try:
        dois = load_dois_from_csv(args.csv, column=args.column)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    results = run_bulk_download(dois, download_dir=args.output, use_browser=not args.no_browser)
    success = int(results["success"].sum()) if len(results) else 0
    print(f"Downloaded {success}/{len(results)} papers into {args.output}")
    print(f"Summary: {Path(args.output) / 'download_summary.csv'}")
    if args.zip or args.splits > 1:
        for zip_path in zip_downloads_split(args.output, n_splits=args.splits, zip_name=Path(args.output).name, dois=dois):
            print(f"Zip: {zip_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="paper-hunter", description="Bulk download academic papers from DOIs.")
    sub = parser.add_subparsers(dest="command")

    ui = sub.add_parser("ui", help="Open the web UI (default when no command is given).")
    ui.add_argument("--port", type=int, default=8765, help="Port to serve on (default: 8765, next free one if taken).")
    ui.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    ui.add_argument("--runs-dir", default="paper_hunter_runs", help="Where each run's PDFs are saved (default: paper_hunter_runs).")

    dl = sub.add_parser("download", help="Download PDFs for the DOIs listed in a CSV file.")
    dl.add_argument("csv", help="CSV file with one DOI per row.")
    dl.add_argument("-c", "--column", default=None, help="Name of the DOI column (default: detected automatically).")
    dl.add_argument("-o", "--output", default="fulldownloads", help="Output directory (default: fulldownloads).")
    dl.add_argument("--no-browser", action="store_true", help="Disable the headless-browser fallback.")
    dl.add_argument("--zip", action="store_true", help="Also zip the PDFs and summary CSV.")
    dl.add_argument("--splits", type=int, default=1, help="Split the results into N ZIP files (implies --zip).")

    args = parser.parse_args()
    if args.command == "download":
        return _run_download(args)
    if args.command is None:
        args = ui.parse_args([])
    return _run_ui(args)


if __name__ == "__main__":
    sys.exit(main())
