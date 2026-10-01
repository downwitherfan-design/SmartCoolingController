@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo   SmartCooling Controller - Windows build
echo ============================================================
echo.

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

rem --- 2. virtual environment -----------------------------------
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

rem --- 3. pinned dependencies -----------------------------------
echo [3/4] Installing pinned dependencies...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto fail_pip
"%VENV_PY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto fail_deps
echo.

rem --- 4. build: lint + tests + freeze + verify -----------------
echo [4/4] Building (lint, tests, freeze, verify)...
"%VENV_PY%" scripts\build\build.py --release
if errorlevel 1 goto fail_build

echo.
echo ============================================================
echo   BUILD COMPLETE
echo ============================================================
echo   Executable:
echo     %CD%\build\dist\SmartCoolingController\SmartCoolingController.exe
echo.
echo   Optional installer (needs Inno Setup 6):
echo     "%VENV_PY%" scripts\package\release.py --release
echo.
pause
exit /b 0

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
