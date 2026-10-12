from zipfile import ZipFile

import pandas as pd

from paper_hunter import zip_downloads, zip_downloads_split
from paper_hunter.sources import PDFDownloadManager

from conftest import FakeManager, FakeDownloader

DOIS = ["10.1000/a1", "10.1000/b2missing", "10.1000/c3", "10.1000/d4", "10.1000/e5missing"]


def run_fake_manager(tmp_path, dois=DOIS, **kwargs):
    manager = PDFDownloadManager([FakeDownloader(headers={}, download_dir=tmp_path)], download_dir=tmp_path)
    manager.download(dois, **kwargs)
    manager.save_results_to_csv()
    return manager


def test_manager_records_source_file_and_counts(tmp_path, capsys):
    manager = run_fake_manager(tmp_path)
    out = capsys.readouterr().out

    assert [r["success"] for r in manager.results] == [True, False, True, True, False]
    assert manager.results[0] == {"doi": "10.1000/a1", "success": True, "source": "Fake", "file": "10.1000_a1.pdf"}
    assert manager.results[1]["source"] == "" and manager.results[1]["file"] == ""
    assert "[1/5] succeeded 0 | failed 0 | remaining 5" in out
    assert "[3/5] succeeded 1 | failed 1 | remaining 3" in out
    assert "Finished 5/5 DOI(s): succeeded 3 (60.0%) | failed 2 -> 10.1000/b2missing, 10.1000/e5missing" in out

    summary = pd.read_csv(tmp_path / "download_summary.csv")
    assert list(summary.columns) == ["doi", "success", "source", "file"]
    assert summary["success"].sum() == 3


def test_manager_should_stop(tmp_path, capsys):
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2  # allow two DOIs, then stop

    manager = run_fake_manager(tmp_path, should_stop=should_stop)
    assert len(manager.results) == 2
    assert "Finished 2/5 DOI(s)" in capsys.readouterr().out


def test_progress_can_be_silenced(tmp_path, capsys):
    run_fake_manager(tmp_path, progress=False)
    assert capsys.readouterr().out == ""


def test_zip_downloads_split(tmp_path):
    downloads = tmp_path / "papers"
    run_fake_manager(downloads)

    zips = zip_downloads_split(downloads, n_splits=2, zip_name="papers", dois=DOIS, out_dir=tmp_path / "zips")
    assert [z.name for z in zips] == ["papers_part1_of_2.zip", "papers_part2_of_2.zip"]

    with ZipFile(zips[0]) as zf:
        assert sorted(zf.namelist()) == ["10.1000_a1.pdf", "10.1000_c3.pdf", "download_summary.csv"]
        rows = zf.read("download_summary.csv").decode().splitlines()
        assert rows == ["doi,success", "10.1000/a1,True", "10.1000/b2missing,False", "10.1000/c3,True"]
    with ZipFile(zips[1]) as zf:
        assert sorted(zf.namelist()) == ["10.1000_d4.pdf", "download_summary.csv"]


def test_zip_downloads_split_defaults_to_summary_dois(tmp_path):
    downloads = tmp_path / "papers"
    run_fake_manager(downloads)
    zips = zip_downloads_split(downloads, n_splits=10, zip_name="all")
    assert len(zips) == len(DOIS)  # clamped to one DOI per part


def test_zip_downloads_single(tmp_path):
    downloads = tmp_path / "papers"
    run_fake_manager(downloads)
    zip_path = zip_downloads(downloads, zip_name="everything")
    with ZipFile(zip_path) as zf:
        assert len([n for n in zf.namelist() if n.endswith(".pdf")]) == 3
        assert "download_summary.csv" in zf.namelist()


def test_fake_manager_matches_real_result_shape(tmp_path):
    fake = FakeManager(tmp_path / "fake")
    fake.download(DOIS)
    real = run_fake_manager(tmp_path / "real")
    assert [set(r) for r in fake.results] == [set(r) for r in real.results]
