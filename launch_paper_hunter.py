"""Paper Hunter - one-click launcher for Windows, macOS and Linux.

Creates a virtual environment (myenv), installs Paper Hunter into it, starts the
web UI and opens it in your browser. The start_windows.bat / start_macos.command /
start_linux.sh files find or install Python and then run this script. It can
also be run directly:

    python launch_paper_hunter.py      (Windows)
    python3 launch_paper_hunter.py     (macOS / Linux)
"""
import hashlib
import os
import platform
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 11)
HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, "myenv")
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe") if IS_WINDOWS else os.path.join(VENV_DIR, "bin", "python")
# Records which pyproject.toml was installed, so later launches can skip pip.
STAMP = os.path.join(VENV_DIR, ".paper_hunter_installed")


def info(msg):
    print("[INFO] " + msg, flush=True)


def fail(msg, hint=""):
    print("[ERROR] " + msg)
    if hint:
        print("        " + hint)
    # Keep the window open when the launcher was started by double-clicking.
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass
    sys.exit(1)


def python_hint():
    if IS_WINDOWS:
        return "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and try again."
    if IS_MAC:
        return "Install it from https://www.python.org/downloads/ (or: brew install python) and try again."
    return "Install it with your package manager (Debian/Ubuntu: sudo apt install python3 python3-venv) and try again."


def find_newer_python():
    """Return a command for a Python >= MIN_PYTHON other than the running one, or None."""
    names = ["python3.13", "python3.12", "python3.11", "python3", "python"]
    candidates = [["py", "-3"]] + [[n] for n in names] if IS_WINDOWS else [[n] for n in names]
    check = "import sys; sys.exit(sys.version_info < {})".format(MIN_PYTHON)
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            if subprocess.run(cmd + ["-c", check], capture_output=True).returncode == 0:
                return cmd
        except OSError:
            continue
    return None


def ensure_python_version():
    if sys.version_info >= MIN_PYTHON:
        return
    newer = find_newer_python()
    if newer:
        info("Python {}.{} is too old; restarting with {}.".format(sys.version_info[0], sys.version_info[1], " ".join(newer)))
        sys.exit(subprocess.call(newer + [os.path.abspath(__file__)] + sys.argv[1:]))
    fail("Python {}.{} or newer is required (this is {}.{}).".format(*(MIN_PYTHON + sys.version_info[:2])), python_hint())


def venv_works():
    if not os.path.exists(VENV_PY):
        return False
    return subprocess.run([VENV_PY, "-m", "pip", "--version"], capture_output=True).returncode == 0


def create_venv():
    if os.path.exists(VENV_DIR):
        print("[WARN] The existing virtual environment is broken; recreating it.")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    info('Creating Python virtual environment at "{}"...'.format(VENV_DIR))
    if subprocess.call([sys.executable, "-m", "venv", VENV_DIR]) != 0 or not os.path.exists(VENV_PY):
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        if shutil.which("apt-get"):
            # Debian/Ubuntu ship venv support as a separate package.
            fail("Could not create the virtual environment.",
                 "Install venv support with: sudo apt install python{}.{}-venv   then run this launcher again.".format(*sys.version_info[:2]))
        fail("Could not create the virtual environment.")


def main():
    print("================================================")
    print("  Paper Hunter Downloader")
    print("  Preparing environment and starting app...")
    print("================================================\n")
    os.chdir(HERE)
    ensure_python_version()

    if not venv_works():
        create_venv()

    pyproject = os.path.join(HERE, "pyproject.toml")
    if not os.path.exists(pyproject):
        fail('Could not find pyproject.toml in "{}".'.format(HERE), "Please verify the repository contents.")
    with open(pyproject, "rb") as f:
        wanted = hashlib.sha256(f.read()).hexdigest()
    installed = open(STAMP).read().strip() if os.path.exists(STAMP) else ""

    if installed != wanted:
        info("Upgrading pip in the virtual environment...")
        subprocess.call([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"])
        info("Installing Paper Hunter and its packages (first run takes a few minutes)...")
        # Editable install: later changes to the code are used without reinstalling.
        if subprocess.call([VENV_PY, "-m", "pip", "install", "-e", HERE]) != 0:
            fail("Installing the required packages failed.")
        with open(STAMP, "w") as f:
            f.write(wanted)

    info("Starting Paper Hunter; it opens in your browser. Close this window to quit.")
    try:
        sys.exit(subprocess.call([VENV_PY, "-m", "paper_hunter"] + sys.argv[1:]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        fail("Paper Hunter stopped because of the error above.")
