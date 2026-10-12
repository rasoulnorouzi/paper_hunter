# Paper Hunter

A tool to bulk download academic papers using DOIs.

## Requirements

- **Python 3.11+** (Python 3.13 recommended)
- **Google Chrome / Chromium** (optional) — only needed for the headless-browser
  fallback. Without it the app still works in browserless mode.

## Running the app

### Option A — Windows one-click launcher

1. Double-click `launch_paper_hunter.bat`.
2. **First run only:** wait a few minutes while the virtual environment and
   packages are installed.
3. The app opens automatically in your browser.

### Option B — Manual (any OS)

```bash
# 1. Create and activate a virtual environment
python -m venv myenv
# Windows:
myenv\Scripts\activate
# Linux / macOS:
source myenv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the app
streamlit run app.py
```

Then open <http://localhost:8501> if it does not open automatically.

## How to Use

1. Copy your DOIs (one per line).
2. Paste them into the text input field in the app.
3. Click **Start Downloading**.
4. When finished, download the results ZIP (PDFs + `download_summary.csv`).

### Example Input

```
10.1038/nphys1170
10.1126/science.aba2420
10.1016/j.cell.2020.06.043
```

## How downloads work

Paper Hunter tries several strategies per DOI, in order, stopping at the first
success. Free/legal sources come first; Sci-Hub is only a last resort:

1. **Unpaywall** – legal open-access copies.
2. **OpenAlex** – legal open-access locations from the open OpenAlex database.
3. **Crossref** – publisher metadata and linked PDFs (responses are checked for
   the `%PDF` signature, so HTML error pages are never saved as papers).
4. **Sci-Hub** – a two-stage fallback (only if nothing above worked):
   - **Browserless (fast path):** plain `requests` across the mirrors. When a
     mirror presents its Altcha proof-of-work gate, the challenge is solved
     directly in Python (`altcha_solver.py`), so no browser is needed.
   - **Headless browser (fallback):** only if every mirror fails, a real
     headless Chrome instance driven by [nodriver](https://github.com/ultrafunkamsterdam/nodriver)
     (the successor of undetected-chromedriver) is used to get past JavaScript
     and bot challenges. The browser is started once and reused for the whole run.

`nodriver` is optional. If it, or Chrome/Chromium, is not installed, the app
still runs and just skips the browser fallback.

### Randomised delays

To avoid looking like a bot, the app sleeps for a random interval (default
1–3 seconds) between DOIs, and a shorter random pause (0.4–1.2 s) between
Sci-Hub mirror attempts. This largely prevents rate limiting or blocks from
piling up requests.

## Sci-Hub mirrors

The mirror list lives in `utility.py` and is shuffled at runtime. It was last
verified in October 2026:

```
https://sci-hub.st/   https://sci-hub.su/   https://sci-hub.ru/
https://sci-hub.al/   https://sci-hub.mk/   https://sci-hub.ee/
```

`sci-hub.se` is currently unreachable (blocked via DNS) and was removed, along
with `sci-hub.box`, `sci-hub.red` and `sci-hub.wf`, which were down at
verification time. Domains come and go, so if downloads start failing, refresh
this list.

## Configuration

Environment variables:

- `PAPER_HUNTER_BROWSER=0` – disable the headless-browser fallback entirely
  (browserless mode only).
- `PAPER_HUNTER_HEADLESS=0` – run the fallback browser with a visible window
  (useful for debugging).
- `PAPER_HUNTER_MIN_DELAY` / `PAPER_HUNTER_MAX_DELAY` – bounds of the random
  pause between DOIs, in seconds (defaults `1` and `3`). Set both to `0` to
  disable the delays.

## Troubleshooting

- **App doesn't start?** Make sure Python 3.11+ is installed and added to PATH.
- **Downloads failing?** Some papers are not freely accessible anywhere. Try
  again later: Sci-Hub mirrors rotate frequently.
