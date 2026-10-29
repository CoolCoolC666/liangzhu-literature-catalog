@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Liangzhu Culture Literature Catalog - double click to start
echo   (Local only: http://127.0.0.1:8771/)
echo ============================================================
echo.

rem ---- WHY THIS FILE LOOKS LIKE THIS ----
rem 1. The body is deliberately ASCII-only with CRLF line endings.
rem    Chinese characters inside a .cmd body get mangled by cmd.exe and
rem    silently corrupt the following lines, which shows up as
rem    "double click and it flashes and dies". Chinese is fine in the
rem    FILE NAME, never in the body.
rem 2. Interpreter probing: the `python` on PATH may be the Microsoft
rem    Store placeholder stub (not an interpreter at all), and the
rem    `py` launcher knows about interpreters that are NOT on PATH.
rem    So we try several entry points and ask the interpreter itself
rem    for its real path via sys.executable.
rem 3. The bundled runtime\ (if shipped alongside) is the last resort:
rem    nothing to install, nothing to configure, no network needed.

set "PY="
set "PYKIND="

for %%C in ("py -3" "python" "python3" "py -3.13" "py -3.12" "py -3.11" "py -3.10") do (
    if not defined PY (
        %%~C -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            for /f "usebackq delims=" %%P in (`%%~C -c "import sys;print(sys.executable)" 2^>nul`) do (
                if not defined PY (
                    set "PY=%%P"
                    set "PYKIND=local"
                )
            )
        )
    )
)

if not defined PY if exist "runtime\python.exe" (
    set "PY=%CD%\runtime\python.exe"
    set "PYKIND=bundled runtime"
)

if not defined PY goto :nopy

echo   Interpreter : %PY%
echo   Kind       : %PYKIND%
echo.

rem ---- dependencies ----
"%PY%" -c "import fastapi,uvicorn,networkx,pyvis,openpyxl" >nul 2>&1
if errorlevel 1 (
    echo   Installing dependencies, please wait ...
    "%PY%" -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   [X] Dependency install failed. Try manually:
        echo       "%PY%" -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)

rem ---- database ----
if not exist "data\lzkg.db" (
    echo   Database not found. Migrating data, please wait ...
    "%PY%" -m lzkg migrate
    if errorlevel 1 (
        echo.
        echo   [X] Migration failed. The source dataset folder was not found.
        echo.
        echo       Point the program at your data source, then migrate again:
        echo         "%PY%" -m lzkg set-source "D:\path\to\your\data\folder"
        echo         "%PY%" -m lzkg migrate
        echo.
        echo       Or run "%PY%" -m lzkg doctor to see what it checked.
        echo.
        pause
        exit /b 1
    )
)

echo   Starting server on http://127.0.0.1:8771/ ...
echo   Close this window to stop the server.
echo.

start "" "http://127.0.0.1:8771/"
"%PY%" -m lzkg serve
exit /b 0

:nopy
echo   [X] No usable Python found (need 3.10 or newer).
echo.
echo       Tried: py -3 / python / python3 / py -3.10..3.13 / runtime\python.exe
echo       Install Python 3.10+ from https://www.python.org/downloads/
echo       or put a portable Python into the runtime\ folder next to this file.
echo.
pause
exit /b 1