@echo off
rem Runs the Suno API wrapper (server.py, http://127.0.0.1:8000) and starts it again when it stops.
rem The "SunoAPI" scheduled task runs this at boot. Output goes to server.log (kept under 10 MB, one old copy).
cd /d "%~dp0"
:loop
for %%A in (server.log) do if %%~zA GTR 10485760 move /y server.log server.log.1 >nul
echo [%date% %time%] starting server.py >> server.log
"%~dp0.venv\Scripts\python.exe" server.py >> server.log 2>&1
echo [%date% %time%] server.py stopped (exit %errorlevel%), restarting in 10 s >> server.log
ping -n 11 127.0.0.1 >nul
goto loop
