@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Liangzhu Catalog - stop public exposure
echo ============================================================
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

"%PY%" "%~dp0public.py" stop
echo.
pause
exit /b 0

:nopy
echo   [X] No usable Python found. Need 3.10 or newer.
echo.
pause
exit /b 1
