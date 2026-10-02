@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo   SmartCooling Controller - Windows build
echo ============================================================
echo.

rem --- 0. persistent caches so the big wheels download once ----
rem The Qt/numpy wheels are ~150 MB.  Keep a repo-local pip cache and a
rem wheelhouse beside the project so a second build is fast and offline.
set "PIP_CACHE_DIR=%CD%\.pip-cache"
set "WHEELHOUSE=%CD%wheelhouse"

rem --- 1. locate a supported interpreter (3.11 - 3.14) ----------
rem Flat structure on purpose: nested parenthesised blocks are
rem fragile across cmd.exe versions, so each probe is a top-level
rem line and control leaves via "goto" as soon as one succeeds.
set "PYCMD="

py -3.14 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.14"
if defined PYCMD goto found_python

py -3.13 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.13"
if defined PYCMD goto found_python

py -3.12 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.12"
if defined PYCMD goto found_python

py -3.11 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.11"
if defined PYCMD goto found_python

python -c "import sys; sys.exit(0 if 0x030b00f0 <= sys.hexversion < 0x030f0000 else 1)" >nul 2>&1 && set "PYCMD=python"
if defined PYCMD goto found_python

goto no_python

:found_python
echo [1/4] Interpreter: %PYCMD%
%PYCMD% --version
echo.

rem --- 2. virtual environment (reused when already present) -----
if not exist ".venv\Scripts\python.exe" goto make_venv
echo [2/4] Reusing existing .venv
goto have_venv

:make_venv
echo [2/4] Creating virtual environment (.venv)...
%PYCMD% -m venv .venv
if errorlevel 1 goto fail_venv

:have_venv
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
echo.

rem --- 3. pinned dependencies, cache + wheelhouse first ---------
echo [3/4] Installing pinned dependencies (cache: %PIP_CACHE_DIR%)...
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"

rem Fast path: if everything is already importable, skip the network entirely.
"%VENV_PY%" -c "import PySide6, pyqtgraph, psutil, websockets, pydantic, cryptography, PyInstaller, pytest" >nul 2>&1
if not errorlevel 1 goto deps_ok
echo       cache miss - resolving dependencies

rem Try the local wheelhouse first so a re-run needs no network at all.
if not exist "%WHEELHOUSE%" goto pip_online
"%VENV_PY%" -m pip install --no-index --find-links "%WHEELHOUSE%" -r requirements-build.txt
if not errorlevel 1 goto deps_ok
echo       wheelhouse incomplete - fetching what is missing

:pip_online
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto fail_pip

rem Download every wheel into the wheelhouse once, then install from it.
"%VENV_PY%" -m pip download -r requirements-build.txt -d "%WHEELHOUSE%"
if errorlevel 1 goto fail_deps

"%VENV_PY%" -m pip install --no-index --find-links "%WHEELHOUSE%" -r requirements-build.txt
if errorlevel 1 goto fail_deps

:deps_ok
echo       dependencies ready
"%VENV_PY%" -m pip check
if errorlevel 1 goto fail_deps
echo.

rem --- 4. build: lint + tests + freeze + verify + package ------
rem The release pipeline runs the build once and then the optional
rem Inno Setup step, release notes and checksums.  It is called with
rem --skip-installer so a machine without Inno Setup 6 still finishes
rem with a verified, runnable bundle instead of stopping at a missing
rem compiler.
rem
rem Guard against a stale checkout: if the scripts folder is older than
rem this launcher it predates the fixes above, and the build would fail
rem with a confusing traceback.  Detect it here and say so plainly.
findstr /C:"--skip-installer" "scripts\package\release.py" >nul 2>&1
if errorlevel 1 goto stale_tree

echo [4/4] Building (lint, tests, freeze, verify)...
"%VENV_PY%" scripts\package\release.py --release --skip-installer
if errorlevel 1 goto fail_build

echo.
echo ============================================================
echo   BUILD COMPLETE
echo ============================================================
echo   Executable:
echo     %CD%\build\dist\SmartCoolingController\SmartCoolingController.exe
echo   Manifest:
echo     %CD%\build\manifest.json
echo.
echo   For a full one-click installer (setup.exe) install Inno
echo   Setup 6 from https://jrsoftware.org/isdl.php, then run:
echo     "%VENV_PY%" scripts\installer\build_installer.py --release
echo.
pause
exit /b 0

:stale_tree
echo.
echo ============================================================
echo   [ERROR] THIS IS AN OUTDATED COPY OF THE SOURCE
echo ============================================================
echo   build_windows.bat is newer than the scripts\ folder next to it,
echo   so this folder still contains the old, unfixed build scripts.
echo   Running it would fail with a "FileNotFoundError: git" traceback
echo   even though nothing is actually wrong with your machine.
echo.
echo   Fix: delete this folder and unpack the latest source archive
echo   again, then run build_windows.bat from the fresh copy.
echo.
pause
exit /b 1

:no_python
echo [ERROR] No supported Python found.
echo.
echo   Install 64-bit Python 3.11 - 3.14 from
echo   https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH" during setup.
echo.
echo   Your current "python" is:
python --version 2>nul || echo   ^(python is not on PATH^)
echo.
pause
exit /b 1

:fail_venv
echo [ERROR] venv creation failed.
pause
exit /b 1

:fail_pip
echo [ERROR] pip upgrade failed.
pause
exit /b 1

:fail_deps
echo [ERROR] dependency install failed.
pause
exit /b 1

:fail_build
echo [ERROR] build failed.
pause
exit /b 1
