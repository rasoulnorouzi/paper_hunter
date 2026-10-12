import pytest

from paper_hunter import clean_dois, doi_to_filename, extract_doi, load_dois_from_csv, split_dois


@pytest.mark.parametrize(
    "value, expected",
    [
        ("10.1038/nphys1170", "10.1038/nphys1170"),
        ("  10.1038/nphys1170  ", "10.1038/nphys1170"),
        ("doi:10.1038/nphys1170", "10.1038/nphys1170"),
        ("https://doi.org/10.1038/nphys1170", "10.1038/nphys1170"),
        ("https://doi.org/10.3389/fpsyg.2021.654336.", "10.3389/fpsyg.2021.654336"),
        ("", None),
        ("   ", None),
        ("\t", None),
        (None, None),
        (float("nan"), None),
        ("N/A", None),
    ],
)
def test_extract_doi(value, expected):
    assert extract_doi(value) == expected


def test_clean_dois_skips_blank_rows_and_duplicates():
    rows = ["10.1093/humrep/dex273", "", "   ", "\t", None, " 10.3390/nu14071428 ", "10.1093/HUMREP/DEX273", "not a doi"]
    assert clean_dois(rows) == ["10.1093/humrep/dex273", "10.3390/nu14071428"]


@pytest.mark.parametrize("n, sizes", [(1, [7]), (3, [3, 2, 2]), (7, [1] * 7), (20, [1] * 7)])
def test_split_dois(n, sizes):
    parts = split_dois(list(range(7)), n)
    assert [len(p) for p in parts] == sizes
    assert [x for p in parts for x in p] == list(range(7))


def test_split_dois_empty():
    assert split_dois([], 3) == [[]]


def test_doi_to_filename():
    assert doi_to_filename("10.1016/S1473-3099(16)30082-2") == "10.1016_S1473-3099(16)30082-2"


def write(tmp_path, name, text, newline="\n"):
    path = tmp_path / name
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def test_csv_with_blank_and_space_rows(tmp_path):
    path = write(tmp_path, "dois.csv", "doi\n10.1093/humrep/dex273\n\n   \n\t\n  10.1371/journal.pbio.2002173  \n\n10.3390/nu14071428\n")
    assert load_dois_from_csv(path) == ["10.1093/humrep/dex273", "10.1371/journal.pbio.2002173", "10.3390/nu14071428"]


def test_csv_semicolon_crlf_and_named_column(tmp_path):
    text = "Title;Article DOI;Year\nA;https://doi.org/10.1093/humrep/dex273;2017\n;;\nB;   ;2018\nC; doi:10.3390/nu14071428 ;2022\n"
    path = write(tmp_path, "dois.csv", text, newline="\r\n")
    assert load_dois_from_csv(path) == ["10.1093/humrep/dex273", "10.3390/nu14071428"]


def test_csv_detects_column_without_doi_in_name(tmp_path):
    path = write(tmp_path, "refs.csv", "title,ref,year\nA,10.1093/humrep/dex273,1\nB,,2\n")
    assert load_dois_from_csv(path) == ["10.1093/humrep/dex273"]


def test_csv_without_header(tmp_path):
    path = write(tmp_path, "plain.csv", "10.1093/humrep/dex273\n\n10.3390/nu14071428\n")
    assert load_dois_from_csv(path) == ["10.1093/humrep/dex273", "10.3390/nu14071428"]


def test_csv_with_utf8_bom(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("\ufeffdoi\n10.1093/humrep/dex273\n".encode("utf-8"))
    assert load_dois_from_csv(path) == ["10.1093/humrep/dex273"]


def test_csv_explicit_column(tmp_path):
    path = write(tmp_path, "two.csv", "doi,other_doi\n10.1093/humrep/dex273,10.3390/nu14071428\n")
    assert load_dois_from_csv(path, column="other_doi") == ["10.3390/nu14071428"]


def test_csv_without_dois_raises(tmp_path):
    path = write(tmp_path, "none.csv", "a,b\n1,2\n")
    with pytest.raises(ValueError):
        load_dois_from_csv(path)
