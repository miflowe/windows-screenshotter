#!/usr/bin/env python3
"""
Fast window screenshot tool for Windows 10  --  ZERO dependencies.

Captures a specific window (the window's own pixels, not the screen, so other
windows on top of it never appear), names the file from a template you define,
and saves it to a folder you choose.

This version uses ONLY Python's standard library (ctypes + zlib + tkinter).
Nothing needs to be installed and the machine does NOT need internet.
Just stock Python 3 on Windows 10.

FIRST RUN
---------
You'll be asked to:
  1. Pick the target window.
  2. Choose a save folder.
  3. Enter a filename template. Put variables in curly braces, e.g.:
         run_{sample}_{temp}C_trial{trial}
     Every {name} becomes an input field in the panel.

SPECIAL TOKENS (filled automatically, no field is created for them):
  {n}        -> auto-incrementing counter (padding works: {n:03d} -> 001, 002...)
  {date}     -> YYYY-MM-DD
  {time}     -> HHMMSS
  {datetime} -> YYYY-MM-DD_HHMMSS

USAGE
-----
Set/adjust the variable fields, then press the big Capture button or the
global F9 hotkey. Values stay filled in between shots; recent values appear
as one-click buttons under each field.
"""

import os
import re
import sys
import json
import time
import zlib
import struct
import ctypes
from ctypes import wintypes
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox


CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".window_screenshotter.json")
RESERVED = {"n", "date", "time", "datetime"}
HISTORY_LEN = 6
VK_HOTKEY = 0x78          # F9
HOTKEY_NAME = "F9"


# =============================================================================
# Win32 via ctypes  (no pywin32 needed)
# =============================================================================
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

# Handles are pointers -- declaring them as c_void_p avoids truncation on 64-bit.
HWND = ctypes.c_void_p
HDC = ctypes.c_void_p
HBITMAP = ctypes.c_void_p
HGDIOBJ = ctypes.c_void_p
LONG = ctypes.c_long
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint
BOOL = ctypes.c_int

SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
SW_RESTORE = 9
SW_SHOW = 5
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# --- function prototypes ------------------------------------------------------
user32.GetWindowDC.restype = HDC
user32.GetWindowDC.argtypes = [HWND]
user32.GetDC.restype = HDC
user32.GetDC.argtypes = [HWND]
user32.ReleaseDC.argtypes = [HWND, HDC]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.PrintWindow.restype = BOOL
user32.PrintWindow.argtypes = [HWND, HDC, UINT]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [HWND]
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

gdi32.CreateCompatibleDC.restype = HDC
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleBitmap.restype = HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = HGDIOBJ
gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
gdi32.DeleteObject.argtypes = [HGDIOBJ]
gdi32.DeleteDC.argtypes = [HDC]
gdi32.BitBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, HDC, ctypes.c_int, ctypes.c_int, DWORD]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [HDC, HBITMAP, UINT, UINT, ctypes.c_void_p,
                            ctypes.c_void_p, UINT]

dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]

WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, ctypes.c_void_p)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG),
        ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
        ("biCompression", DWORD), ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG), ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD), ("biClrImportant", DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 3)]


def make_dpi_aware():
    """Critical: without this, capture is cropped/misaligned on scaled displays."""
    try:
        # PER_MONITOR_AWARE_V2 (Win10 1703+)
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# =============================================================================
# Window enumeration
# =============================================================================
def list_windows():
    out = []

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title.strip():
                    out.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_hwnd(title):
    wins = list_windows()
    for hwnd, t in wins:
        if t == title:
            return hwnd
    for hwnd, t in wins:
        if title.lower() in t.lower():
            return hwnd
    return None


def get_window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def get_extended_frame(hwnd):
    """Visible window rectangle, excluding Win10's invisible resize border."""
    r = wintypes.RECT()
    res = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    if res == 0:
        return r.left, r.top, r.right, r.bottom
    return get_window_rect(hwnd)


# =============================================================================
# Capture
# =============================================================================
def _getdibits(mem_dc, bmp, w, h):
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # negative -> top-down rows
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    buf = (ctypes.c_ubyte * (w * h * 4))()
    res = gdi32.GetDIBits(mem_dc, bmp, 0, h,
                          ctypes.cast(buf, ctypes.c_void_p),
                          ctypes.byref(bmi), DIB_RGB_COLORS)
    if res == 0:
        return None
    return bytes(buf)          # BGRA, top-down


def _looks_blank(buf):
    # cheap heuristic: are all sampled bytes zero (solid black)?
    return not any(buf[::1009])


def crop_bgra(buf, w, h, x, y, cw, ch):
    src_stride = w * 4
    dst_stride = cw * 4
    out = bytearray(dst_stride * ch)
    for row in range(ch):
        s = (y + row) * src_stride + x * 4
        d = row * dst_stride
        out[d:d + dst_stride] = buf[s:s + dst_stride]
    return bytes(out), cw, ch


def capture_printwindow(hwnd):
    """Clean capture of the window's own content (ignores overlapping windows)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.12)

    l, t, r, b = get_window_rect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None

    win_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(win_dc)
    bmp = gdi32.CreateCompatibleBitmap(win_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
    buf = _getdibits(mem_dc, bmp, w, h)
    gdi32.SelectObject(mem_dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, win_dc)

    if not ok or buf is None or _looks_blank(buf):
        return None

    # Crop away the invisible Win10 border so the image is tight to the window.
    el, et, er, eb = get_extended_frame(hwnd)
    cx = max(0, el - l)
    cy = max(0, et - t)
    cw = min(w - cx, er - el)
    ch = min(h - cy, eb - et)
    if cw > 0 and ch > 0 and (cx or cy or cw != w or ch != h):
        return crop_bgra(buf, w, h, cx, cy, cw, ch)
    return buf, w, h


def capture_foreground(hwnd):
    """Fallback: bring window to front and copy its screen region."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        user32.ShowWindow(hwnd, SW_SHOW)
    time.sleep(0.2)

    l, t, r, b = get_extended_frame(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, l, t, SRCCOPY)
    buf = _getdibits(mem_dc, bmp, w, h)
    gdi32.SelectObject(mem_dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(None, screen_dc)

    if buf is None:
        return None
    return buf, w, h


# =============================================================================
# PNG writer (pure stdlib, no Pillow)
# =============================================================================
def save_png(path, bgra, w, h, level=6):
    data = bytearray(bgra)                       # BGRA, top-down
    data[0::4], data[2::4] = data[2::4], data[0::4]   # swap B<->R -> RGBA
    del data[3::4]                               # drop alpha -> RGB

    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)                            # filter type 0 (None)
        raw += data[y * stride:(y + 1) * stride]

    compressed = zlib.compress(bytes(raw), level)

    def chunk(typ, payload):
        out = struct.pack(">I", len(payload)) + typ + payload
        out += struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit, RGB
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", compressed))
        f.write(chunk(b"IEND", b""))


# =============================================================================
# Config
# =============================================================================
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("Could not save config:", e)


# =============================================================================
# Filename helpers
# =============================================================================
def template_variables(template):
    names = []
    for m in re.finditer(r"\{([^}{:!]+)(?:[:!][^}]*)?\}", template):
        name = m.group(1)
        if name not in RESERVED and name not in names:
            names.append(name)
    return names


def sanitize(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().strip(".")


def build_filename(template, values, counter):
    now = datetime.now()
    ctx = dict(values)
    ctx["n"] = counter
    ctx["date"] = now.strftime("%Y-%m-%d")
    ctx["time"] = now.strftime("%H%M%S")
    ctx["datetime"] = now.strftime("%Y-%m-%d_%H%M%S")
    try:
        name = template.format(**ctx)
    except Exception:
        safe = {k: str(v) for k, v in ctx.items()}
        name = re.sub(r"\{([^}{:!]+)(?:[:!][^}]*)?\}",
                      lambda m: safe.get(m.group(1), m.group(0)), template)
    return sanitize(name)


def unique_path(folder, base, ext=".png"):
    path = os.path.join(folder, base + ext)
    if not os.path.exists(path):
        return path
    i = 1
    while True:
        path = os.path.join(folder, f"{base}_{i}{ext}")
        if not os.path.exists(path):
            return path
        i += 1


# =============================================================================
# GUI
# =============================================================================
class App(tk.Tk):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.title("Window Screenshotter")
        self.attributes("-topmost", True)
        self.resizable(False, False)

        self.var_widgets = {}
        self.last_saved = None
        self._hotkey_down = False

        self._build_top_controls()
        self.vars_frame = ttk.Frame(self, padding=(10, 0))
        self.vars_frame.pack(fill="x")
        self._build_bottom_controls()

        self.rebuild_variable_fields()
        self._refresh_preview()
        self.bind_all(f"<{HOTKEY_NAME}>", lambda e: self.capture())
        self.after(40, self._poll_hotkey)

    # ---- UI construction -----------------------------------------------------
    def _build_top_controls(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        self.target_var = tk.StringVar(value=self.cfg.get("target", "(none)"))
        ttk.Label(top, text="Target window:").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.target_var, width=34,
                  foreground="#0a58ca").grid(row=0, column=1, sticky="w", padx=4)
        ttk.Button(top, text="Pick…", width=8,
                   command=self.pick_window).grid(row=0, column=2)

        self.folder_var = tk.StringVar(value=self.cfg.get("folder", "(none)"))
        ttk.Label(top, text="Save folder:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(top, textvariable=self.folder_var, width=34,
                  foreground="#198754").grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Button(top, text="Change…", width=8,
                   command=self.change_folder).grid(row=1, column=2, pady=(6, 0))

        self.template_var = tk.StringVar(value=self.cfg.get("template", "shot_{n:03d}"))
        ttk.Label(top, text="Name template:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(top, textvariable=self.template_var, width=34,
                  foreground="#6f42c1").grid(row=2, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Button(top, text="Edit…", width=8,
                   command=self.edit_template).grid(row=2, column=2, pady=(6, 0))

    def _build_bottom_controls(self):
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=4)
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")

        crow = ttk.Frame(bottom)
        crow.pack(fill="x")
        ttk.Label(crow, text="Next {n}:").pack(side="left")
        self.counter_var = tk.IntVar(value=self.cfg.get("counter", 1))
        ttk.Spinbox(crow, from_=0, to=999999, width=8, textvariable=self.counter_var,
                    command=self._refresh_preview).pack(side="left", padx=4)
        self.foreground_var = tk.BooleanVar(value=self.cfg.get("force_foreground", False))
        ttk.Checkbutton(crow, text="Foreground capture (only if shots come out black)",
                        variable=self.foreground_var).pack(side="left", padx=10)

        self.preview_var = tk.StringVar(value="")
        ttk.Label(bottom, text="Saves as:").pack(anchor="w", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.preview_var, foreground="#444",
                  font=("Segoe UI", 9, "italic")).pack(anchor="w")

        self.capture_btn = tk.Button(
            bottom, text=f"📸  CAPTURE  ({HOTKEY_NAME})", command=self.capture,
            bg="#0d6efd", fg="white", font=("Segoe UI", 12, "bold"),
            activebackground="#0b5ed7", height=2, cursor="hand2")
        self.capture_btn.pack(fill="x", pady=(10, 4))

        actions = ttk.Frame(bottom)
        actions.pack(fill="x")
        ttk.Button(actions, text="Open folder", command=self.open_folder).pack(side="left")
        ttk.Button(actions, text="Undo last", command=self.undo_last).pack(side="left", padx=6)

        self.status_var = tk.StringVar(value=f"Ready. Press {HOTKEY_NAME} or the button.")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#555").pack(anchor="w", pady=(6, 0))

    def rebuild_variable_fields(self):
        for child in self.vars_frame.winfo_children():
            child.destroy()
        self.var_widgets.clear()

        names = template_variables(self.template_var.get())
        saved_vals = self.cfg.get("values", {})
        histories = self.cfg.get("histories", {})

        if names:
            ttk.Label(self.vars_frame, text="Variables (stay filled in between shots):",
                      font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))

        for name in names:
            row = ttk.Frame(self.vars_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{name}:", width=12).pack(side="left")
            sv = tk.StringVar(value=saved_vals.get(name, ""))
            sv.trace_add("write", lambda *a: self._refresh_preview())
            ttk.Entry(row, textvariable=sv, width=22).pack(side="left", padx=4)

            hist_frame = ttk.Frame(self.vars_frame)
            hist_frame.pack(fill="x", padx=(96, 0))
            self.var_widgets[name] = (sv, hist_frame)
            self._render_history(name, histories.get(name, []))

    def _render_history(self, name, values):
        sv, hist_frame = self.var_widgets[name]
        for child in hist_frame.winfo_children():
            child.destroy()
        for val in values[:HISTORY_LEN]:
            tk.Button(hist_frame, text=val, font=("Segoe UI", 8), relief="groove",
                      cursor="hand2",
                      command=lambda v=val, s=sv: s.set(v)).pack(side="left", padx=2, pady=1)

    # ---- actions -------------------------------------------------------------
    def pick_window(self):
        wins = list_windows()
        titles = [t for _, t in wins]
        dlg = tk.Toplevel(self)
        dlg.title("Pick a window")
        dlg.attributes("-topmost", True)
        dlg.geometry("520x360")
        ttk.Label(dlg, text="Double-click the target window:").pack(anchor="w", padx=8, pady=6)
        lb = tk.Listbox(dlg, font=("Segoe UI", 9))
        lb.pack(fill="both", expand=True, padx=8, pady=4)
        for t in titles:
            lb.insert("end", t)

        def choose(_=None):
            sel = lb.curselection()
            if sel:
                self.target_var.set(titles[sel[0]])
                self._persist()
                dlg.destroy()

        lb.bind("<Double-Button-1>", choose)
        ttk.Button(dlg, text="Select", command=choose).pack(pady=6)

    def change_folder(self):
        folder = filedialog.askdirectory(title="Choose save folder")
        if folder:
            self.folder_var.set(folder)
            self._persist()

    def edit_template(self):
        new = simpledialog.askstring(
            "Filename template",
            "Use {name} for input fields, plus {n}/{date}/{time}/{datetime}.\n"
            "Example:  run_{sample}_{temp}C_trial{trial}",
            initialvalue=self.template_var.get(), parent=self)
        if new:
            self.template_var.set(new)
            self.rebuild_variable_fields()
            self._refresh_preview()
            self._persist()

    def _current_values(self):
        return {name: sv.get() for name, (sv, _) in self.var_widgets.items()}

    def _refresh_preview(self):
        base = build_filename(self.template_var.get(),
                              self._current_values(), self.counter_var.get())
        self.preview_var.set(base + ".png")

    def capture(self):
        target = self.target_var.get()
        folder = self.folder_var.get()
        if target in ("", "(none)"):
            messagebox.showwarning("No window", "Pick a target window first.")
            return
        if folder in ("", "(none)") or not os.path.isdir(folder):
            messagebox.showwarning("No folder", "Choose a valid save folder first.")
            return

        hwnd = find_hwnd(target)
        if not hwnd:
            messagebox.showerror("Window not found",
                                 f"Couldn't find a window matching:\n{target}\n\n"
                                 "Is it still open? Try Pick… again.")
            return

        try:
            if self.foreground_var.get():
                self.withdraw(); self.update(); time.sleep(0.05)
                try:
                    result = capture_foreground(hwnd)
                finally:
                    self.deiconify(); self.lift()
            else:
                result = capture_printwindow(hwnd)
                if result is None:           # auto-fallback if PrintWindow fails
                    self.withdraw(); self.update(); time.sleep(0.05)
                    try:
                        result = capture_foreground(hwnd)
                    finally:
                        self.deiconify(); self.lift()
        except Exception as e:
            messagebox.showerror("Capture failed", str(e))
            return

        if not result:
            messagebox.showerror("Capture failed",
                                 "Got an empty image. Try toggling 'Foreground capture'.")
            return

        buf, w, h = result
        values = self._current_values()
        base = build_filename(self.template_var.get(), values, self.counter_var.get())
        path = unique_path(folder, base)
        try:
            save_png(path, buf, w, h)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return

        self.last_saved = path
        tmpl = self.template_var.get()
        if "{n}" in tmpl or re.search(r"\{n[:!]", tmpl):
            self.counter_var.set(self.counter_var.get() + 1)
        self._update_histories(values)
        self._refresh_preview()
        self._persist()
        self._flash_ok(os.path.basename(path))

    def _update_histories(self, values):
        histories = self.cfg.get("histories", {})
        for name, val in values.items():
            if not val:
                continue
            hist = [val] + [v for v in histories.get(name, []) if v != val]
            histories[name] = hist[:HISTORY_LEN]
            if name in self.var_widgets:
                self._render_history(name, histories[name])
        self.cfg["histories"] = histories

    def _flash_ok(self, fname):
        self.status_var.set(f"Saved {fname}")
        orig = self.capture_btn.cget("bg")
        self.capture_btn.config(bg="#198754", text="✓  SAVED")
        self.after(350, lambda: self.capture_btn.config(
            bg=orig, text=f"📸  CAPTURE  ({HOTKEY_NAME})"))

    def open_folder(self):
        folder = self.folder_var.get()
        if os.path.isdir(folder):
            os.startfile(folder)

    def undo_last(self):
        if self.last_saved and os.path.exists(self.last_saved):
            try:
                os.remove(self.last_saved)
                tmpl = self.template_var.get()
                if "{n}" in tmpl or re.search(r"\{n[:!]", tmpl):
                    self.counter_var.set(max(0, self.counter_var.get() - 1))
                self.status_var.set(f"Deleted {os.path.basename(self.last_saved)}")
                self.last_saved = None
                self._refresh_preview()
                self._persist()
            except Exception as e:
                messagebox.showerror("Undo failed", str(e))
        else:
            self.status_var.set("Nothing to undo.")

    # ---- plumbing ------------------------------------------------------------
    def _poll_hotkey(self):
        """Global hotkey via GetAsyncKeyState (works even when unfocused)."""
        try:
            down = bool(user32.GetAsyncKeyState(VK_HOTKEY) & 0x8000)
            if down and not self._hotkey_down:
                self._hotkey_down = True
                self.capture()
            elif not down:
                self._hotkey_down = False
        except Exception:
            pass
        self.after(40, self._poll_hotkey)

    def _persist(self):
        self.cfg.update({
            "target": self.target_var.get(),
            "folder": self.folder_var.get(),
            "template": self.template_var.get(),
            "counter": self.counter_var.get(),
            "force_foreground": self.foreground_var.get(),
            "values": self._current_values(),
        })
        save_config(self.cfg)

    def destroy(self):
        try:
            self._persist()
        except Exception:
            pass
        super().destroy()


# =============================================================================
# First-run setup
# =============================================================================
def first_run_setup(cfg):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    messagebox.showinfo("Setup", "Let's set up your screenshot tool.\n\n"
                                 "1) Pick the target window\n"
                                 "2) Pick a save folder\n"
                                 "3) Enter a name template")

    wins = list_windows()
    titles = [t for _, t in wins]
    dlg = tk.Toplevel(root)
    dlg.title("Pick the target window")
    dlg.attributes("-topmost", True)
    dlg.geometry("520x360")
    ttk.Label(dlg, text="Double-click the window you want to screenshot:").pack(
        anchor="w", padx=8, pady=6)
    lb = tk.Listbox(dlg, font=("Segoe UI", 9))
    lb.pack(fill="both", expand=True, padx=8, pady=4)
    for t in titles:
        lb.insert("end", t)
    chosen = {"title": None}

    def choose(_=None):
        sel = lb.curselection()
        if sel:
            chosen["title"] = titles[sel[0]]
            dlg.destroy()

    lb.bind("<Double-Button-1>", choose)
    ttk.Button(dlg, text="Select", command=choose).pack(pady=6)
    root.wait_window(dlg)
    cfg["target"] = chosen["title"] or "(none)"

    folder = filedialog.askdirectory(title="Choose where to save screenshots")
    cfg["folder"] = folder or "(none)"

    tmpl = simpledialog.askstring(
        "Filename template",
        "Use {name} for each variable, plus {n}/{date}/{time}/{datetime}.\n\n"
        "Example:  run_{sample}_{temp}C_trial{trial}",
        initialvalue="run_{sample}_{temp}C_trial{trial}", parent=root)
    cfg["template"] = tmpl or "shot_{n:03d}"
    cfg["counter"] = 1

    root.destroy()
    save_config(cfg)
    return cfg


def main():
    make_dpi_aware()
    cfg = load_config()
    if not cfg.get("target") or not cfg.get("folder") or not cfg.get("template"):
        cfg = first_run_setup(cfg)
    App(cfg).mainloop()


if __name__ == "__main__":
    main()
