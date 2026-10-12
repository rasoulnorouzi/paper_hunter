import time
from zipfile import ZipFile
import io

import pytest
from fastapi.testclient import TestClient

from paper_hunter.server import create_app

from conftest import FakeManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("paper_hunter.server.make_manager", lambda run_dir, use_browser=True: FakeManager(run_dir))
    return TestClient(create_app(tmp_path / "runs"))


def wait_until_finished(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("done", "stopped", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_page_and_static_files(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>Paper Hunter</title>" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_info(client):
    info = client.get("/api/info").json()
    assert set(info) >= {"version", "browser_available", "runs_dir", "current_job"}
    assert info["current_job"] is None


def test_parse_text_skips_blank_rows(client):
    text = "10.1093/humrep/dex273\n\n   \n\t\nhttps://doi.org/10.1093/humrep/dex273\n10.3390/nu14071428\n"
    assert client.post("/api/parse", json={"text": text}).json() == {"count": 2, "column": None}


def test_parse_csv_detects_column(client):
    csv = "Title;Article DOI\nA;10.1093/humrep/dex273\nB;   \n\nC;10.3390/nu14071428\n"
    assert client.post("/api/parse", json={"csv": csv}).json() == {"count": 2, "column": "Article DOI"}


def test_parse_csv_without_dois(client):
    response = client.post("/api/parse", json={"csv": "a,b\n1,2\n"})
    assert response.status_code == 400
    assert "No column containing DOIs" in response.json()["detail"]


def test_start_without_dois(client):
    response = client.post("/api/jobs", json={"text": "  \n nothing here \n"})
    assert response.status_code == 400


def test_unknown_job(client):
    assert client.get("/api/jobs/run_does_not_exist").status_code == 404


def test_full_run_with_split_zips(client):
    text = "10.1000/a1\n\n10.1000/b2missing\n   \n10.1000/c3\n"
    job = client.post("/api/jobs", json={"text": text, "use_browser": False, "parts": 2}).json()
    assert job["total"] == 3

    job = wait_until_finished(client, job["id"])
    assert (job["state"], job["done"], job["succeeded"], job["failed"]) == ("done", 3, 2, 1)
    assert [z["name"].rsplit("_", 3)[-3:] for z in job["zips"]] == [["part1", "of", "2.zip"], ["part2", "of", "2.zip"]]
    assert [r["source"] for r in job["results"]] == ["Fake", "", "Fake"]
    assert len(client.get(f"/api/jobs/{job['id']}?since=2").json()["results"]) == 1

    summary = client.get(f"/api/jobs/{job['id']}/summary.csv")
    assert summary.status_code == 200
    assert summary.text.splitlines()[0] == "doi,success,source,file"

    files = client.get(f"/api/jobs/{job['id']}/zips?parts=3").json()
    assert len(files) == 3
    first = client.get(files[0]["url"])
    assert first.status_code == 200
    with ZipFile(io.BytesIO(first.content)) as zf:
        assert "download_summary.csv" in zf.namelist()


def test_zip_download_rejects_other_paths(client):
    job = client.post("/api/jobs", json={"text": "10.1000/a1"}).json()
    wait_until_finished(client, job["id"])
    client.get(f"/api/jobs/{job['id']}/zips?parts=1")
    assert client.get(f"/api/jobs/{job['id']}/zips/1/..%2Fdownload_summary.csv").status_code == 404


@pytest.fixture
def slow_client(tmp_path, monkeypatch):
    """A client whose fake downloader takes a moment per DOI, so runs can be paused and stopped."""

    def slow_manager(run_dir, use_browser=True):
        manager = FakeManager(run_dir)
        original = manager.download

        def download(dois, progress=True, should_stop=None):
            def slow_stop():
                time.sleep(0.02)
                return should_stop()
            original(dois, progress, slow_stop)

        manager.download = download
        return manager

    monkeypatch.setattr("paper_hunter.server.make_manager", slow_manager)
    return TestClient(create_app(tmp_path / "runs"))


def start_long_run(client, n=300):
    dois = "\n".join(f"10.1000/x{i}" for i in range(n))
    return client.post("/api/jobs", json={"text": dois}).json()["id"]


def wait_for_state(client, job_id, state, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] == state:
            return job
        time.sleep(0.02)
    raise AssertionError(f"run never reached state {state!r}")


def wait_for_progress(client, job_id, at_least=3, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.get(f"/api/jobs/{job_id}").json()["done"] >= at_least:
            return
        time.sleep(0.02)
    raise AssertionError("no progress")


def test_stop_makes_the_zip(slow_client):
    job_id = start_long_run(slow_client)
    wait_for_progress(slow_client, job_id)
    slow_client.post(f"/api/jobs/{job_id}/stop")
    job = wait_until_finished(slow_client, job_id)
    assert job["state"] == "stopped"
    assert 0 < job["done"] < 300
    assert len(job["zips"]) == 1
    assert slow_client.get(job["zips"][0]["url"]).status_code == 200


def test_pause_and_resume(slow_client):
    job_id = start_long_run(slow_client, n=40)
    wait_for_progress(slow_client, job_id)
    slow_client.post(f"/api/jobs/{job_id}/pause")
    paused = wait_for_state(slow_client, job_id, "paused")

    time.sleep(0.3)  # nothing happens while paused
    assert slow_client.get(f"/api/jobs/{job_id}").json()["done"] == paused["done"]
    assert slow_client.post("/api/jobs", json={"text": "10.1000/other"}).status_code == 409

    slow_client.post(f"/api/jobs/{job_id}/resume")
    job = wait_until_finished(slow_client, job_id)
    assert (job["state"], job["done"]) == ("done", 40)
    assert len(job["zips"]) == 1


def test_stop_while_paused(slow_client):
    job_id = start_long_run(slow_client)
    wait_for_progress(slow_client, job_id)
    slow_client.post(f"/api/jobs/{job_id}/pause")
    paused = wait_for_state(slow_client, job_id, "paused")
    slow_client.post(f"/api/jobs/{job_id}/stop")
    job = wait_until_finished(slow_client, job_id)
    assert job["state"] == "stopped"
    assert job["done"] == paused["done"]
    assert len(job["zips"]) == 1
