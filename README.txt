WINDOW SCREENSHOTTER  -  setup & usage
======================================

WHAT IT NEEDS
-------------
- Windows 10
- Stock Python 3 (the normal installer from python.org, which includes
  tkinter by default)

That's it. NO internet connection required. NO packages to install.
This tool uses only Python's standard library (ctypes, zlib, tkinter), so
there is nothing to download or "pip install".


HOW TO INSTALL (offline)
------------------------
1. Copy this whole folder onto the offline PC (USB stick is fine). It contains:
       window_screenshotter.py   <- the program
       run_screenshotter.bat      <- double-click launcher
       README.txt                 <- this file
2. Make sure Python is installed on that PC. Open Command Prompt and type:
       python --version
   If you see a version number (e.g. "Python 3.12.x"), you're good.
   If "python" isn't recognized, try:
       py --version
3. Double-click  run_screenshotter.bat  to start.
   (Or run:  python window_screenshotter.py )


FIRST RUN
---------
You'll be asked three things:
  1. Pick the target window from a list.
  2. Pick the folder to save screenshots into.
  3. Enter a filename template. Put each variable in curly braces, e.g.
         run_{sample}_{temp}C_trial{trial}
     Every {name} becomes an input field in the panel.

  Auto-filled tokens you can also use (no field is created for them):
     {n}        auto-incrementing counter  (padding works: {n:03d} -> 001, 002)
     {date}     2026-06-16
     {time}     143005
     {datetime} 2026-06-16_143005

Your choices are remembered, so this only happens once. To change them later,
use the Pick / Change / Edit buttons in the panel. Settings are stored in:
     C:\Users\<you>\.window_screenshotter.json


TAKING SHOTS (the fast loop)
----------------------------
- The variable fields stay filled in between captures, so each time just change
  the one or two values that differ.
- Recent values for each variable appear as one-click buttons under its field.
- Press F9 (works even while the target app is focused) OR click the big
  CAPTURE button. The filename preview shows exactly what will be saved.
- "Undo last" deletes the most recent file (and rolls the {n} counter back).
- "Open folder" opens your save folder.


TROUBLESHOOTING
---------------
Shots are cropped or wrong size:
    This is normally a display-scaling issue and is already handled by the
    tool. If you still see it, make sure you launched the included .py file
    (the fix is built in).

Shots come out solid black:
    A few apps (some games / GPU-accelerated windows) can't be captured by the
    clean method. Tick "Foreground capture (only if shots come out black)".
    In that mode the window is briefly brought to the front and the panel hides
    itself during the grab, so the tool won't appear in the screenshot.

"No module named 'tkinter'":
    Python was installed without Tcl/Tk. Reinstall Python and tick the
    "tcl/tk and IDLE" option (it's on by default in the standard installer).

Window not found:
    The target app was closed or its title changed. Click "Pick..." and choose
    it again.


TIP: want it to run without the little black console window?
-----------------------------------------------------------
Launch with pythonw instead of python:
     pythonw window_screenshotter.py
(Downside: if something goes wrong you won't see an error message, so use the
normal launcher first to confirm everything works.)
