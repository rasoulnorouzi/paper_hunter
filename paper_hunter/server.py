"""Local web UI: a small FastAPI app that runs one download job at a time in the background.

API (all JSON unless noted):

    GET  /api/info                        browser fallback availability, runs folder, current job
    POST /api/parse                       {text, csv} -> how many DOIs were found (and the CSV column)
    POST /api/jobs                        {text, csv, use_browser} -> start a run
    GET  /api/jobs/{id}?since=N           progress, results from index N on, recent log lines
    POST /api/jobs/{id}/stop              stop after the current DOI
    GET  /api/jobs/{id}/zips?parts=N      build N ZIP files -> their names and URLs
    GET  /api/jobs/{id}/zips/{n}/{name}   download one ZIP (file)
    GET  /api/jobs/{id}/summary.csv       download_summary.csv (file)
"""
import io
import logging
import os
import socket
import threading
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .browser import browser_available
from .core import make_manager, zip_downloads_split
from .dois import clean_dois, find_doi_column, read_doi_table

STATIC_DIR = Path(__file__).parent / "static"
FINISHED = ("done", "stopped", "error")


class DoiInput(BaseModel):
    text: str = ""
    csv: str = ""  # contents of an uploaded CSV file
    use_browser: bool = True


class Job:
    def __init__(self, dois: list[str], run_dir: Path, use_browser: bool):
        self.id = run_dir.name
        self.dois = dois
        self.run_dir = run_dir
        self.use_browser = use_browser
        self.state = "running"  # running | stopping | done | stopped | error
        self.error = ""
        self.log = deque(maxlen=300)
        self.manager = make_manager(run_dir, use_browser=use_browser)

    def run(self) -> None:
        try:
            self.manager.download(self.dois, progress=False, should_stop=lambda: self.state == "stopping")
            self.manager.save_results_to_csv()
            self.state = "stopped" if self.state == "stopping" else "done"
        except Exception as e:  # keep the server alive and show the error in the UI
            logging.exception("Run failed")
            self.error = str(e)
            self.state = "error"

    def snapshot(self, since: int = 0) -> dict:
        results = list(self.manager.results)
        succeeded = sum(r["success"] for r in results)
        return {
            "id": self.id,
            "state": self.state,
            "error": self.error,
            "folder": str(self.run_dir.resolve()),
            "total": len(self.dois),
            "done": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results[since:],
            "log": list(self.log),
        }


class _JobLogHandler(logging.Handler):
    """Copy log messages of the running job into its log, for the UI."""

    def __init__(self, server: "Server"):
        super().__init__()
        self.server = server
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        job = self.server.current
        if job is not None and job.state in ("running", "stopping"):
            job.log.append(self.format(record))


class Server:
    def __init__(self, runs_dir: str | Path = "paper_hunter_runs"):
        self.runs_dir = Path(runs_dir)
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None
        self.lock = threading.Lock()

    def job(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise HTTPException(404, "Unknown run.")
        return self.jobs[job_id]

    @staticmethod
    def collect_dois(data: DoiInput) -> tuple[list[str], str | None]:
        """DOIs from the pasted text plus the CSV (whose DOI column is detected)."""
        column = None
        csv_values: list = []
        if data.csv.strip():
            try:
                table = read_doi_table(io.StringIO(data.csv))
                column = find_doi_column(table)
            except Exception as e:
                raise HTTPException(400, f"Could not read DOIs from the CSV file: {e}")
            csv_values = list(table[column])
        return clean_dois([*data.text.splitlines(), *csv_values]), (str(column) if column is not None else None)

    def start(self, data: DoiInput) -> Job:
        dois, _ = self.collect_dois(data)
        if not dois:
            raise HTTPException(400, "No DOIs found. A DOI looks like 10.1038/nphys1170.")
        with self.lock:
            if self.current is not None and self.current.state not in FINISHED:
                raise HTTPException(409, "A run is already in progress.")
            run_dir = self.runs_dir / datetime.now().strftime("run_%Y%m%d_%H%M%S")
            run_dir.mkdir(parents=True, exist_ok=True)
            job = Job(dois, run_dir, data.use_browser)
            self.jobs[job.id] = job
            self.current = job
        threading.Thread(target=job.run, daemon=True).start()
        return job


def create_app(runs_dir: str | Path = "paper_hunter_runs") -> FastAPI:
    server = Server(runs_dir)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(_JobLogHandler(server))

    app = FastAPI(title="Paper Hunter", version=__version__)

    @app.get("/api/info")
    def info():
        return {
            "version": __version__,
            "browser_available": browser_available(),
            "runs_dir": str(server.runs_dir.resolve()),
            "current_job": server.current.id if server.current else None,
        }

    @app.post("/api/parse")
    def parse(data: DoiInput):
        dois, column = server.collect_dois(data)
        return {"count": len(dois), "column": column}

    @app.post("/api/jobs")
    def start(data: DoiInput):
        return server.start(data).snapshot()

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str, since: int = 0):
        return server.job(job_id).snapshot(since)

    @app.post("/api/jobs/{job_id}/stop")
    def stop(job_id: str):
        job = server.job(job_id)
        if job.state == "running":
            job.state = "stopping"
        return job.snapshot(len(job.manager.results))

    @app.get("/api/jobs/{job_id}/zips")
    def zips(job_id: str, parts: int = 1):
        job = server.job(job_id)
        if job.state not in FINISHED:
            raise HTTPException(409, "The run is still in progress.")
        processed = [r["doi"] for r in job.manager.results]
        if not processed:
            raise HTTPException(400, "Nothing was processed, so there is nothing to zip.")
        parts = max(1, min(parts, len(processed)))
        out_dir = job.run_dir / "zips" / str(parts)
        paths = sorted(out_dir.glob("*.zip")) or zip_downloads_split(
            job.run_dir, n_splits=parts, zip_name=f"paper_hunter_{job.id}", dois=processed, out_dir=out_dir
        )
        return [
            {"name": p.name, "size": p.stat().st_size, "url": f"/api/jobs/{job.id}/zips/{parts}/{p.name}"}
            for p in paths
        ]

    @app.get("/api/jobs/{job_id}/zips/{parts}/{name}")
    def zip_file(job_id: str, parts: int, name: str):
        job = server.job(job_id)
        path = job.run_dir / "zips" / str(parts) / name
        if Path(name).name != name or not path.is_file():
            raise HTTPException(404, "File not found.")
        return FileResponse(path, media_type="application/zip", filename=name)

    @app.get("/api/jobs/{job_id}/summary.csv")
    def summary(job_id: str):
        path = server.job(job_id).run_dir / "download_summary.csv"
        if not path.is_file():
            raise HTTPException(404, "No summary yet.")
        return FileResponse(path, media_type="text/csv", filename="download_summary.csv")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Like uvicorn, ignore ports that are only cooling down after a restart
            # (on Windows this option would allow sharing a port in use, so skip it).
            if os.name != "nt":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port between {start} and {start + 49}.")


def serve(port: int = 8765, open_browser: bool = True, runs_dir: str | Path = "paper_hunter_runs") -> None:
    """Start the web UI on http://127.0.0.1:<port> and open it in the browser."""
    import uvicorn

    port = _free_port(port)
    url = f"http://127.0.0.1:{port}"
    print(f"Paper Hunter is running at {url}  (press Ctrl+C to quit)", flush=True)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(runs_dir), host="127.0.0.1", port=port, log_level="warning")
