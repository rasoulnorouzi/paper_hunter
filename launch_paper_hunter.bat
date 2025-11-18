@echo off
setlocal

REM Paper Hunter - One-click launcher (venv + deps + app)
echo ================================================
echo   Paper Hunter Downloader

echo   Preparing environment and starting app...
echo ================================================
echo.

REM Change to the folder that contains this script
cd /d "%~dp0"

set "VENV_DIR=%~dp0myenv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%~dp0requirements.txt"
set "APP_ENTRY=%~dp0app.py"

if exist "%VENV_PY%" goto :VENV_READY

echo [INFO] Creating Python virtual environment at "%VENV_DIR%"...
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 -m venv "%VENV_DIR%"
) else (
    python -m venv "%VENV_DIR%"
)

:VENV_READY
if exist "%VENV_PY%" goto :VENV_OK
echo [ERROR] Could not find or create venv Python: %VENV_PY%
echo         Please install Python 3.9+ and try again.
pause
exit /b 1

:VENV_OK
echo [INFO] Upgrading pip in the virtual environment...
"%VENV_PY%" -m pip install --upgrade pip

echo [INFO] Installing required packages (this may take a moment)...
if exist "%REQ_FILE%" (
    "%VENV_PY%" -m pip install -r "%REQ_FILE%"
) else (
    echo [WARN] requirements.txt not found; skipping dependency install.
)

if not exist "%APP_ENTRY%" goto :NO_APP

echo [INFO] Launching Streamlit app...
"%VENV_PY%" -m streamlit run "%APP_ENTRY%"
goto :EOF

:NO_APP
echo [ERROR] Could not locate app.py at "%APP_ENTRY%".
echo         Please verify the repository contents.
pause
exit /b 1

:EOF
echo.
pause
endlocal
