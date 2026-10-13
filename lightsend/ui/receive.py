"""Wi-Fi receive mode, the PC counterpart of WifiReceiveActivity. Saves photos to Pictures/LightSend."""

import os
import shutil
import subprocess
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

from PIL import Image, ImageTk

from .. import imaging, network, strings
from ..discovery import Advertiser
from ..transfer import ReceiverListener, ReceiverServer
from ..util import default_receive_folder, save_received, short_size
from .theme import Theme
from .widgets import Dispatcher, ScrollFrame, WrapLabel, center_on

_NETWORK_POLL_MS = 3000


class ReceiveDialog(tk.Toplevel, ReceiverListener):
    def __init__(self, master, theme: Theme, dispatcher: Dispatcher):
        tk.Toplevel.__init__(self, master, background=theme["Surface"])
        self._dispatcher = dispatcher
        self._server = ReceiverServer(self)
        self._advertiser = Advertiser()
        self._folder = default_receive_folder()
        self._port = 0
        self._saved_count = 0
        self._last_saved: Optional[str] = None
        self._incoming_name = ""
        self._clock = 0.0
        self._last_progress_ui = 0.0
        self._network_signature = None
        self._result_photo: Optional[ImageTk.PhotoImage] = None
        self._closed = False

        self.title(strings.WIFI_RECEIVE_TITLE)
        self.transient(master)
        self.minsize(380, 420)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())

        page = ScrollFrame(self, background=theme["Surface"])
        page.pack(fill="both", expand=True)
        body = page.body
        ttk.Label(body, text=strings.WIFI_RECEIVE_TITLE, style="Title.TLabel").pack(anchor="w")
        WrapLabel(body, text=strings.WIFI_RX_HINT.format(self._folder), style="Hint.TLabel").pack(
            fill="x", pady=(8, 0))
        self._status = WrapLabel(body, text=strings.WIFI_STARTING, style="Medium.TLabel")
        self._status.pack(fill="x", pady=(16, 0))
        self._network_button = ttk.Button(body, text=strings.OPEN_NETWORK_SETTINGS, style="Outlined.TButton",
                                          command=self._on_network_settings)
        self._progress = ttk.Progressbar(body, maximum=1000)
        self._progress_text = WrapLabel(body, style="Body.TLabel")
        self._result_image = ttk.Label(body, anchor="center")
        self._result_text = WrapLabel(body, style="Body.TLabel")
        self._buttons = ttk.Frame(body)
        self._buttons.columnconfigure((0, 1), weight=1, uniform="b")
        ttk.Button(self._buttons, text=strings.RECEIVE_ANOTHER, command=self._on_receive_another).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(self._buttons, text=strings.OPEN_FOLDER, style="Outlined.TButton",
                   command=self._on_open_folder).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        center_on(self, master, 520, 760)
        self.wait_visibility()
        self.grab_set()
        self._start()

    def show(self) -> None:
        self.master.wait_window(self)

    # ---- Lifecycle ----

    def _start(self) -> None:
        try:
            self._port = self._server.start()
        except OSError as e:
            self._status.configure(text=strings.WIFI_FAILED.format(e))
            return
        self._network_signature = network.lan_interfaces()
        self._advertise()
        self.after(_NETWORK_POLL_MS, self._poll_network)

    def _close(self) -> None:
        self._closed = True
        self._advertiser.stop()
        self._server.stop()
        self.destroy()

    def _poll_network(self) -> None:
        if self._closed:
            return
        signature = network.lan_interfaces()
        if signature != self._network_signature:
            self._network_signature = signature
            self._advertise()
        self.after(_NETWORK_POLL_MS, self._poll_network)

    def _advertise(self) -> None:
        """(Re)announces this PC; called at start and whenever the network changes."""
        if self._closed or self._port == 0:
            return
        connected = network.is_connected()
        if connected:
            self._network_button.pack_forget()
        elif not self._network_button.winfo_ismapped():
            self._network_button.pack(fill="x", pady=(12, 0), after=self._status)
        if not connected:
            self._advertiser.stop()
            self._status.configure(text=strings.WIFI_NOT_CONNECTED)
            return
        name = network.device_name()
        addresses = [f"{a}:{self._port}" for a in network.lan_ipv4_addresses()]
        waiting = strings.WIFI_VISIBLE_AS.format(name, ", ".join(addresses) if addresses else f"port {self._port}")
        try:
            self._advertiser.start(name, self._port)
            if not self._progress.winfo_ismapped():
                self._status.configure(text=waiting)
        except OSError as e:
            self._status.configure(text=waiting + "\n" + strings.WIFI_ADVERTISE_FAILED.format(e))

    # ---- ReceiverListener (called on the server thread) ----

    def on_incoming(self, name: str, size: int, sender: str) -> None:
        self._clock = time.monotonic()
        self._last_progress_ui = 0.0
        self._incoming_name = name
        self._dispatcher.post(self._show_incoming, name, size, sender)

    def on_progress(self, received: int, total: int) -> None:
        now = time.monotonic()
        if now - self._last_progress_ui < 0.1 and received < total:
            return
        self._last_progress_ui = now
        elapsed = now - self._clock
        speed = int(received / elapsed) if elapsed > 0 else 0
        self._dispatcher.post(self._show_progress, self._incoming_name, received, total, speed)

    def on_received(self, name: str, data: bytes) -> None:
        # Saved here, on the server thread, so photos sent back-to-back are written in order.
        path = error = None
        try:
            path = save_received(self._folder, name, data)
        except OSError as e:
            error = str(e)
        preview = imaging.try_load(data, 960)
        self._dispatcher.post(self._show_result, len(data), path, error, preview)

    def on_failed(self, message: str) -> None:
        self._dispatcher.post(self._show_failed, message)

    # ---- UI updates (main thread) ----

    def _show_incoming(self, name: str, size: int, sender: str) -> None:
        self._status.configure(text=strings.WIFI_RECEIVING_FROM.format(sender))
        self._progress.configure(value=0)
        self._progress.pack(fill="x", pady=(16, 4), after=self._status)
        self._progress_text.pack(fill="x", after=self._progress)
        self._progress_text.configure(
            text=strings.RX_PROGRESS.format(name, short_size(0), short_size(size), short_size(0)))

    def _show_progress(self, name: str, received: int, total: int, speed: int) -> None:
        self._progress.configure(value=1000.0 * received / max(1, total))
        self._progress_text.configure(
            text=strings.RX_PROGRESS.format(name, short_size(received), short_size(total), short_size(speed)))

    def _hide_progress(self) -> None:
        self._progress.pack_forget()
        self._progress_text.pack_forget()

    def _show_failed(self, message: str) -> None:
        self._hide_progress()
        self._status.configure(text=strings.WIFI_FAILED.format(message))

    def _show_result(self, size: int, path: Optional[str], error: Optional[str],
                     preview: Optional[Image.Image]) -> None:
        self._hide_progress()
        if preview is not None:
            fitted = preview.copy()
            fitted.thumbnail((max(self.winfo_width() - 64, 120), 320), Image.LANCZOS)
            self._result_photo = ImageTk.PhotoImage(fitted)
            self._result_image.configure(image=self._result_photo)
            self._result_image.pack(fill="x", pady=(16, 0))
        else:
            self._result_image.pack_forget()
        self._result_text.pack(fill="x", pady=(8, 0))
        self._buttons.pack(fill="x", pady=(16, 0))
        if path is None:
            self._status.configure(text=strings.RX_COMPLETE)
            self._result_text.configure(text=strings.RX_SAVE_FAILED.format(error))
            return
        self._saved_count += 1
        self._last_saved = path
        self._status.configure(text=strings.RX_SAVED_COUNT.format(self._saved_count, os.path.basename(path)))
        self._result_text.configure(text=strings.RX_SAVED.format(short_size(size), path))

    # ---- Buttons ----

    def _on_receive_another(self) -> None:
        self._result_image.pack_forget()
        self._result_photo = None
        self._result_text.pack_forget()
        self._buttons.pack_forget()
        self._advertise()

    def _on_open_folder(self) -> None:
        if self._last_saved and os.path.isfile(self._last_saved):
            target = os.path.dirname(self._last_saved)
        else:
            try:
                os.makedirs(self._folder, exist_ok=True)
            except OSError as e:
                self._result_text.configure(text=str(e))
                return
            target = self._folder
        self._launch(["xdg-open", target])

    def _on_network_settings(self) -> None:
        for cmd in (["gnome-control-center", "network"], ["nm-connection-editor"],
                    ["systemsettings", "kcm_networkmanagement"]):
            if shutil.which(cmd[0]):
                self._launch(cmd)
                return

    def _launch(self, cmd: list) -> None:
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self._result_text.configure(text=str(e))
