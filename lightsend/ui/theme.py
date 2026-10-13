"""Material 3 baseline colours, the same ones the Android app gets from Theme.Material3.DayNight."""

import os
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Dict

LIGHT = {
    "ButtonFill": "#424242", "OnButtonFill": "#ffffff",
    "Primary": "#6750a4", "OnPrimary": "#ffffff",
    "SecondaryContainer": "#e8def8", "OnSecondaryContainer": "#1d192b",
    "Surface": "#fffbfe", "SurfaceContainer": "#f3edf7",
    "OnSurface": "#1c1b1f", "OnSurfaceVariant": "#49454f",
    "Outline": "#79747e", "OutlineVariant": "#cac4d0",
}
DARK = {
    "ButtonFill": "#424242", "OnButtonFill": "#ffffff",
    "Primary": "#d0bcff", "OnPrimary": "#381e72",
    "SecondaryContainer": "#4a4458", "OnSecondaryContainer": "#e8def8",
    "Surface": "#1c1b1f", "SurfaceContainer": "#2b2930",
    "OnSurface": "#e6e1e5", "OnSurfaceVariant": "#cac4d0",
    "Outline": "#938f99", "OutlineVariant": "#49454f",
}


def blend(fg: str, bg: str, alpha: float) -> str:
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * alpha + y * (1 - alpha)):02x}" for x, y in zip(f, b))


def system_is_dark() -> bool:
    """Follows the desktop's light/dark setting (GNOME/GTK); light when it cannot be told."""
    forced = os.environ.get("LIGHTSEND_THEME", "").lower()
    if forced in ("dark", "light"):
        return forced == "dark"
    for cmd in (["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout.lower()
        except (OSError, subprocess.SubprocessError):
            continue
        if "prefer-dark" in out or "dark" in out:
            return True
        if "prefer-light" in out or "default" in out:
            return False
    return "dark" in os.environ.get("GTK_THEME", "").lower()


class Theme:
    def __init__(self, dark: bool):
        self.dark = dark
        self.c: Dict[str, str] = dict(DARK if dark else LIGHT)
        c = self.c
        c["Disabled"] = blend(c["OnSurface"], c["Surface"], 0.38)
        c["DisabledContainer"] = blend(c["OnSurface"], c["Surface"], 0.12)
        c["PrimaryHover"] = blend(c["Primary"], c["Surface"], 0.08)
        c["PrimaryPressed"] = blend(c["Primary"], c["Surface"], 0.16)
        c["ButtonHover"] = blend("#ffffff", c["ButtonFill"], 0.12)
        c["ButtonPressed"] = blend("#ffffff", c["ButtonFill"], 0.2)
        c["TileLabel"] = "#2e2e2e"
        c["TileFailed"] = "#b3261e"
        c["Error"] = "#ef9a9a" if dark else "#d32f2f"

    def __getitem__(self, key: str) -> str:
        return self.c[key]


def apply(root: tk.Misc, theme: Theme) -> None:
    c = theme
    root.configure(background=c["Surface"])
    base = tkfont.nametofont("TkDefaultFont")
    base.configure(size=11)
    tkfont.nametofont("TkTextFont").configure(size=11)
    tkfont.nametofont("TkFixedFont").configure(size=10)
    title = tkfont.Font(family=base.actual("family"), size=18, weight="bold")
    medium = tkfont.Font(family=base.actual("family"), size=13, weight="bold")
    small = tkfont.Font(family=base.actual("family"), size=10)
    root.option_add("*TCombobox*Listbox.background", c["SurfaceContainer"])
    root.option_add("*TCombobox*Listbox.foreground", c["OnSurface"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["Primary"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["OnPrimary"])

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=c["Surface"], foreground=c["OnSurface"], bordercolor=c["Outline"],
                lightcolor=c["Surface"], darkcolor=c["Surface"], troughcolor=c["SurfaceContainer"],
                focuscolor=c["Primary"], selectbackground=c["Primary"], selectforeground=c["OnPrimary"])
    s.configure("TFrame", background=c["Surface"])
    s.configure("TLabel", background=c["Surface"], foreground=c["OnSurface"])
    s.configure("Title.TLabel", font=title)
    s.configure("Medium.TLabel", font=medium)
    s.configure("Hint.TLabel", font=small, foreground=c["OnSurfaceVariant"])
    s.configure("Body.TLabel", foreground=c["OnSurfaceVariant"])
    s.configure("Error.TLabel", foreground=c["Error"])

    s.configure("TButton", background=c["ButtonFill"], foreground=c["OnButtonFill"], borderwidth=0,
                focusthickness=2, padding=(16, 10), relief="flat", anchor="center")
    s.map("TButton",
          background=[("disabled", c["DisabledContainer"]), ("pressed", c["ButtonPressed"]),
                      ("active", c["ButtonHover"])],
          foreground=[("disabled", c["Disabled"])])
    s.configure("Outlined.TButton", background=c["Surface"], foreground=c["Primary"], borderwidth=1,
                bordercolor=c["Outline"], lightcolor=c["Outline"], darkcolor=c["Outline"])
    s.map("Outlined.TButton",
          background=[("disabled", c["Surface"]), ("pressed", c["PrimaryPressed"]),
                      ("active", c["PrimaryHover"])],
          foreground=[("disabled", c["Disabled"])],
          bordercolor=[("disabled", c["DisabledContainer"])])
    s.configure("Text.TButton", background=c["Surface"], foreground=c["Primary"], borderwidth=0)
    s.map("Text.TButton",
          background=[("pressed", c["PrimaryPressed"]), ("active", c["PrimaryHover"])],
          foreground=[("disabled", c["Disabled"])])

    s.configure("TRadiobutton", background=c["Surface"], foreground=c["OnSurface"], indicatorcolor=c["Surface"],
                indicatorbackground=c["Surface"], padding=(0, 4))
    s.map("TRadiobutton",
          background=[("active", c["Surface"])],
          indicatorcolor=[("selected", c["Primary"]), ("disabled", c["DisabledContainer"])],
          foreground=[("disabled", c["Disabled"])])

    s.configure("TCombobox", fieldbackground=c["SurfaceContainer"], background=c["SurfaceContainer"],
                foreground=c["OnSurface"], arrowcolor=c["OnSurfaceVariant"], padding=6,
                selectbackground=c["SurfaceContainer"], selectforeground=c["OnSurface"])
    s.map("TCombobox",
          fieldbackground=[("readonly", c["SurfaceContainer"]), ("disabled", c["Surface"])],
          foreground=[("disabled", c["Disabled"])])
    s.configure("TEntry", fieldbackground=c["SurfaceContainer"], foreground=c["OnSurface"], padding=8,
                insertcolor=c["OnSurface"])
    s.configure("Horizontal.TProgressbar", background=c["Primary"], troughcolor=c["SurfaceContainer"],
                borderwidth=0, thickness=8)
    s.configure("Vertical.TScrollbar", background=c["OutlineVariant"], troughcolor=c["Surface"],
                arrowcolor=c["OnSurfaceVariant"], borderwidth=0)
    s.map("Vertical.TScrollbar", background=[("active", c["Outline"])])
    s.configure("Row.TFrame", background=c["Surface"])
