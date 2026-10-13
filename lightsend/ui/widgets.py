"""Small Tk helpers: cross-thread dispatch, a scrolling page and wrapping labels."""

import queue
import sys
import tkinter as tk
import traceback
from tkinter import ttk
from typing import Callable


class Dispatcher:
    """Runs callables posted from worker threads on the Tk main thread."""

    def __init__(self, root: tk.Misc):
        self._root = root
        self._queue: "queue.SimpleQueue[Callable[[], None]]" = queue.SimpleQueue()
        self._closed = False
        root.after(30, self._pump)

    def post(self, fn: Callable, *args, **kwargs) -> None:
        self._queue.put(lambda: fn(*args, **kwargs))

    def close(self) -> None:
        self._closed = True

    def _pump(self) -> None:
        if self._closed:
            return
        # Rescheduled first so a modal dialog's nested event loop keeps pumping.
        try:
            self._root.after(30, self._pump)
        except tk.TclError:
            return
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except tk.TclError:
                pass  # the target widget was closed meanwhile
            except Exception:
                traceback.print_exc(file=sys.stderr)


class WrapLabel(ttk.Label):
    """A label whose text wraps to the width it is given."""

    def __init__(self, master, **kw):
        super().__init__(master, justify="left", **kw)
        self.bind("<Configure>", lambda e: self.configure(wraplength=max(1, e.width)))


class ScrollFrame(ttk.Frame):
    """A vertically scrolling page whose content is centred and at most max_width wide."""

    def __init__(self, master, max_width: int = 720, background: str = "", padding: int = 16):
        super().__init__(master)
        self._max_width = max_width
        self._canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=background)
        self._bar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self._canvas, padding=padding)
        self._window = self._canvas.create_window(0, 0, window=self.body, anchor="nw")
        self._canvas.bind("<Configure>", self._on_canvas)
        self.body.bind("<Configure>", self._on_body)
        for w in (self._canvas, self.body):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _on_canvas(self, event) -> None:
        width = min(event.width, self._max_width)
        self._canvas.itemconfigure(self._window, width=width)
        self._canvas.coords(self._window, (event.width - width) // 2, 0)
        self._update_bar()

    def _on_body(self, _event) -> None:
        self._canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(), self.body.winfo_reqheight()))
        self._update_bar()

    def _needs_scroll(self) -> bool:
        return self.body.winfo_reqheight() > self._canvas.winfo_height()

    def _update_bar(self) -> None:
        if self._needs_scroll():
            if not self._bar.winfo_ismapped():
                self._bar.pack(side="right", fill="y")
        else:
            self._bar.pack_forget()
            self._canvas.yview_moveto(0)

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<Button-4>", self._wheel)
        self._canvas.bind_all("<Button-5>", self._wheel)
        self._canvas.bind_all("<MouseWheel>", self._wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")
        self._canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event) -> None:
        if not self._needs_scroll():
            return
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            self._canvas.yview_scroll(-3, "units")
        else:
            self._canvas.yview_scroll(3, "units")


def center_on(window: tk.Toplevel, parent: tk.Misc, width: int, height: int, fixed_size: bool = True) -> None:
    """Centres window over parent; with fixed_size=False only its position is set."""
    parent.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    x = max(0, min(x, window.winfo_screenwidth() - width))
    y = max(0, min(y, window.winfo_screenheight() - height))
    window.geometry(f"{width}x{height}+{x}+{y}" if fixed_size else f"+{x}+{y}")
