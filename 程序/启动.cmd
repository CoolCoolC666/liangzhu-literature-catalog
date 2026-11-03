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
rem NOTE: The probe list MUST cover every third-party package in requirements.txt.
rem   A partial probe is worse than none: the probe passes (fastapi is there),
rem   the installer is skipped, and then the app dies on a missing module at
rem   startup -> the window flashes and dies with no visible reason.
rem   Keep this list == requirements.txt (test_all.py::test_requirements checks it).
"%PY%" -c "import requests,fastapi,uvicorn,networkx,pyvis,openpyxl,httpx,mcp,defusedxml,matplotlib,shapely,jwt" >nul 2>&1
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

rem ---- port check ----
rem NOTE: if the site is already running, uvicorn exits right away with
rem   "address already in use" and this window closes -> the user sees
rem   "double click and it flashes and dies" with no clue why.
rem   The port below must match the URL used further down.
"%PY%" -c "import socket,sys;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',8771));s.close();sys.exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   Port 8771 is already in use.
    echo   The site is probably already running - just open:
    echo       http://127.0.0.1:8771/
    echo   Or close the other window first, then start again.
    echo.
    pause
    exit /b 0
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