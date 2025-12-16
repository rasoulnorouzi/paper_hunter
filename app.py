import io
import logging
import shutil
import uuid
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pandas as pd
import streamlit as st

from plugins_class import (
    PDFDownloadManager,
    UnpaywallDownloader,
    CrossrefDownloader,
    SciHubDownloader,
)
from utility import headers, scihub_mirrors


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
    st.session_state.setdefault("zip_bytes", None)


class StreamlitLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        st.session_state.logs.append(msg)


def _attach_logging():
    # Configure root logger once per run
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers across reruns
    if not any(isinstance(h, StreamlitLogHandler) for h in logger.handlers):
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
        CrossrefDownloader(headers=headers, download_dir=run_dir),
        SciHubDownloader(headers=headers, download_dir=run_dir, mirrors=scihub_mirrors),
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
    st.session_state.zip_bytes = None


def _process_one():
    # Process exactly one DOI per rerun to keep UI responsive (supports Stop)
    i = st.session_state.current_index
    if i >= st.session_state.total:
        return

    doi = st.session_state.dois[i]
    manager: PDFDownloadManager = st.session_state.manager
    manager.download(doi)
    st.session_state.current_index = i + 1


def _finalize_outputs():
    if st.session_state.results_saved:
        return
    manager: PDFDownloadManager = st.session_state.manager
    if manager is None:
        return
    # Save CSV summary
    manager.save_results_to_csv()

    # Build ZIP in-memory for download
    run_dir = Path(st.session_state.run_dir)
    zip_buffer = io.BytesIO()
    with ZipFile(zip_buffer, mode="w", compression=ZIP_DEFLATED) as zf:
        # Add all PDFs
        for pdf in run_dir.glob("*.pdf"):
            zf.write(pdf, arcname=pdf.name)
        # Add CSV report if present
        csv_path = run_dir / "download_summary.csv"
        if csv_path.exists():
            zf.write(csv_path, arcname=csv_path.name)
    zip_buffer.seek(0)

    st.session_state.zip_bytes = zip_buffer.read()
    st.session_state.zip_ready = True
    st.session_state.results_saved = True


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
        "zip_bytes",
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
st.caption("Enter DOIs (one per line), download PDFs and a summary.")

# Text input for DOIs (one per line)
doi_input = st.text_area(
    "Enter DOIs (one per line)",
    height=150,
    placeholder="10.1016/j.jclinepi.2022.01.014\n10.1038/nature12373\n10.1126/science.1234567",
    key="doi_text_input"
)

col1, col2, col3, col4 = st.columns(4)
with col1:
    run_clicked = st.button("Start Downloading", type="primary")
with col2:
    stop_clicked = st.button("Stop")
with col3:
    reset_clicked = st.button("Reset")
with col4:
    download_ready = st.session_state.zip_ready and st.session_state.zip_bytes is not None
    if download_ready:
        st.download_button(
            label="Download Finally",
            data=st.session_state.zip_bytes,
            file_name="paper_hunter_results.zip",
            mime="application/zip",
            key="download_final_zip",
        )
    else:
        st.button("Download Finally", disabled=True, key="download_final_disabled")


# Handle actions
if reset_clicked:
    _reset()
    st.rerun()

if stop_clicked and st.session_state.running:
    st.session_state.stop = True

if run_clicked:
    if not doi_input or not doi_input.strip():
        st.warning("Please enter at least one DOI.")
    else:
        # Parse DOIs from text input (one per line)
        lines = doi_input.strip().split('\n')
        dois = []
        for line in lines:
            # Clean each line and extract DOI
            cleaned = line.strip()
            if cleaned:
                # Handle various DOI formats (URLs, doi: prefix, etc.)
                # Extract DOI pattern if present
                import re
                doi_match = re.search(r'(10\.\d{4,9}/[^\s]+)', cleaned)
                if doi_match:
                    dois.append(doi_match.group(1))
                elif cleaned.startswith('10.'):
                    dois.append(cleaned)
        
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
