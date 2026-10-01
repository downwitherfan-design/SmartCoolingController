@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   SmartCooling Controller - Windows build
echo ============================================================
echo.

rem --- 1. locate a supported interpreter (3.11 - 3.14) -------------
rem Try the py launcher first (newest supported version wins), then fall
rem back to whatever "python" resolves to on PATH.
set "PYCMD="
for %%V in (3.14 3.13 3.12 3.11) do (
    if not defined PYCMD (
        py -%%V -c "import sys" >nul 2>&1 && set "PYCMD=py -%%V"
    )
)
if not defined PYCMD (
    python -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,15) else 1)" >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] No supported Python found.
    echo.
    echo   Install 64-bit Python 3.11 - 3.14 from
    echo   https://www.python.org/downloads/ and tick
    echo   "Add python.exe to PATH" during setup.
    echo.
    echo   Your current "python" is:
    python --version 2>nul || echo   (python is not on PATH^)
    echo.
    pause
    exit /b 1
)
echo [1/4] Interpreter: %PYCMD%
%PYCMD% --version
echo.

rem --- 2. virtual environment -------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/4] Creating virtual environment (.venv)...
    %PYCMD% -m venv .venv
    if errorlevel 1 ( echo [ERROR] venv creation failed. & pause & exit /b 1 )
) else (
    echo [2/4] Reusing existing .venv
)
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
echo.

rem --- 3. pinned dependencies -------------------------------------
echo [3/4] Installing pinned dependencies...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 ( echo [ERROR] pip upgrade failed. & pause & exit /b 1 )
"%VENV_PY%" -m pip install -r requirements-build.txt
if errorlevel 1 ( echo [ERROR] dependency install failed. & pause & exit /b 1 )
echo.

rem --- 4. build: lint + tests + freeze + verify -------------------
echo [4/4] Building (lint, tests, freeze, verify)...
"%VENV_PY%" scripts\build\build.py --release
if errorlevel 1 ( echo [ERROR] build failed. & pause & exit /b 1 )

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
