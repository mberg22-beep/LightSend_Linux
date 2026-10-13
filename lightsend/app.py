"""Application entry point."""

import os
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request

from PIL import Image, ImageTk

from .ui import theme as theme_module
from .ui.main_window import MainWindow
from .ui.widgets import Dispatcher
from .util import Settings

_ICON = os.path.join(os.path.dirname(__file__), "assets", "LightSend.ico")
STARTUP_URL = "http://www.lightsend.net/client.html"
STARTUP_TIMEOUT_S = 2

try:  # optional: native file drops (pip install tkinterdnd2)
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = TkinterDnD = None


def _record_startup() -> None:
    # Usage tracking only; it must never delay startup or show an error.
    try:
        with urllib.request.urlopen(STARTUP_URL, timeout=STARTUP_TIMEOUT_S):
            pass
    except (urllib.error.URLError, OSError, ValueError):
        pass


def main() -> int:
    threading.Thread(target=_record_startup, name="LightSendStartup", daemon=True).start()
    try:
        root = TkinterDnD.Tk(className="lightsend") if TkinterDnD else tk.Tk(className="lightsend")
    except tk.TclError as e:
        print(f"LightSend needs a graphical desktop: {e}", file=sys.stderr)
        return 1

    theme = theme_module.Theme(theme_module.system_is_dark())
    theme_module.apply(root, theme)
    try:
        icon = Image.open(_ICON)
        root._icon = ImageTk.PhotoImage(icon)  # keep a reference
        root.iconphoto(True, root._icon)
    except (OSError, tk.TclError):
        pass

    dispatcher = Dispatcher(root)
    window = MainWindow(root, theme, Settings(), dispatcher, drop_enabled=TkinterDnD is not None)
    if TkinterDnD:
        def on_drop(event):
            window.on_drop(list(root.tk.splitlist(event.data)))
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", on_drop)

    root.mainloop()
    return 0
