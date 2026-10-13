"""TCP sender and receiver for the LightSend wire format."""

import errno
import select
import socket
import threading
import time
from typing import Callable, Optional, Tuple

from . import protocol

READ_TIMEOUT_S = 20.0
CONNECT_TIMEOUT_S = 5.0


class Cancelled(Exception):
    pass


class ReceiverListener:
    """Callbacks are made on the server thread."""

    def on_incoming(self, name: str, size: int, sender: str) -> None: ...
    def on_progress(self, received: int, total: int) -> None: ...
    def on_received(self, name: str, data: bytes) -> None: ...
    def on_failed(self, message: str) -> None: ...


class ReceiverServer:
    """Accepts senders one at a time on a TCP port."""

    def __init__(self, listener: ReceiverListener):
        self._listener = listener
        self._server: Optional[socket.socket] = None
        self._running = False

    @property
    def port(self) -> int:
        return self._server.getsockname()[1] if self._server else 0

    def start(self, bind_address: str = "0.0.0.0", port: int = 0) -> int:
        """Starts listening on port (0 = any free port) and returns the port."""
        self.stop()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((bind_address, port))
        server.listen(4)
        server.settimeout(0.5)
        self._server = server
        self._running = True
        threading.Thread(target=self._accept_loop, args=(server,), name="LightSendWifiServer",
                         daemon=True).start()
        return self.port

    def stop(self) -> None:
        self._running = False
        server, self._server = self._server, None
        if server:
            try:
                server.close()
            except OSError:
                pass

    def _accept_loop(self, server: socket.socket) -> None:
        while self._running:
            try:
                client, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                self._handle(client, addr[0])

    def _handle(self, client: socket.socket, sender: str) -> None:
        client.settimeout(READ_TIMEOUT_S)
        try:
            received = protocol.receive(
                client,
                on_start=lambda name, size: self._listener.on_incoming(name, size, sender),
                on_progress=self._listener.on_progress)
            self._listener.on_received(received.name, received.data)
        except (OSError, EOFError) as e:
            if self._running:
                self._listener.on_failed(str(e) or e.__class__.__name__)


class Sender:
    """Connects to a receiver and streams one file."""

    def __init__(self):
        self._socket: Optional[socket.socket] = None
        self._cancelled = False

    def send(self, address: Tuple[str, int], name: str, data: bytes,
             on_progress: Optional[Callable[[int, int], None]] = None) -> bool:
        """Blocks until the receiver verified the file. Raises IOError on network errors or
        Cancelled after cancel()."""
        self._cancelled = False
        host, port = address
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        self._socket = s
        try:
            if not self._connect(s, address):
                raise IOError(f"Could not connect to {host}:{port}")
            s.settimeout(READ_TIMEOUT_S)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return protocol.send(s, name, data, on_progress)
        except OSError as e:
            if self._cancelled:
                raise Cancelled() from e
            raise
        finally:
            self._socket = None
            s.close()

    def _connect(self, s: socket.socket, address: Tuple[str, int]) -> bool:
        """Connects in short slices so cancel() takes effect while connecting."""
        s.setblocking(False)
        if s.connect_ex(address) not in (0, errno.EINPROGRESS):
            return False
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._cancelled:
                raise Cancelled()
            _, writable, _ = select.select([], [s], [], 0.1)
            if writable:
                return s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0
        return False

    def cancel(self) -> None:
        """Aborts a transfer in progress from another thread."""
        self._cancelled = True
        s = self._socket
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
