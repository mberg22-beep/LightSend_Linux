"""Advertising this PC as a receiver, and finding receivers, on the local network."""

import select
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import mdns, network
from .protocol import SERVICE_TYPE
from .transfer import Cancelled

_SERVICE_NAME = f"{SERVICE_TYPE}.local"
_TTL = 120


class _Runner:
    """Owns one multicast endpoint per LAN interface and a thread that services them."""

    def __init__(self):
        self._endpoints: List[Tuple[mdns.Endpoint, network.LanInterface]] = []
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _open(self) -> None:
        for nic in network.lan_interfaces():
            try:
                self._endpoints.append((mdns.Endpoint(nic.addresses[0], nic.index), nic))
            except OSError:
                continue
        if not self._endpoints:
            raise OSError("No network interface could be opened for multicast DNS")
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=type(self).__name__, daemon=True)
        self._thread.start()

    def _close(self) -> None:
        self._running = False
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(2)
        for endpoint, _ in self._endpoints:
            endpoint.close()
        self._endpoints = []

    def _loop(self) -> None:
        by_fd = {e.socket.fileno(): (e, nic) for e, nic in self._endpoints}
        sockets = [e.socket for e, _ in self._endpoints]
        while self._running:
            self._tick()
            try:
                ready, _, _ = select.select(sockets, [], [], 0.25)
            except (OSError, ValueError):
                break
            for s in ready:
                endpoint, nic = by_fd[s.fileno()]
                while True:
                    try:
                        payload, source = s.recvfrom(9000)
                    except (BlockingIOError, InterruptedError):
                        break
                    except OSError:
                        break
                    try:
                        message = mdns.parse_message(payload)
                    except Exception:  # malformed packets from other devices are ignored
                        continue
                    self._on_message(endpoint, nic, message, source)

    def _tick(self) -> None: ...

    def _on_message(self, endpoint: mdns.Endpoint, nic: network.LanInterface,
                    message: mdns.Message, source: Tuple[str, int]) -> None: ...


class Advertiser(_Runner):
    """Answers mDNS queries for this PC separately on each LAN adapter, so every network only
    hears this PC's address on that network (a phone must not resolve an address it cannot reach)."""

    def __init__(self):
        super().__init__()
        self._instance = ""
        self._host = ""
        self._port = 0
        self._label = ""
        self._announce_at: List[float] = []

    def start(self, name: str, port: int) -> str:
        """Starts answering queries and announces the service. Returns the advertised name."""
        self.stop()
        self._label = name
        self._instance = mdns.instance_name(name, SERVICE_TYPE)
        safe_host = "".join(c if c.isalnum() or c == "-" else "-" for c in name).strip("-") or "lightsend"
        self._host = f"{safe_host}-ls.local"
        self._port = port
        self._open()
        self._announce_at = [time.monotonic(), time.monotonic() + 1.0]
        return name

    def stop(self) -> None:
        if self._running:
            for endpoint, nic in self._endpoints:
                answers, additional = self._records(nic, ttl=0)
                endpoint.send_multicast(mdns.build_response(answers + additional, []))
        self._close()

    def _records(self, nic: network.LanInterface, ttl: int = _TTL):
        ptr = mdns.Record(_SERVICE_NAME, mdns.TYPE_PTR, ttl, self._instance)
        srv = mdns.Record(self._instance, mdns.TYPE_SRV, ttl, (0, 0, self._port, self._host))
        txt = mdns.Record(self._instance, mdns.TYPE_TXT, ttl, b"\x00")
        a = [mdns.Record(self._host, mdns.TYPE_A, ttl, addr) for addr in nic.addresses]
        return [ptr], [srv, txt, *a]

    def _tick(self) -> None:
        if self._announce_at and time.monotonic() >= self._announce_at[0]:
            self._announce_at.pop(0)
            for endpoint, nic in self._endpoints:
                answers, additional = self._records(nic)
                endpoint.send_multicast(mdns.build_response(answers + additional, []))

    def _on_message(self, endpoint, nic, message, source) -> None:
        if message.is_response or not message.questions:
            return
        ptr, additional = self._records(nic)
        srv, txt, *addresses = additional
        answers: List[mdns.Record] = []
        extra: List[mdns.Record] = []
        for name, qtype in message.questions:
            lname = name.lower()
            if lname == _SERVICE_NAME and qtype in (mdns.TYPE_PTR, mdns.TYPE_ANY):
                answers += ptr
                extra += [srv, txt, *addresses]
            elif lname == self._instance.lower():
                if qtype in (mdns.TYPE_SRV, mdns.TYPE_ANY):
                    answers.append(srv)
                    extra += addresses
                if qtype in (mdns.TYPE_TXT, mdns.TYPE_ANY):
                    answers.append(txt)
            elif lname == self._host.lower() and qtype in (mdns.TYPE_A, mdns.TYPE_ANY):
                answers += addresses
        if not answers:
            return
        extra = [r for r in extra if r not in answers]
        if source[1] == mdns.MDNS_PORT:
            endpoint.send_multicast(mdns.build_response(answers, extra))
        else:
            endpoint.send_unicast(mdns.build_response(answers, extra, message.ident), source)


class Discovery(_Runner):
    """Finds receivers (phones or PCs running LightSend) on the local network."""

    REQUERY_S = 2.0

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._found: Dict[str, Tuple[str, float]] = {}  # display name -> (instance name, expiry)
        self._srv: Dict[str, Tuple[str, int]] = {}  # instance (lower) -> (target (lower), port)
        self._hosts: Dict[str, str] = {}  # host (lower) -> address
        self._next_query = 0.0

    def start(self) -> None:
        self.stop()
        with self._lock:
            self._found.clear()
        self._next_query = 0.0
        self._open()

    def stop(self) -> None:
        self._close()

    def names(self) -> List[str]:
        now = time.monotonic()
        with self._lock:
            names = [n for n, (_, expiry) in self._found.items() if expiry > now]
        return sorted(names, key=str.casefold)

    def resolve(self, name: str, cancel: Optional[threading.Event] = None) -> Tuple[str, int]:
        """Looks up the address and port of a receiver found by names()."""
        with self._lock:
            entry = self._found.get(name)
        if entry is None:
            raise IOError(f"{name} is no longer available")
        instance = entry[0]
        key = instance.lower()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self._lock:
                srv = self._srv.get(key)
                host = self._hosts.get(srv[0]) if srv else None
            if srv and host:
                return host, srv[1]
            if srv:
                self._query([(srv[0], mdns.TYPE_A)])
            else:
                self._query([(instance, mdns.TYPE_SRV)])
            if cancel is not None:
                if cancel.wait(0.3):
                    raise Cancelled()
            else:
                time.sleep(0.3)
        raise IOError(f"Could not find {name}")

    def _query(self, questions) -> None:
        payload = mdns.build_query(questions)
        for endpoint, _ in list(self._endpoints):
            endpoint.send_multicast(payload)

    def _tick(self) -> None:
        # Phones answer the first query quickly, but asking again catches slow responders.
        if time.monotonic() >= self._next_query:
            self._next_query = time.monotonic() + self.REQUERY_S
            self._query([(_SERVICE_NAME, mdns.TYPE_PTR)])

    def _on_message(self, endpoint, nic, message, source) -> None:
        if not message.is_response:
            return
        now = time.monotonic()
        with self._lock:
            for r in message.records:
                if r.rtype == mdns.TYPE_PTR:
                    label = mdns.split_instance(r.data, SERVICE_TYPE)  # type: ignore[arg-type]
                    if label is None or not self._is_service_name(r.name):
                        continue
                    if r.ttl == 0:
                        self._found.pop(label, None)
                    else:
                        self._found[label] = (r.data, now + min(r.ttl, 4500))  # type: ignore[assignment]
                elif r.rtype == mdns.TYPE_SRV:
                    _, _, port, target = r.data  # type: ignore[misc]
                    self._srv[r.name.lower()] = (target.lower(), port)
                elif r.rtype == mdns.TYPE_A and r.ttl > 0:
                    self._hosts[r.name.lower()] = r.data  # type: ignore[assignment]

    @staticmethod
    def _is_service_name(name: str) -> bool:
        return name.lower().rstrip(".") == _SERVICE_NAME

    @staticmethod
    def is_lightsend(instance: str) -> bool:
        """The network is full of other services (Chromecasts, printers...), so keep only
        "<name>._lightsend._tcp.local"."""
        return mdns.split_instance(instance, SERVICE_TYPE) is not None
