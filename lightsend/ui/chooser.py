"""The "Send to" dialog: receivers found on the network, plus a typed address for when discovery
cannot reach the phone."""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple

from .. import network, strings
from ..discovery import Discovery
from ..util import Settings
from .theme import Theme
from .widgets import WrapLabel, center_on


class ChooseReceiverDialog(tk.Toplevel):
    def __init__(self, master, discovery: Discovery, settings: Settings, theme: Theme):
        super().__init__(master, background=theme["Surface"])
        self._discovery = discovery
        self._settings = settings
        self._theme = theme
        self.chosen_name: Optional[str] = None  # set when a discovered receiver was chosen
        self.typed_address: Optional[Tuple[str, int]] = None  # set when the user typed an address
        self.confirmed = False

        self.title(strings.APP_NAME)
        self.transient(master)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda e: self.destroy())

        pad = 24
        ttk.Label(self, text=strings.WIFI_CHOOSE_RECEIVER, style="Title.TLabel").pack(
            anchor="w", padx=pad, pady=(20, 12))
        self._searching = WrapLabel(self, style="Hint.TLabel")
        self._searching.pack(fill="x", padx=pad, pady=(0, 8))

        self._list = tk.Listbox(
            self, height=1, activestyle="none", exportselection=False, borderwidth=0, highlightthickness=0,
            background=theme["Surface"], foreground=theme["OnSurface"], font="TkDefaultFont",
            selectbackground=theme["SecondaryContainer"], selectforeground=theme["OnSecondaryContainer"])
        self._list.pack(fill="x", padx=pad)
        self._list.bind("<<ListboxSelect>>", lambda e: self._update_send())
        self._list.bind("<Double-Button-1>", self._on_double_click)

        WrapLabel(self, text=strings.ENTER_ADDRESS, style="Body.TLabel").pack(fill="x", padx=pad, pady=(16, 6))
        self._address = tk.StringVar(value=settings.last_address or "")
        self._entry = ttk.Entry(self, textvariable=self._address)
        self._entry.pack(fill="x", padx=pad)
        self._address.trace_add("write", lambda *_: self._on_address_changed())
        self._error = WrapLabel(self, text=strings.BAD_ADDRESS, style="Error.TLabel")

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=pad, pady=(20, 16))
        self._send = ttk.Button(buttons, text=strings.SEND, command=self._on_send, width=10)
        self._send.pack(side="right")
        ttk.Button(buttons, text=strings.CANCEL, style="Text.TButton", command=self.destroy).pack(
            side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: self._on_send())

        ttk.Frame(self, width=420, height=1).pack()
        self._names: list = []
        self._refresh()
        self._update_send()
        center_on(self, master, 420, self.winfo_reqheight(), fixed_size=False)
        self.wait_visibility()
        self.grab_set()
        if not self._names:
            self._entry.focus_set()
            self._entry.selection_range(0, "end")

    def show(self) -> bool:
        self.master.wait_window(self)
        return self.confirmed

    def _refresh(self) -> None:
        try:
            names = self._discovery.names()
            if names != self._names:
                selected = self._selected_name()
                self._names = names
                self._list.delete(0, "end")
                for n in names:
                    self._list.insert("end", "  " + n)
                self._list.configure(height=min(max(len(names), 1), 6))
                if selected in names:
                    self._list.selection_set(names.index(selected))
                self._update_send()
            self._searching.configure(text="" if names else strings.WIFI_NONE_FOUND)
            self.after(500, self._refresh)
        except tk.TclError:
            pass  # closed

    def _selected_name(self) -> Optional[str]:
        sel = self._list.curselection()
        return self._names[sel[0]] if sel else None

    def _update_send(self) -> None:
        enabled = self._selected_name() is not None or bool(self._address.get().strip())
        self._send.state(["!disabled"] if enabled else ["disabled"])

    def _on_address_changed(self) -> None:
        self._error.pack_forget()
        if self.focus_get() is self._entry and self._address.get():
            self._list.selection_clear(0, "end")
        self._update_send()

    def _on_double_click(self, _event) -> None:
        if self._selected_name() is not None:
            self._on_send()

    def _on_send(self) -> None:
        name = self._selected_name()
        if name is not None:
            self.chosen_name = name
            self.confirmed = True
            self.destroy()
            return
        if not self._address.get().strip():
            return
        parsed = network.try_parse_address(self._address.get())
        if parsed is None:
            self._error.pack(fill="x", padx=24, pady=(4, 0), after=self._entry)
            self._entry.focus_set()
            return
        self._settings.last_address = self._address.get().strip()
        self._settings.save()
        self.typed_address = parsed
        self.confirmed = True
        self.destroy()
