"""Picks the network adapters a phone on the same Wi-Fi/LAN can actually reach."""

import ctypes
import ctypes.util
import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import List, Optional, Tuple

_IFF_UP = 0x1
_IFF_LOOPBACK = 0x8
_IFF_RUNNING = 0x40
_IFF_MULTICAST = 0x1000


@dataclass(frozen=True)
class LanInterface:
    name: str
    index: int
    addresses: Tuple[str, ...]
    wireless: bool


class _SockAddr(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("data", ctypes.c_ubyte * 14)]


class _IfAddrs(ctypes.Structure):
    pass


_IfAddrs._fields_ = [
    ("next", ctypes.POINTER(_IfAddrs)),
    ("name", ctypes.c_char_p),
    ("flags", ctypes.c_uint),
    ("addr", ctypes.POINTER(_SockAddr)),
    ("netmask", ctypes.POINTER(_SockAddr)),
    ("ifu", ctypes.c_void_p),
    ("data", ctypes.c_void_p),
]


def _ipv4_addresses():
    """Yields (interface name, flags, address) for every IPv4 address via getifaddrs."""
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    head = ctypes.POINTER(_IfAddrs)()
    if libc.getifaddrs(ctypes.byref(head)) != 0:
        return
    try:
        node = head
        while node:
            entry = node.contents
            if entry.addr and entry.addr.contents.family == socket.AF_INET:
                raw = bytes(entry.addr.contents.data)[2:6]
                yield entry.name.decode(), entry.flags, socket.inet_ntoa(raw)
            node = entry.next
    finally:
        libc.freeifaddrs(head)


def _gateway_interfaces() -> set:
    """Interfaces that carry an IPv4 default route with a gateway."""
    names = set()
    try:
        with open("/proc/net/route") as f:
            next(f, None)
            for line in f:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000" and fields[2] != "00000000":
                    names.add(fields[0])
    except OSError:
        pass
    return names


def _usable(address: str) -> bool:
    ip = ipaddress.IPv4Address(address)
    return not ip.is_loopback and not ip.is_link_local


def lan_interfaces() -> List[LanInterface]:
    """Adapters that are up and have an IPv4 default gateway (the home network).

    Virtual bridges (Docker, libvirt) normally have no gateway and are skipped.
    """
    found = {}
    for name, flags, address in _ipv4_addresses():
        if not (flags & _IFF_UP and flags & _IFF_RUNNING and flags & _IFF_MULTICAST):
            continue
        if flags & _IFF_LOOPBACK or not _usable(address):
            continue
        found.setdefault(name, []).append(address)

    gateways = _gateway_interfaces()
    chosen = {n: a for n, a in found.items() if n in gateways} or found
    result = []
    for name, addresses in chosen.items():
        try:
            index = socket.if_nametoindex(name)
        except OSError:
            continue
        wireless = os.path.isdir(f"/sys/class/net/{name}/wireless")
        result.append(LanInterface(name, index, tuple(addresses), wireless))
    result.sort(key=lambda i: (not i.wireless, i.name))
    return result


def lan_ipv4_addresses() -> List[str]:
    return [a for i in lan_interfaces() for a in i.addresses]


def is_connected() -> bool:
    return len(lan_interfaces()) > 0


def device_name() -> str:
    """Name shown to the phones, e.g. "ubuntu-desktop"."""
    name = socket.gethostname()
    name = name.replace(".", "-") if name.strip() else "LightSend PC"
    return name[:40]


def try_parse_address(text: Optional[str]) -> Optional[Tuple[str, int]]:
    """Parses an address typed by the user, such as "192.168.1.20:41483" or "[fe80::1]:1234".

    Requires a full dotted IPv4 address, or [IPv6], and a port. Returns (host, port) or None.
    """
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("["):
        host, sep, port_text = text[1:].partition("]:")
        if not sep:
            return None
        try:
            host = str(ipaddress.IPv6Address(host))
        except ValueError:
            return None
    else:
        host, sep, port_text = text.partition(":")
        if not sep or ":" in port_text:
            return None
        parts = host.split(".")
        if len(parts) != 4:
            return None
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError:
            return None
    if not port_text.isascii() or not port_text.isdigit():
        return None
    port = int(port_text)
    if port <= 0 or port > 65535:
        return None
    return host, port
