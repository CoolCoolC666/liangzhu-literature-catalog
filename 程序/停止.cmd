@echo off
setlocal
rem Stop the Liangzhu catalog server by listening port.
rem ASCII-only body on purpose (see the notes in the start script).

set "PORT=8771"
echo   Stopping Liangzhu catalog server on port %PORT% ...

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    if not "%%p"=="" (
        echo   Killing PID %%p ...
        taskkill /F /PID %%p >nul 2>&1
    )
)

echo   Done.
exit /b 0