"""Small shared helpers: size formatting, saving received files, settings and folders."""

import json
import os
import re
import tempfile
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

_UNITS = ["B", "kB", "MB", "GB"]


def short_size(num_bytes: float) -> str:
    """Formats sizes like Android's Formatter.formatShortFileSize (1000-based units)."""
    v = max(0.0, float(num_bytes))
    unit = 0
    while v >= 900 and unit < len(_UNITS) - 1:
        v /= 1000
        unit += 1
    decimals = Decimal("1") if unit == 0 or v >= 10 else Decimal("0.1")
    text = str(Decimal(repr(v)).quantize(decimals, rounding=ROUND_HALF_UP))
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {_UNITS[unit]}"


def pictures_dir() -> str:
    """The user's Pictures folder (XDG), falling back to ~/Pictures."""
    home = Path.home()
    try:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
        for line in config.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*XDG_PICTURES_DIR\s*=\s*"?([^"]*)"?', line)
            if m:
                return m.group(1).replace("$HOME", str(home))
    except OSError:
        pass
    return str(home / "Pictures")


def default_receive_folder() -> str:
    return os.path.join(pictures_dir(), "LightSend")


_INVALID_CHARS = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL"}


def _is_reserved(stem: str) -> bool:
    s = stem.upper()
    return s in _RESERVED or (len(s) == 4 and s[:3] in ("COM", "LPT") and s[3].isdigit())


def safe_file_name(name: str) -> str:
    """Strips folders and characters other systems do not allow, keeping a sensible extension."""
    base = name.replace("\\", "/").split("/")[-1]
    cleaned = "".join("_" if c in _INVALID_CHARS or ord(c) < 32 or c == "\x7f" else c for c in base)
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned or all(c in "._" for c in cleaned):
        cleaned = f"LightSend_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    stem, ext = os.path.splitext(cleaned)
    if _is_reserved(stem):
        cleaned = "_" + cleaned
    if not os.path.splitext(cleaned)[1]:
        cleaned += ".jpg"
    if len(cleaned) <= 150:
        return cleaned
    stem, ext = os.path.splitext(cleaned)
    if len(ext) > 10:
        ext = ".jpg"
    return stem[:140] + ext


def save_received(folder: str, name: str, data: bytes) -> str:
    """Writes data without overwriting existing files and returns the full path that was used."""
    os.makedirs(folder, exist_ok=True)
    safe = safe_file_name(name)
    stem, ext = os.path.splitext(safe)
    path = os.path.join(folder, safe)
    i = 0
    while True:
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            i += 1
            if i >= 10_000:
                raise
            path = os.path.join(folder, f"{stem} ({i}){ext}")


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "lightsend"


class Settings:
    """Remembers small preferences in ~/.config/lightsend/settings.json."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _config_dir() / "settings.json"
        self.photo_folder: Optional[str] = None
        self.last_address: Optional[str] = None
        self._load()

    @property
    def effective_photo_folder(self) -> str:
        if self.photo_folder and os.path.isdir(self.photo_folder):
            return self.photo_folder
        return pictures_dir()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.photo_folder = data.get("photo_folder")
            self.last_address = data.get("last_address")
        except (OSError, ValueError, AttributeError):
            pass

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"photo_folder": self.photo_folder, "last_address": self.last_address}, f)
            os.replace(tmp, self._path)
        except OSError:
            pass
