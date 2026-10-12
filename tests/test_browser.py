import paper_hunter.browser as browser


def test_safe_name_sanitizes_dois():
    assert browser._safe_name("10.1038/nphys1170") == "10.1038_nphys1170"
    assert browser._safe_name("https://sci-hub.st/10.1000/a b?c") == "https_sci-hub.st_10.1000_a_b_c"


def test_safe_name_is_truncated():
    assert len(browser._safe_name("x" * 500)) <= 120


def test_env_flag_defaults(monkeypatch):
    monkeypatch.delenv("PAPER_HUNTER_SAVE_SCREENSHOTS", raising=False)
    assert browser.screenshots_enabled() is True
    monkeypatch.delenv("PAPER_HUNTER_PRINT_TO_PDF", raising=False)
    assert browser.print_to_pdf_enabled() is False


def test_env_flag_off_values(monkeypatch):
    for value in ("0", "false", "no", "off", "OFF", " 0 "):
        monkeypatch.setenv("PAPER_HUNTER_SAVE_SCREENSHOTS", value)
        assert browser.screenshots_enabled() is False


def test_sandbox_defaults_on(monkeypatch):
    monkeypatch.delenv("PAPER_HUNTER_SANDBOX", raising=False)
    assert browser.sandbox_enabled() is True
    monkeypatch.setenv("PAPER_HUNTER_SANDBOX", "0")
    assert browser.sandbox_enabled() is False


def test_profile_and_cookies_file_are_optional(monkeypatch):
    monkeypatch.delenv("PAPER_HUNTER_PROFILE_DIR", raising=False)
    monkeypatch.delenv("PAPER_HUNTER_COOKIES_FILE", raising=False)
    assert browser.profile_dir() is None
    assert browser.cookies_file() is None

    monkeypatch.setenv("PAPER_HUNTER_PROFILE_DIR", "/tmp/ph_profile")
    monkeypatch.setenv("PAPER_HUNTER_COOKIES_FILE", "/tmp/ph_cookies.dat")
    assert browser.profile_dir() == "/tmp/ph_profile"
    assert browser.cookies_file() == "/tmp/ph_cookies.dat"


def test_default_browser_args_have_no_no_sandbox():
    assert "--no-sandbox" not in browser._DEFAULT_BROWSER_ARGS
