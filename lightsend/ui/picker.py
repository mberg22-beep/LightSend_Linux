"""Grid of photos in a folder (Pictures by default), the PC counterpart of DcimPickerActivity."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageTk

from .. import imaging, strings
from ..util import Settings
from .theme import Theme
from .widgets import Dispatcher, WrapLabel, center_on

MAX_SELECTION = 50
MAX_PHOTOS = 3000
MIN_CELL = 150
THUMB_SIZE = 240
LOADER_WORKERS = 4
KEEP_ROWS = 6  # rows beyond the viewport whose thumbnails are kept


class _Item:
    def __init__(self, path: str):
        self.path = path
        self.thumb: Optional[Image.Image] = None
        self.photo: Optional[ImageTk.PhotoImage] = None
        self.photo_size = 0
        self.requested = False
        self.badge = 0
        self.index = 0


class _ThumbnailLoader:
    """Decodes thumbnails on a few background threads, newest requests first (what is on screen now)."""

    def __init__(self, on_done: Callable[[_Item, Optional[Image.Image]], None]):
        self._on_done = on_done
        self._cond = threading.Condition()
        self._stack: List[_Item] = []
        self._closed = False
        for _ in range(LOADER_WORKERS):
            threading.Thread(target=self._work, name="LightSendThumbs", daemon=True).start()

    def request(self, item: _Item) -> None:
        with self._cond:
            self._stack.append(item)
            self._cond.notify()

    def clear(self) -> None:
        with self._cond:
            for item in self._stack:
                item.requested = False
            self._stack.clear()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._stack.clear()
            self._cond.notify_all()

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._stack and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                item = self._stack.pop()
            image = imaging.try_load_thumbnail(item.path, THUMB_SIZE)
            if image is not None:
                image = imaging.cover(image, THUMB_SIZE)
            self._on_done(item, image)


def _scan(folder: str, is_current: Callable[[], bool]) -> Tuple[List[str], int]:
    found: List[Tuple[str, float]] = []
    for root, dirs, files in os.walk(folder):
        if not is_current():
            return [], 0
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith(".") or not imaging.is_photo(f):
                continue
            path = os.path.join(root, f)
            try:
                found.append((path, os.path.getmtime(path)))
            except OSError:
                continue
    found.sort(key=lambda p: p[1], reverse=True)
    return [p for p, _ in found[:MAX_PHOTOS]], len(found)


class PickerDialog(tk.Toplevel):
    def __init__(self, master, preselected: List[str], settings: Settings, theme: Theme, dispatcher: Dispatcher):
        super().__init__(master, background=theme["Surface"])
        self._settings = settings
        self._theme = theme
        self._dispatcher = dispatcher
        self._selection = list(preselected)
        self.confirmed = False
        self._items: List[_Item] = []
        self._columns = 0
        self._cell = 0
        self._drawn: Dict[int, None] = {}
        self._scan_id = 0
        self._refresh_pending = False
        self._loader = _ThumbnailLoader(lambda item, image: dispatcher.post(self._thumb_ready, item, image))

        self.title(strings.PICK_TITLE)
        self.transient(master)
        self.minsize(420, 400)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())

        top = ttk.Frame(self, padding=(16, 12, 16, 4))
        top.pack(fill="x")
        self._selection_label = ttk.Label(top, style="Medium.TLabel")
        self._selection_label.pack(side="left")
        self._done = ttk.Button(top, text=strings.PICK_DONE, command=self._on_done)
        self._done.pack(side="right")
        self._clear = ttk.Button(top, text=strings.PICK_CLEAR, style="Outlined.TButton", command=self._on_clear)
        self._clear.pack(side="right", padx=(0, 8))

        folder_row = ttk.Frame(self, padding=(16, 0, 16, 4))
        folder_row.pack(fill="x")
        ttk.Button(folder_row, text=strings.PICK_CHANGE_FOLDER, style="Text.TButton",
                   command=self._on_change_folder).pack(side="right")
        self._folder_label = ttk.Label(folder_row, style="Hint.TLabel")
        self._folder_label.pack(side="left", fill="x", expand=True)
        self._status = WrapLabel(self, style="Hint.TLabel")
        self._status.pack(fill="x", padx=16, pady=(0, 6))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self._bar = ttk.Scrollbar(body, orient="vertical", command=self._canvas_yview)
        self._bar.pack(side="right", fill="y")
        self._canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0, background=theme["Surface"],
                                 yscrollcommand=self._on_yscroll)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda e: self._schedule_layout())
        self._canvas.bind("<Button-1>", self._on_click)
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self._canvas.bind(seq, self._on_wheel)

        center_on(self, master, 760, 720)
        self._update_selection_ui()
        self.wait_visibility()
        self.grab_set()
        self._load(settings.effective_photo_folder)

    @property
    def selection(self) -> List[str]:
        return self._selection

    def show(self) -> bool:
        self.master.wait_window(self)
        return self.confirmed

    # ---- Loading ----

    def _load(self, folder: str) -> None:
        self._scan_id += 1
        scan_id = self._scan_id
        self._loader.clear()
        self._folder_label.configure(text=folder)
        self._status.configure(text=strings.PICK_LOADING)
        self._items = []
        self._relayout(force=True)

        def work():
            paths, total = _scan(folder, lambda: scan_id == self._scan_id)
            self._dispatcher.post(self._scan_done, scan_id, paths, total)

        threading.Thread(target=work, name="LightSendScan", daemon=True).start()

    def _scan_done(self, scan_id: int, paths: List[str], total: int) -> None:
        if scan_id != self._scan_id:
            return
        self._items = [_Item(p) for p in paths]
        for i, item in enumerate(self._items):
            item.index = i
        if not paths:
            self._status.configure(text=strings.PICK_EMPTY)
        elif total > len(paths):
            self._status.configure(
                text=f"{strings.PICK_TAP_TO_SELECT}. {strings.PICK_TOO_MANY.format(MAX_PHOTOS)}")
        else:
            self._status.configure(text=strings.PICK_TAP_TO_SELECT)
        self._update_badges()
        self._relayout(force=True)

    def _thumb_ready(self, item: _Item, image: Optional[Image.Image]) -> None:
        item.thumb = image
        if item.index in self._drawn and item.index < len(self._items) and self._items[item.index] is item:
            self._draw(item.index)

    # ---- Layout and drawing ----

    def _schedule_layout(self) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            self.after(30, self._layout_now)

    def _layout_now(self) -> None:
        self._refresh_pending = False
        self._relayout(force=False)

    def _relayout(self, force: bool) -> None:
        width = self._canvas.winfo_width()
        if width <= 1:
            return
        columns = max(3, width // MIN_CELL)
        cell = width // columns
        if force or columns != self._columns or cell != self._cell:
            self._columns, self._cell = columns, cell
            self._canvas.delete("all")
            self._drawn.clear()
            for item in self._items:
                item.photo = None
                item.photo_size = 0
            rows = -(-len(self._items) // columns)
            self._canvas.configure(scrollregion=(0, 0, width, rows * cell))
            if force:
                self._canvas.yview_moveto(0)
        self._refresh_visible()

    def _refresh_visible(self) -> None:
        if not self._items or not self._cell:
            return
        cell, columns = self._cell, self._columns
        top = self._canvas.canvasy(0)
        bottom = top + self._canvas.winfo_height()
        first_row = max(0, int(top // cell) - 1)
        last_row = int(bottom // cell) + 1
        first = first_row * columns
        last = min(len(self._items), (last_row + 1) * columns)

        for index in [i for i in self._drawn if not first <= i < last]:
            self._canvas.delete(f"i{index}")
            del self._drawn[index]
        keep_first = max(0, first_row - KEEP_ROWS) * columns
        keep_last = (last_row + 1 + KEEP_ROWS) * columns
        for index, item in enumerate(self._items):
            if (item.thumb is not None or item.photo is not None) and not keep_first <= index < keep_last:
                item.thumb = None
                item.photo = None
                item.requested = False

        for index in range(first, last):
            item = self._items[index]
            if item.thumb is None and not item.requested:
                item.requested = True
                self._loader.request(item)
            if index not in self._drawn:
                self._draw(index)

    def _draw(self, index: int) -> None:
        item = self._items[index]
        cell, columns, c = self._cell, self._columns, self._theme
        x, y = (index % columns) * cell, (index // columns) * cell
        tag = f"i{index}"
        self._canvas.delete(tag)
        self._drawn[index] = None
        pad = 1
        self._canvas.create_rectangle(x + pad, y + pad, x + cell - pad, y + cell - pad,
                                      fill=c["SurfaceContainer"], outline="", tags=tag)
        if item.thumb is not None:
            size = cell - 2 * pad
            if item.photo is None or item.photo_size != size:
                item.photo = ImageTk.PhotoImage(item.thumb.resize((size, size), Image.BILINEAR))
                item.photo_size = size
            self._canvas.create_image(x + pad, y + pad, image=item.photo, anchor="nw", tags=tag)
        if item.badge:
            self._canvas.create_rectangle(x + pad + 2, y + pad + 2, x + cell - pad - 2, y + cell - pad - 2,
                                          outline=c["Primary"], width=4, tags=tag)
            cx, cy = x + 20, y + 20
            self._canvas.create_oval(cx - 13, cy - 13, cx + 13, cy + 13, fill=c["Primary"], outline="#ffffff",
                                     width=2, tags=tag)
            self._canvas.create_text(cx, cy, text=str(item.badge), fill=c["OnPrimary"],
                                     font=("TkDefaultFont", 10, "bold"), tags=tag)

    def _canvas_yview(self, *args) -> None:
        self._canvas.yview(*args)
        self._refresh_visible()

    def _on_yscroll(self, first, last) -> None:
        self._bar.set(first, last)
        self._schedule_layout()

    def _on_wheel(self, event) -> None:
        up = event.num == 4 or getattr(event, "delta", 0) > 0
        self._canvas.yview_scroll(-3 if up else 3, "units")

    # ---- Selection ----

    def _on_click(self, event) -> None:
        if not self._cell:
            return
        col = int(self._canvas.canvasx(event.x) // self._cell)
        row = int(self._canvas.canvasy(event.y) // self._cell)
        index = row * self._columns + col
        if col >= self._columns or not 0 <= index < len(self._items):
            return
        path = self._items[index].path
        if path in self._selection:
            self._selection.remove(path)
        elif len(self._selection) >= MAX_SELECTION:
            self._selection_label.configure(text=strings.PICK_LIMIT.format(MAX_SELECTION))
            return
        else:
            self._selection.append(path)
        self._update_badges()
        self._update_selection_ui()

    def _update_badges(self) -> None:
        order: Dict[str, int] = {}
        for i, path in enumerate(self._selection):
            order.setdefault(path, i + 1)
        for index, item in enumerate(self._items):
            badge = order.get(item.path, 0)
            if badge != item.badge:
                item.badge = badge
                if index in self._drawn:
                    self._draw(index)

    def _update_selection_ui(self) -> None:
        count = len(self._selection)
        if count == 0:
            text = strings.PICK_TAP_TO_SELECT
        elif count == 1:
            text = strings.PICK_ONE.format(1)
        else:
            text = strings.PICK_MANY.format(count)
        self._selection_label.configure(text=text)
        state = ["!disabled"] if count else ["disabled"]
        self._done.state(state)
        self._clear.state(state)

    def _on_clear(self) -> None:
        self._selection.clear()
        self._update_badges()
        self._update_selection_ui()

    def _on_done(self) -> None:
        if self._selection:
            self.confirmed = True
            self._close()

    def _on_change_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, initialdir=self._settings.effective_photo_folder,
                                         mustexist=True)
        if not folder:
            return
        self._settings.photo_folder = folder
        self._settings.save()
        self._load(folder)

    def _close(self) -> None:
        self._scan_id += 1
        self._loader.close()
        self.destroy()
