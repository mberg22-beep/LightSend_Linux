"""Send screen, the PC counterpart of the Android MainActivity (Wi-Fi only)."""

import os
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from PIL import Image, ImageTk

from .. import imaging, network, strings
from ..discovery import Discovery
from ..transfer import Cancelled, Sender
from ..util import Settings, short_size
from .chooser import ChooseReceiverDialog
from .picker import MAX_SELECTION, PickerDialog
from .receive import ReceiveDialog
from .theme import Theme
from .widgets import Dispatcher, ScrollFrame, WrapLabel

GRID_COLUMNS_MANY = 3
TILE_SIZE = 256
SEARCH_TIMEOUT_S = 10.0
SEARCH_GRACE_S = 1.5


class PhotoTile:
    """A selected photo as shown in the send grid: what will be sent, its size and its place in the order."""

    def __init__(self, path: str, number: int, show_number: bool):
        self.path = path
        self.number = str(number) if show_number else ""
        self.image: Optional[Image.Image] = None
        self.label = strings.TILE_PREPARING
        self.failed = False
        self.canvas: Optional[tk.Canvas] = None
        self.photo: Optional[ImageTk.PhotoImage] = None


class MainWindow:
    def __init__(self, root: tk.Tk, theme: Theme, settings: Settings, dispatcher: Dispatcher,
                 drop_enabled: bool):
        self._root = root
        self._theme = theme
        self._settings = settings
        self._d = dispatcher
        self._drop_enabled = drop_enabled

        self._selected: List[str] = []
        self._tiles: List[PhotoTile] = []
        self._tile_layout: Tuple[int, int] = (0, 0)
        self._grid_columns = 2
        self._layout_pending = False
        self._encoded: Optional[imaging.Encoded] = None
        self._encode_generation = 0
        self._prepared_count = 0
        self._prepared_bytes = 0
        self._failure: Optional[Tuple[int, str]] = None
        self._sending = False
        self._send_quality = imaging.Quality.ORIGINAL
        self._cancel = threading.Event()
        self._sender: Optional[Sender] = None
        self._discovery: Optional[Discovery] = None
        self._search_started = 0.0
        self._first_seen: Optional[float] = None

        root.title(strings.APP_NAME)
        screen_h = root.winfo_screenheight()
        root.geometry(f"520x{min(900, max(500, screen_h - 120))}")
        root.minsize(400, 500)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        page = ScrollFrame(root, background=theme["Surface"])
        page.pack(fill="both", expand=True)
        b = page.body

        ttk.Label(b, text=strings.SEND_HEADING, style="Title.TLabel").pack(anchor="w")
        ttk.Label(b, text=strings.TRANSFER_METHOD).pack(anchor="w", pady=(8, 0))
        methods = ttk.Frame(b)
        methods.pack(fill="x")
        methods.columnconfigure((0, 1, 2), weight=1, uniform="m")
        self._method = tk.StringVar(value="wifi")
        for col, (value, text, enabled) in enumerate(
                (("wifi", strings.METHOD_WIFI, True), ("light", strings.METHOD_LIGHT, False),
                 ("nfc", strings.METHOD_NFC, False))):
            radio = ttk.Radiobutton(methods, text=text, value=value, variable=self._method)
            radio.grid(row=0, column=col, sticky="w")
            if not enabled:
                radio.state(["disabled"])

        self._choose_button = ttk.Button(b, text=strings.CHOOSE_PHOTOS, command=self._open_picker)
        self._choose_button.pack(fill="x", pady=(12, 0))
        self._selected_label = ttk.Label(b, text=strings.SELECTED_PHOTO)
        self._selected_label.pack(anchor="w", pady=(12, 0))

        self._empty_box = tk.Canvas(b, height=160, highlightthickness=0, background=theme["SecondaryContainer"],
                                    cursor="hand2")
        self._empty_box.pack(fill="x", pady=(4, 0))
        self._empty_box.create_text(0, 0, text=strings.TAP_TO_CHOOSE, fill=theme["OnSecondaryContainer"],
                                    tags="label")
        self._empty_box.bind("<Configure>", lambda e: self._empty_box.coords("label", e.width // 2, e.height // 2))
        self._empty_box.bind("<Button-1>", lambda e: self._open_picker())

        self._tile_frame = tk.Frame(b, background=theme["Surface"])
        self._tile_frame.bind("<Configure>", lambda e: self._schedule_tile_layout())

        self._info = WrapLabel(b, style="Body.TLabel", text=self._no_photo_text())
        self._info.pack(fill="x", pady=(8, 0))
        ttk.Label(b, text=strings.IMAGE_SIZE).pack(anchor="w", pady=(12, 0))
        self._quality = ttk.Combobox(b, state="readonly", values=[label for _, label, _ in imaging.QUALITIES])
        self._quality.current(0)
        self._quality.pack(fill="x")
        self._quality.bind("<<ComboboxSelected>>", self._on_quality_changed)
        self._send_button = ttk.Button(b, text=strings.SEND, command=self._on_send)
        self._send_button.state(["disabled"])
        self._send_button.pack(fill="x", pady=(8, 0))

        slot = ttk.Frame(b, height=8)
        slot.pack(fill="x", pady=(12, 4))
        slot.pack_propagate(False)
        self._progress = ttk.Progressbar(slot, maximum=1000)
        self._status = WrapLabel(b, style="Body.TLabel")
        self._status.pack(fill="x")
        self._receive_button = ttk.Button(b, text=strings.RECEIVE_MODE, style="Outlined.TButton",
                                          command=self._on_receive)
        self._receive_button.pack(fill="x", pady=(24, 0))
        WrapLabel(b, text=strings.WIFI_TIPS, style="Hint.TLabel").pack(fill="x", pady=(12, 0))

    def _no_photo_text(self) -> str:
        if self._drop_enabled:
            return strings.NO_PHOTO
        return strings.NO_PHOTO.replace(" (or drop them on this window)", "")

    @property
    def _quality_value(self) -> imaging.Quality:
        return imaging.QUALITIES[max(0, self._quality.current())][0]

    # ---- Choosing photos ----

    def _open_picker(self) -> None:
        if self._sending:
            return
        picker = PickerDialog(self._root, self._selected, self._settings, self._theme, self._d)
        if picker.show() and picker.selection:
            self._on_photos_selected(list(picker.selection))

    def on_drop(self, paths: List[str]) -> None:
        if self._sending:
            return
        photos = [p for p in paths if os.path.isfile(p) and imaging.is_photo(p)]
        if not photos:
            self._status.configure(text=strings.DROPPED_NO_PHOTOS)
            return
        self._status.configure(text=strings.PICK_LIMIT.format(MAX_SELECTION) if len(photos) > MAX_SELECTION else "")
        self._on_photos_selected(photos[:MAX_SELECTION])

    def _on_photos_selected(self, paths: List[str]) -> None:
        self._selected = paths
        many = len(paths) > 1
        self._tiles = [PhotoTile(p, i + 1, many) for i, p in enumerate(paths)]
        self._grid_columns = GRID_COLUMNS_MANY if many else 2
        self._selected_label.configure(
            text=strings.SELECTED_PHOTOS.format(len(paths)) if many else strings.SELECTED_PHOTO)
        self._empty_box.pack_forget()
        self._tile_frame.pack(fill="x", pady=(4, 0), before=self._info)
        self._tile_layout = (0, 0)
        self._layout_tiles()
        self._prepare_all()

    def _on_quality_changed(self, _event) -> None:
        if self._selected and not self._sending:
            self._prepare_all()

    # ---- Tiles ----

    def _schedule_tile_layout(self) -> None:
        if not self._layout_pending:
            self._layout_pending = True
            self._root.after(40, self._layout_tiles)

    def _layout_tiles(self) -> None:
        self._layout_pending = False
        if not self._tiles:
            return
        width = self._tile_frame.winfo_width()
        if width <= 1:
            width = 480
        cols = self._grid_columns
        size = max(60, width // cols - 4)
        if (cols, size) == self._tile_layout and all(t.canvas for t in self._tiles):
            return
        self._tile_layout = (cols, size)
        for child in self._tile_frame.winfo_children():
            child.destroy()
        for i, tile in enumerate(self._tiles):
            canvas = tk.Canvas(self._tile_frame, width=size, height=size, highlightthickness=0,
                               background=self._theme["SecondaryContainer"], cursor="hand2")
            canvas.grid(row=i // cols, column=i % cols, padx=2, pady=2)
            canvas.bind("<Button-1>", lambda e: self._open_picker())
            tile.canvas = canvas
            self._draw_tile(tile)

    def _draw_tile(self, tile: PhotoTile) -> None:
        canvas = tile.canvas
        if canvas is None or not canvas.winfo_exists():
            return
        size = self._tile_layout[1]
        c = self._theme
        canvas.delete("all")
        if tile.image is not None:
            tile.photo = ImageTk.PhotoImage(imaging.cover(tile.image, size))
            canvas.create_image(0, 0, image=tile.photo, anchor="nw")
        if tile.number:
            canvas.create_oval(5, 5, 31, 31, fill=c["Primary"], outline="#ffffff", width=2)
            canvas.create_text(18, 18, text=tile.number, fill=c["OnPrimary"], font=("TkDefaultFont", 10, "bold"))
        canvas.create_rectangle(0, size - 24, size, size, fill=c["TileFailed"] if tile.failed else c["TileLabel"],
                                outline="")
        canvas.create_text(size // 2, size - 12, text=tile.label, fill="#ffffff", font=("TkDefaultFont", 10))

    # ---- Preparing photos ----

    def _prepare_all(self) -> None:
        """Encodes every selected photo at the chosen size so each tile shows exactly what will be sent."""
        self._encode_generation += 1
        generation = self._encode_generation
        quality = self._quality_value
        tiles = list(self._tiles)
        self._encoded = None
        self._failure = None
        self._prepared_count = 0
        self._prepared_bytes = 0
        for tile in tiles:
            tile.label = strings.TILE_PREPARING
            tile.failed = False
            self._draw_tile(tile)
        self._update_send_enabled()
        self._update_info()
        threading.Thread(target=self._prepare_worker, args=(generation, quality, tiles),
                         name="LightSendPrepare", daemon=True).start()

    def _prepare_worker(self, generation: int, quality: imaging.Quality, tiles: List[PhotoTile]) -> None:
        for i, tile in enumerate(tiles):
            if generation != self._encode_generation:
                return
            if tile.image is None:
                self._d.post(self._apply_thumbnail, generation, tile,
                             imaging.try_load_thumbnail(tile.path, TILE_SIZE))
            try:
                encoded = imaging.encode(tile.path, quality)
                preview = None if quality == imaging.Quality.ORIGINAL else imaging.try_load(encoded.data, TILE_SIZE)
            except Exception as e:  # a bad file must not stop the other photos
                self._d.post(self._apply_failure, generation, i, tile, str(e))
                continue
            self._d.post(self._apply_encoded, generation, i, tile, encoded if i == 0 else None,
                         len(encoded.data), preview)

    def _apply_thumbnail(self, generation: int, tile: PhotoTile, image: Optional[Image.Image]) -> None:
        if generation != self._encode_generation:
            return
        if tile.image is None:
            tile.image = image
            self._draw_tile(tile)

    def _apply_encoded(self, generation: int, index: int, tile: PhotoTile, first: Optional[imaging.Encoded],
                       size: int, preview: Optional[Image.Image]) -> None:
        if generation != self._encode_generation:
            return
        if first is not None:
            self._encoded = first
        if preview is not None:
            tile.image = preview
        tile.label = short_size(size)
        self._prepared_bytes += size
        self._prepared_count += 1
        self._draw_tile(tile)
        self._update_send_enabled()
        self._update_info()

    def _apply_failure(self, generation: int, index: int, tile: PhotoTile, message: str) -> None:
        if generation != self._encode_generation:
            return
        tile.label = strings.TILE_FAILED
        tile.failed = True
        if self._failure is None:
            self._failure = (index, message)
        self._prepared_count += 1
        self._draw_tile(tile)
        self._update_send_enabled()
        self._update_info()

    def _update_info(self) -> None:
        count = len(self._selected)
        if count == 0:
            text = self._no_photo_text()
        elif self._failure is not None:
            index, message = self._failure
            text = (strings.SEND_INFO_MULTI_FAILED.format(index + 1, message) if count > 1
                    else strings.ENCODE_FAILED.format(message))
        elif count > 1:
            text = (strings.SEND_INFO_MULTI_COUNTING.format(count, self._prepared_count)
                    if self._prepared_count < count
                    else strings.SEND_INFO_MULTI.format(count, short_size(self._prepared_bytes)))
        elif self._encoded is not None:
            text = strings.SEND_INFO.format(self._encoded.name, short_size(len(self._encoded.data)))
        else:
            text = strings.PREPARING
        self._info.configure(text=text)

    def _update_send_enabled(self) -> None:
        enabled = self._sending or (self._encoded is not None and self._failure is None)
        self._send_button.state(["!disabled"] if enabled else ["disabled"])

    # ---- Sending ----

    def _on_send(self) -> None:
        if self._sending:
            self._stop()
        else:
            self._start_wifi()

    def _stop(self) -> None:
        self._cancel.set()
        sender = self._sender
        if sender:
            sender.cancel()

    def _set_sending_ui(self, sending: bool) -> None:
        self._sending = sending
        self._send_button.configure(text=strings.STOP if sending else strings.SEND)
        self._update_send_enabled()
        state = ["disabled"] if sending else ["!disabled"]
        self._choose_button.state(state)
        self._quality.state(["disabled"] if sending else ["!disabled", "readonly"])
        self._receive_button.state(state)

    def _show_progress_bar(self, visible: bool) -> None:
        if visible:
            self._progress.pack(fill="x")
        else:
            self._progress.pack_forget()

    def _start_wifi(self) -> None:
        if self._encoded is None:
            return
        if not network.is_connected():
            self._status.configure(text=strings.WIFI_NOT_CONNECTED)
            return
        self._cancel = threading.Event()
        self._send_quality = self._quality_value
        self._set_sending_ui(True)
        self._show_progress_bar(False)
        self._status.configure(text=strings.WIFI_SEARCHING)
        discovery = Discovery()
        try:
            discovery.start()
        except OSError as e:
            self._status.configure(text=strings.WIFI_FAILED.format(e))
            self._finish(False)
            return
        self._discovery = discovery
        self._search_started = time.monotonic()
        self._first_seen = None
        self._search_tick()

    def _search_tick(self) -> None:
        """Looks for receivers like the phone app does (up to 10 s, then 1.5 s more once one answers)."""
        discovery = self._discovery
        if self._cancel.is_set() or discovery is None:
            self._status.configure(text=strings.SEND_STOPPED)
            self._finish(False)
            return
        now = time.monotonic()
        if discovery.names() and self._first_seen is None:
            self._first_seen = now
        if (now - self._search_started >= SEARCH_TIMEOUT_S
                or (self._first_seen is not None and now - self._first_seen >= SEARCH_GRACE_S)):
            self._search_done(discovery)
            return
        self._root.after(250, self._search_tick)

    def _search_done(self, discovery: Discovery) -> None:
        """Sends straight to a single receiver; otherwise asks, offering a typed address as a fallback."""
        names = discovery.names()
        if len(names) == 1:
            self._status.configure(text=strings.WIFI_CONNECTING.format(names[0]))
            self._begin_transfer(discovery, names[0], None)
            return
        if not names:
            self._status.configure(text=strings.WIFI_NONE_FOUND)
        dialog = ChooseReceiverDialog(self._root, discovery, self._settings, self._theme)
        confirmed = dialog.show()
        if not confirmed or self._cancel.is_set():
            if names or self._cancel.is_set():
                self._status.configure(text=strings.SEND_STOPPED)
            self._finish(False)
            return
        if dialog.typed_address is not None:
            host, port = dialog.typed_address
            label = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            self._begin_transfer(discovery, label, dialog.typed_address)
            return
        name = dialog.chosen_name or ""
        self._status.configure(text=strings.WIFI_CONNECTING.format(name))
        self._begin_transfer(discovery, name, None)

    def _begin_transfer(self, discovery: Discovery, name: str, address: Optional[Tuple[str, int]]) -> None:
        threading.Thread(target=self._transfer_worker, args=(discovery, name, address),
                         name="LightSendSend", daemon=True).start()

    def _transfer_worker(self, discovery: Discovery, name: str, address: Optional[Tuple[str, int]]) -> None:
        success = False
        try:
            if address is None:
                address = discovery.resolve(name, self._cancel)
            success = self._send_all(name, address)
        except Cancelled:
            self._d.post(self._status.configure, text=strings.SEND_STOPPED)
        except Exception as e:
            if self._cancel.is_set():
                self._d.post(self._status.configure, text=strings.SEND_STOPPED)
            else:
                self._d.post(self._status.configure, text=strings.WIFI_FAILED.format(e))
        finally:
            self._d.post(self._finish, success)

    def _send_all(self, name: str, address: Tuple[str, int]) -> bool:
        photos = list(self._selected)
        total = len(photos)
        quality = self._send_quality
        sender = Sender()
        self._sender = sender
        status = self._status
        post = self._d.post
        post(self._show_progress_bar, True)

        for i, path in enumerate(photos):
            if self._cancel.is_set():
                raise Cancelled()

            def wrap(text: str, i=i) -> str:
                return strings.MULTI_PROGRESS.format(i + 1, total, text) if total > 1 else text

            if i == 0 and self._encoded is not None:
                photo = self._encoded
            else:
                post(status.configure, text=strings.MULTI_PREPARING.format(i + 1, total))
                photo = imaging.encode(path, quality)

            post(self._progress.configure, value=0)
            post(status.configure, text=wrap(strings.WIFI_CONNECTING.format(name)))
            last_ui = [0.0]

            def on_progress(sent: int, size: int, wrap=wrap) -> None:
                now = time.monotonic()
                if now - last_ui[0] < 0.1 and sent < size:
                    return
                last_ui[0] = now
                post(self._progress.configure, value=1000.0 * sent / max(1, size))
                post(status.configure,
                     text=wrap(strings.WIFI_SEND_PROGRESS.format(name, short_size(sent), short_size(size))))

            if not sender.send(address, photo.name, photo.data, on_progress):
                post(status.configure, text=wrap(strings.WIFI_REJECTED.format(name)))
                return False

        post(self._progress.configure, value=1000)
        post(status.configure, text=strings.WIFI_SENT_MULTI.format(total, name) if total > 1
             else strings.WIFI_SENT.format(name))
        return True

    def _finish(self, success: bool) -> None:
        discovery, self._discovery = self._discovery, None
        if discovery:
            discovery.stop()
        self._sender = None
        if not success:
            self._show_progress_bar(False)
        self._set_sending_ui(False)

    # ---- Receiving ----

    def _on_receive(self) -> None:
        if self._sending:
            return
        ReceiveDialog(self._root, self._theme, self._d).show()

    def _on_close(self) -> None:
        self._stop()
        discovery = self._discovery
        if discovery:
            discovery.stop()
        self._d.close()
        self._root.destroy()
