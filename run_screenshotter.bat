@echo off
REM Double-click this file to start the Window Screenshotter.
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    set PY=python
) else (
    set PY=py
)

echo Launching Window Screenshotter with %PY% ...
%PY% "window_screenshotter.py"

if errorlevel 1 (
    echo.
    echo ---------------------------------------------------------
    echo The program exited with an error. Read the message above.
    echo If it says "No module named 'tkinter'", Python was installed
    echo without the Tcl/Tk option - reinstall Python and tick it.
    echo ---------------------------------------------------------
    pause
)
