@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Liangzhu Catalog - PUBLIC exposure launcher
echo   Local  : http://127.0.0.1:8771/
echo   Public : https://lztp.example.com/
echo ============================================================
echo.
echo   NOTE: no auto-start on boot. This window starts it, and
echo         closing the site is done with the stop script.
echo.

set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            for /f "usebackq delims=" %%P in (`%%~C -c "import sys;print(sys.executable)" 2^>nul`) do (
                if not defined PY set "PY=%%P"
            )
        )
    )
)
if not defined PY goto :nopy

rem ---- dependencies ----
rem NOTE: same probe list as the local start script / requirements.txt.
rem   public.py has no dependency handling of its own, so without this the
rem   window dies on a missing module with a raw traceback.
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

"%PY%" "%~dp0public.py" start
echo.
echo   Public check:  "%PY%" "%~dp0public.py" check
echo.
pause
exit /b 0

:nopy
echo   [X] No usable Python found. Need 3.10 or newer.
echo.
pause
exit /b 1
