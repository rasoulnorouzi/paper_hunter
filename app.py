import io
import logging
import shutil
import uuid
from pathlib import Path

import streamlit as st

from plugins_class import (
    PDFDownloadManager,
    OpenAlexDownloader,
    UnpaywallDownloader,
    CrossrefDownloader,
    SciHubDownloader,
)
from utility import headers, scihub_mirrors, clean_dois
from browser_fetcher import browser_available
from downloader import read_doi_table, find_doi_column, zip_downloads_split


# --- Helpers: session state management ---
def _init_state():
    st.session_state.setdefault("uploaded_file", None)
    st.session_state.setdefault("dois", [])
    st.session_state.setdefault("total", 0)
    st.session_state.setdefault("current_index", 0)
    st.session_state.setdefault("running", False)
    st.session_state.setdefault("stop", False)
    st.session_state.setdefault("logs", [])
    st.session_state.setdefault("run_dir", None)
    st.session_state.setdefault("manager", None)
    st.session_state.setdefault("results_saved", False)
    st.session_state.setdefault("zip_ready", False)
    st.session_state.setdefault("zip_parts", None)  # list of (file name, bytes)
    st.session_state.setdefault("zip_parts_n", None)
    st.session_state.setdefault("use_browser", browser_available())


class StreamlitLogHandler(logging.Handler):
    # Stable marker so the handler can be recognised across script reruns,
    # where the class object itself is recreated and isinstance() would fail.
    _is_streamlit_log_handler = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        st.session_state.logs.append(msg)


def _attach_logging():
    # Configure root logger once per process
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers across reruns. Streamlit re-executes this
    # script on every rerun, which recreates the class, so check a stable
    # attribute instead of the class identity.
    if not any(getattr(h, "_is_streamlit_log_handler", False) for h in logger.handlers):
        sh = StreamlitLogHandler()
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")
        sh.setFormatter(formatter)
        logger.addHandler(sh)


def _prepare_manager(dois: list[str]):
    run_root = Path("streamlit_runs")
    run_root.mkdir(parents=True, exist_ok=True)
    run_dir = run_root / f"run_{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)

    strategies = [
        UnpaywallDownloader(headers=headers, download_dir=run_dir),
        OpenAlexDownloader(headers=headers, download_dir=run_dir),
        CrossrefDownloader(headers=headers, download_dir=run_dir),
        SciHubDownloader(
            headers=headers,
            download_dir=run_dir,
            mirrors=scihub_mirrors,
            use_browser=st.session_state.get("use_browser", True),
        ),
    ]
    manager = PDFDownloadManager(strategies=strategies, download_dir=run_dir)

    st.session_state.run_dir = str(run_dir)
    st.session_state.manager = manager
    st.session_state.dois = dois
    st.session_state.total = len(dois)
    st.session_state.current_index = 0
    st.session_state.running = True
    st.session_state.stop = False
    st.session_state.results_saved = False
    st.session_state.zip_ready = False
    st.session_state.zip_parts = None
    st.session_state.zip_parts_n = None


def _process_one():
    # Process exactly one DOI per rerun to keep UI responsive (supports Stop)
    i = st.session_state.current_index
    if i >= st.session_state.total:
        return

    doi = st.session_state.dois[i]
    manager: PDFDownloadManager = st.session_state.manager
    manager.download(doi, progress=False)  # the UI shows its own counters
    st.session_state.current_index = i + 1


def _finalize_outputs():
    if st.session_state.results_saved:
        return
    manager: PDFDownloadManager = st.session_state.manager
    if manager is None:
        return
    # Save CSV summary
    manager.save_results_to_csv()
    _build_zips(st.session_state.get("n_splits", 1))
    st.session_state.zip_ready = True
    st.session_state.results_saved = True


def _build_zips(n_splits: int):
    """Split the processed DOIs into *n_splits* ZIPs (PDFs + summary per part)."""
    run_dir = Path(st.session_state.run_dir)
    zip_dir = run_dir / "zips"
    shutil.rmtree(zip_dir, ignore_errors=True)
    processed = [r["doi"] for r in st.session_state.manager.results]
    paths = zip_downloads_split(
        run_dir, n_splits=n_splits, zip_name="paper_hunter_results", dois=processed, out_dir=zip_dir
    )
    st.session_state.zip_parts = [(p.name, p.read_bytes()) for p in paths]
    st.session_state.zip_parts_n = n_splits


@st.cache_data(show_spinner=False)
def _parse_csv(data: bytes):
    """Return (detected column, DOIs) for an uploaded CSV. Cached so reruns do not re-parse."""
    table = read_doi_table(io.BytesIO(data))
    column = find_doi_column(table)
    return str(column), clean_dois(table[column])


def _reset():
    # Clean temp directory
    run_dir = st.session_state.get("run_dir")
    if run_dir:
        try:
            shutil.rmtree(run_dir, ignore_errors=True)
        except Exception:
            pass
    # Clear state
    for k in [
        "uploaded_file",
        "dois",
        "total",
        "current_index",
        "running",
        "stop",
        "logs",
        "run_dir",
        "manager",
        "results_saved",
        "zip_ready",
        "zip_parts",
        "zip_parts_n",
    ]:
        if k == "logs":
            st.session_state[k] = []
        elif k in ("running", "stop", "results_saved", "zip_ready"):
            st.session_state[k] = False
        elif k in ("total", "current_index"):
            st.session_state[k] = 0
        else:
            st.session_state[k] = None


# --- UI ---
st.set_page_config(page_title="Paper Hunter", layout="centered")
_init_state()
_attach_logging()

st.title("Paper Hunter – Simple Downloader")
st.caption("Enter DOIs (one per line) or upload a CSV, then download the PDFs and a summary.")

# Sidebar: downloader options.
with st.sidebar:
    st.header("Options")
    st.checkbox(
        "Use headless browser fallback (nodriver)",
        key="use_browser",
        help=(
            "Downloads first try the fast, browserless path (requests plus an "
            "in-browser-free solver for Sci-Hub's proof-of-work gate). When that "
            "fails, a real headless Chrome instance driven by nodriver is used to "
            "get past JavaScript and bot challenges."
        ),
    )
    if browser_available():
        st.caption("Chrome/nodriver detected: browser fallback can run.")
    else:
        st.caption("Chrome/nodriver not detected: browser fallback is unavailable.")
    st.caption(
        "Sci-Hub mirrors are shuffled at runtime. Set the environment variables "
        "`PAPER_HUNTER_BROWSER=0` or `PAPER_HUNTER_HEADLESS=0` to disable the "
        "browser fallback or to watch it run."
    )

# Text input for DOIs (one per line)
doi_input = st.text_area(
    "Enter DOIs (one per line)",
    height=150,
    placeholder="10.1016/j.jclinepi.2022.01.014\n10.1038/nature12373\n10.1126/science.1234567",
    key="doi_text_input"
)

uploaded_csv = st.file_uploader(
    "…or upload a CSV file (the DOI column is detected automatically)",
    type=["csv", "tsv", "txt"],
    key="csv_upload",
)
csv_dois = []
if uploaded_csv is not None:
    try:
        csv_column, csv_dois = _parse_csv(uploaded_csv.getvalue())
        st.caption(f"Detected DOI column **{csv_column}**: {len(csv_dois)} DOI(s).")
    except Exception as e:
        st.error(f"Could not read DOIs from {uploaded_csv.name}: {e}")

n_splits = st.slider(
    "Split results into ZIP files",
    min_value=1,
    max_value=50,
    value=1,
    key="n_splits",
    help=(
        "The DOIs are divided into this many parts of about equal size, in input "
        "order. Each part is a separate ZIP with its PDFs and a summary CSV. "
        "You can change this after the run finishes."
    ),
)

# Re-split finished results when the slider changes; no need to download again.
if st.session_state.results_saved and st.session_state.zip_parts_n != n_splits:
    _build_zips(n_splits)

col1, col2, col3, col4 = st.columns(4)
with col1:
    run_clicked = st.button("Start Downloading", type="primary")
with col2:
    stop_clicked = st.button("Stop")
with col3:
    reset_clicked = st.button("Reset")
zip_parts = st.session_state.zip_parts or []
with col4:
    if st.session_state.zip_ready and len(zip_parts) == 1:
        st.download_button(
            label="Download Finally",
            data=zip_parts[0][1],
            file_name=zip_parts[0][0],
            mime="application/zip",
            key="download_final_zip",
        )
    else:
        st.button(
            "Download Finally",
            disabled=True,
            key="download_final_disabled",
            help="When the results are split, download each part below." if len(zip_parts) > 1 else None,
        )

if st.session_state.zip_ready and len(zip_parts) > 1:
    st.write(f"Results split into {len(zip_parts)} ZIP files:")
    part_cols = st.columns(min(len(zip_parts), 5))
    for i, (name, data) in enumerate(zip_parts):
        with part_cols[i % len(part_cols)]:
            st.download_button(
                label=f"Part {i + 1}/{len(zip_parts)}",
                data=data,
                file_name=name,
                mime="application/zip",
                key=f"download_part_{i}",
            )


# Handle actions
if reset_clicked:
    _reset()
    st.rerun()

if stop_clicked and st.session_state.running:
    st.session_state.stop = True

if run_clicked:
    if not (doi_input and doi_input.strip()) and not csv_dois:
        st.warning("Please enter at least one DOI or upload a CSV file.")
    else:
        # One DOI per line plus the uploaded CSV; blank lines, non-DOI values
        # and duplicates are skipped, and doi.org URLs / "doi:" prefixes work.
        dois = clean_dois([*(doi_input or "").splitlines(), *csv_dois])

        if not dois:
            st.error("No valid DOIs found. DOIs should start with '10.' (e.g., 10.1016/j.jclinepi.2022.01.014)")
            st.stop()

        st.session_state.logs = []
        _prepare_manager(dois)
        st.info(f"Loaded {len(dois)} DOI(s). Starting downloads…")
        st.rerun()


# Progress and logs
total = st.session_state.total
idx = st.session_state.current_index
running = st.session_state.running
stop_flag = st.session_state.stop

# Metrics: successes / failures / remaining
succ = fail = 0
if st.session_state.manager is not None:
    res = getattr(st.session_state.manager, "results", []) or []
    succ = sum(1 for r in res if r.get("success") is True)
    fail = sum(1 for r in res if r.get("success") is False)
remaining = max(total - (succ + fail), 0)

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Success", succ)
with m2:
    st.metric("Failed", fail)
with m3:
    st.metric("Remaining", remaining)

if total > 0:
    pct = int(100 * idx / total)
    st.progress(pct, text=f"Progress: {idx}/{total}")

log_text = "\n".join(st.session_state.logs[-500:])  # keep last 500 lines
st.text_area("Logs", value=log_text, height=240)


# Incremental processing
if running and not stop_flag and idx < total:
    _process_one()
    # Trigger rerun to respond to Stop promptly and update UI
    st.rerun()

# Finalization when done or stopped
if total > 0 and (idx >= total or stop_flag) and not st.session_state.results_saved:
    _finalize_outputs()
    # Rerun so the 'Download Finally' button becomes active immediately
    st.rerun()
