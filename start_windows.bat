@echo off
rem Paper Hunter starter for Windows: double-click this file.
rem Finds Python 3.11+ (or installs it), then runs launch_paper_hunter.py,
rem which sets everything up and opens Paper Hunter in your browser.
setlocal
title Paper Hunter
cd /d "%~dp0"

set "PY="
for %%V in (3.13 3.12 3.11) do if not defined PY call :try py -%%V
if not defined PY call :try py -3
if not defined PY call :try python
if not defined PY call :install_python
if not defined PY (
    echo.
    echo Python could not be installed automatically.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH"^), then double-click this file again.
    pause
    exit /b 1
)

%PY% launch_paper_hunter.py %*
exit /b


:try
rem Use the command in %* if it is Python 3.11 or newer.
%* -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul && set "PY=%*"
exit /b


:install_python
echo Python 3.11 or newer was not found. Installing it now (one time only)...
set "UV=uv"
where uv >nul 2>nul || (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
"%UV%" python install 3.13 || exit /b
for /f "delims=" %%P in ('call "%UV%" python find 3.13') do set PY="%%P"
exit /b
