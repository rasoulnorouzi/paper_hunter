"""Command-line entry point installed as ``paper-hunter``.

    paper-hunter app                      # launch the Streamlit UI
    paper-hunter download dois.csv -o out # bulk download from a CSV of DOIs
"""
import argparse
import sys
from pathlib import Path


def _run_app(extra_args: list[str]) -> int:
    from streamlit.web import cli as stcli

    # app.py is installed next to this module.
    app_path = Path(__file__).with_name("app.py")
    sys.argv = ["streamlit", "run", str(app_path), *extra_args]
    return stcli.main()


def _run_download(args: argparse.Namespace) -> int:
    import os

    if args.no_browser:
        os.environ["PAPER_HUNTER_BROWSER"] = "0"

    from downloader import load_dois_from_csv, run_bulk_download, zip_downloads_split

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
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("app", help="Launch the Streamlit web app (extra args go to `streamlit run`).")

    dl = sub.add_parser("download", help="Download PDFs for the DOIs listed in a CSV file.")
    dl.add_argument("csv", help="CSV file with one DOI per row.")
    dl.add_argument("-c", "--column", default=None, help="Name of the DOI column (default: detected automatically).")
    dl.add_argument("-o", "--output", default="fulldownloads", help="Output directory (default: fulldownloads).")
    dl.add_argument("--no-browser", action="store_true", help="Disable the headless-browser fallback.")
    dl.add_argument("--zip", action="store_true", help="Also zip the PDFs and summary CSV.")
    dl.add_argument("--splits", type=int, default=1, help="Split the results into N ZIP files (implies --zip).")

    args, extra = parser.parse_known_args()
    if args.command == "app":
        return _run_app(extra)
    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return _run_download(args)


if __name__ == "__main__":
    sys.exit(main())
