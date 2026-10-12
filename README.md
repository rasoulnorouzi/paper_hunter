# Paper Hunter

[![CI](https://github.com/rasoulnorouzi/paper_hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/rasoulnorouzi/paper_hunter/actions/workflows/ci.yml)

Bulk download academic papers (PDFs) from a list of DOIs: paste them or give a
CSV file, and get the PDFs plus a report of which papers were found.

Use it as a web page on your own computer, from Python, or from the command line.

## Quick start: one click

1. Download this repository: green **Code** button → **Download ZIP**, then unzip it.
2. Double-click the starter for your system:

   | System | Double-click |
   |---|---|
   | Windows | `start_windows.bat` |
   | macOS | `start_macos.command` |
   | Linux | `start_linux.sh` (or run `./start_linux.sh` in a terminal) |

That's all. The starter installs whatever is missing (including Python, if
needed), and Paper Hunter opens in your browser at <http://127.0.0.1:8765>.
The first start takes a few minutes; after that it starts in seconds. Close the
starter's window to quit.

The first time, your system may ask for confirmation because the file was
downloaded from the internet:

- **Windows:** "Windows protected your PC" → **More info** → **Run anyway**.
- **macOS:** "cannot be opened because it is from an unidentified developer" →
  right-click the file → **Open** → **Open**.
- **Linux:** if double-clicking opens the file as text, right-click it →
  **Run as a Program**, or run `./start_linux.sh` in a terminal.

<details>
<summary>What the starter does</summary>

1. Looks for Python 3.11 or newer. If there is none, it installs Python with
   [uv](https://docs.astral.sh/uv/), without administrator rights.
2. Runs `launch_paper_hunter.py`, which creates a private environment in the
   `myenv` folder, installs Paper Hunter's packages into it (first run only)
   and starts the web page. You can also run that script directly with
   `python3 launch_paper_hunter.py`.

</details>

### Using the web page

1. Paste DOIs (one per line) and/or choose a CSV file. The DOI column is found
   automatically; empty rows and duplicates are skipped.
2. Click **Start**. A counter shows how many papers were found and not found,
   with a row per DOI. **Stop** ends the run after the current paper.
3. When it finishes, choose how many ZIP files to split the results into and
   click **Make ZIP**. Each ZIP holds its PDFs and a `download_summary.csv`.
   **Retry the ones not found** starts a new run with just the missing DOIs.

The PDFs are also kept on disk, in `paper_hunter_runs/run_<date>_<time>/`.

## Use from Python

```bash
pip install git+https://github.com/rasoulnorouzi/paper_hunter.git
```

```python
from paper_hunter import load_dois_from_csv, run_bulk_download, zip_downloads_split

dois = load_dois_from_csv("dois.csv")                             # DOI column detected automatically
results = run_bulk_download(dois, download_dir="papers")          # DataFrame: doi, success, source, file
zip_downloads_split("papers", n_splits=5, zip_name="papers")      # papers_part1_of_5.zip ... part5
```

A running count is printed before each DOI, and a summary with the DOIs that
were not found at the end. Every run also writes `download_summary.csv` into
the download folder.

`load_dois_from_csv` accepts a path, a URL or a file object. It prefers columns
with "doi" in the name and otherwise scans every column for DOIs (pass
`column=` to choose), detects the separator (`,` `;` tab `|`), handles CSVs
without a header row, and accepts `doi:10.…` and `https://doi.org/10.…` values.

To update later, force a reinstall (pip skips it while the version number is
unchanged) and restart any running notebook kernel:

```bash
pip install --force-reinstall --no-deps git+https://github.com/rasoulnorouzi/paper_hunter.git
```

### Notebooks

- [`examples/quickstart.ipynb`](examples/quickstart.ipynb): the shortest path from a CSV to PDFs and a ZIP.
- [`examples/download_dois_from_csv.ipynb`](examples/download_dois_from_csv.ipynb):
  the full workflow, exporting a per-DOI report (CSV, Excel, JSON, Markdown),
  the failed DOIs and split ZIPs. Works in Google Colab.

## Command line

```bash
paper-hunter                                          # open the web page
paper-hunter download dois.csv -o papers --zip        # download, then one ZIP
paper-hunter download dois.csv -o papers --splits 5   # download, then five ZIPs
```

`python -m paper_hunter` works the same way. Run `paper-hunter download -h` for
all options.

## How downloads work

Each DOI goes through these sources in order, stopping at the first PDF. Free
and legal sources come first; Sci-Hub is only a last resort:

1. **Unpaywall**: legal open-access copies.
2. **OpenAlex**: legal open-access locations from the open OpenAlex database.
3. **Crossref**: publisher metadata and linked PDFs. Responses are checked for
   the `%PDF` signature, so HTML error pages are never saved as papers.
4. **Sci-Hub**, in two stages:
   - **Without a browser (fast):** plain HTTP requests across the mirrors. When
     a mirror shows its Altcha proof-of-work check, it is solved directly in
     Python (`paper_hunter/altcha.py`).
   - **With a headless browser (fallback):** only if every mirror fails, a real
     Chrome driven by [nodriver](https://github.com/ultrafunkamsterdam/nodriver)
     gets past JavaScript and bot checks. It is started once and reused for
     the whole run. If Chrome/Chromium is not installed, this step is skipped.

To avoid looking like a bot, Paper Hunter waits a random 1–3 seconds between
DOIs and 0.4–1.2 seconds between Sci-Hub mirror attempts.

## Project layout

```
start_windows.bat          one-click starters: find or install Python,
start_macos.command          then run launch_paper_hunter.py
start_linux.sh
launch_paper_hunter.py     sets up myenv and opens the web page
paper_hunter/              the Python package
  core.py                  run_bulk_download, ZIP helpers
  dois.py                  DOI parsing and CSV loading
  sources.py               Unpaywall, OpenAlex, Crossref and Sci-Hub sources
  altcha.py                solver for the Sci-Hub proof-of-work check
  browser.py               headless Chrome fallback
  net.py                   HTTP headers, Sci-Hub mirrors, delays
  server.py                API behind the web page (FastAPI)
  static/                  the web page (HTML, CSS, JS)
  cli.py                   the paper-hunter command
examples/                  notebooks and a sample CSV
tests/                     offline tests (pytest)
.github/workflows/ci.yml   CI: installs and tests on Windows, macOS and Linux
index.html                 the project's GitHub Pages site
```

Downloads, ZIPs, run folders, virtual environments and personal CSV files are
listed in `.gitignore`, so they never end up in the repository.

To add a source, subclass `PDFDownloader` in `paper_hunter/sources.py`,
implement `try_download(doi)`, and add it to `make_manager` in
`paper_hunter/core.py`.

## Running the tests

```bash
pip install -e ".[test]"
pytest
```

The tests need no internet connection. On every push, GitHub Actions installs
Paper Hunter on Windows, macOS and Linux (Python 3.11 and 3.13), runs the
tests, and starts each one-click starter to check that the web page comes up.

## Configuration

Environment variables:

- `PAPER_HUNTER_BROWSER=0`: turn off the headless-browser fallback.
- `PAPER_HUNTER_HEADLESS=0`: show the fallback browser window (for debugging).
- `PAPER_HUNTER_MIN_DELAY` / `PAPER_HUNTER_MAX_DELAY`: bounds of the random
  pause between DOIs, in seconds (defaults `1` and `3`). Set both to `0` to
  turn the pauses off.

### Sci-Hub mirrors

The mirror list is in `paper_hunter/net.py` and is shuffled at runtime. It was
last checked in October 2026:

```
https://sci-hub.st/   https://sci-hub.su/   https://sci-hub.ru/
https://sci-hub.al/   https://sci-hub.mk/   https://sci-hub.ee/
```

`sci-hub.se` is unreachable (blocked via DNS) and was removed, along with
`sci-hub.box`, `sci-hub.red` and `sci-hub.wf`, which were down when checked.
Domains come and go, so if downloads start failing, refresh this list.

## Troubleshooting

- **The starter can't install Python** (for example without internet access
  or behind a strict firewall): install Python 3.11+ from
  [python.org](https://www.python.org/downloads/) (on Windows, tick "Add
  python.exe to PATH"), then double-click the starter again.
- **Some papers are not found:** some papers are not freely available anywhere.
  Sci-Hub mirrors also change often, so **Retry the ones not found** later.
- **Port already in use:** Paper Hunter picks the next free port and prints
  the address in the launcher window.
