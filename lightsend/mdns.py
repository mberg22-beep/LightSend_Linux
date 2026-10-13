"""Minimal multicast DNS (RFC 6762) / DNS-SD (RFC 6763) with no third-party dependencies.

Only what LightSend needs: PTR/SRV/TXT/A records, answering queries for one service instance,
and querying for instances of one service type.
"""

import socket
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
TYPE_ANY = 255
CLASS_IN = 1
CACHE_FLUSH = 0x8000

_IP_MULTICAST_ALL = 49  # not exported by the socket module


@dataclass
class Record:
    name: str
    rtype: int
    ttl: int
    # PTR: target name, SRV: (priority, weight, port, target), A: dotted address, TXT: bytes
    data: object


@dataclass
class Message:
    ident: int
    is_response: bool
    questions: List[Tuple[str, int]]
    records: List[Record]


def _labels(name: str) -> List[str]:
    """Splits a dotted name; a backslash escapes a dot (or backslash) inside a label."""
    labels: List[str] = []
    current: List[str] = []
    i = 0
    while i < len(name):
        c = name[i]
        if c == "\\" and i + 1 < len(name):
            current.append(name[i + 1])
            i += 2
            continue
        if c == ".":
            labels.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    if current:
        labels.append("".join(current))
    return [l for l in labels if l]


def _escape(label: str) -> str:
    return label.replace("\\", "\\\\").replace(".", "\\.")


def encode_name(name: str) -> bytes:
    out = bytearray()
    for label in _labels(name):
        raw = label.encode("utf-8")[:63]
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def instance_name(label: str, service_type: str) -> str:
    """Builds "<label>._type.local"; dots inside the label stay part of the label."""
    return f"{_escape(label)}.{service_type}.local"


def split_instance(name: str, service_type: str) -> Optional[str]:
    """Returns the instance label of "<label>._type.local", or None if it is another service."""
    labels = _labels(name)
    type_labels = service_type.split(".")
    if len(labels) < len(type_labels) + 1:
        return None
    for i, t in enumerate(type_labels):
        if labels[i + 1].lower() != t.lower():
            return None
    return labels[0]


def _decode_name(data: bytes, pos: int) -> Tuple[str, int]:
    labels = []
    end = None
    jumps = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated name")
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                raise ValueError("truncated pointer")
            if end is None:
                end = pos + 2
            pos = ((length & 0x3F) << 8) | data[pos + 1]
            jumps += 1
            if jumps > 32:
                raise ValueError("pointer loop")
            continue
        pos += 1
        labels.append(_escape(data[pos:pos + length].decode("utf-8", "replace")))
        pos += length
    return ".".join(labels), (end if end is not None else pos)


def parse_message(data: bytes) -> Message:
    if len(data) < 12:
        raise ValueError("short packet")
    ident, flags, qd, an, ns, ar = struct.unpack(">6H", data[:12])
    pos = 12
    questions = []
    for _ in range(qd):
        name, pos = _decode_name(data, pos)
        qtype, _qclass = struct.unpack(">HH", data[pos:pos + 4])
        pos += 4
        questions.append((name, qtype))

    records = []
    for _ in range(an + ns + ar):
        name, pos = _decode_name(data, pos)
        rtype, _rclass, ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        rdata_end = pos + rdlen
        if rdata_end > len(data):
            raise ValueError("truncated record")
        value: object = None
        if rtype == TYPE_A and rdlen == 4:
            value = socket.inet_ntoa(data[pos:rdata_end])
        elif rtype == TYPE_PTR:
            value, _ = _decode_name(data, pos)
        elif rtype == TYPE_SRV and rdlen >= 7:
            priority, weight, port = struct.unpack(">HHH", data[pos:pos + 6])
            target, _ = _decode_name(data, pos + 6)
            value = (priority, weight, port, target)
        elif rtype == TYPE_TXT:
            value = data[pos:rdata_end]
        if value is not None:
            records.append(Record(name, rtype, ttl, value))
        pos = rdata_end
    return Message(ident, bool(flags & 0x8000), questions, records)


def _encode_record(rec: Record) -> bytes:
    if rec.rtype == TYPE_A:
        rdata = socket.inet_aton(rec.data)  # type: ignore[arg-type]
    elif rec.rtype == TYPE_PTR:
        rdata = encode_name(rec.data)  # type: ignore[arg-type]
    elif rec.rtype == TYPE_SRV:
        priority, weight, port, target = rec.data  # type: ignore[misc]
        rdata = struct.pack(">HHH", priority, weight, port) + encode_name(target)
    elif rec.rtype == TYPE_TXT:
        rdata = rec.data  # type: ignore[assignment]
    else:
        raise ValueError("unsupported record type")
    klass = CLASS_IN if rec.rtype == TYPE_PTR else CLASS_IN | CACHE_FLUSH
    return encode_name(rec.name) + struct.pack(">HHIH", rec.rtype, klass, rec.ttl, len(rdata)) + rdata


def build_response(answers: List[Record], additional: List[Record], ident: int = 0) -> bytes:
    header = struct.pack(">6H", ident, 0x8400, 0, len(answers), 0, len(additional))
    return header + b"".join(_encode_record(r) for r in answers + additional)


def build_query(questions: List[Tuple[str, int]]) -> bytes:
    out = bytearray(struct.pack(">6H", 0, 0, len(questions), 0, 0, 0))
    for name, qtype in questions:
        out += encode_name(name) + struct.pack(">HH", qtype, CLASS_IN)
    return bytes(out)


class Endpoint:
    """A multicast socket on one network interface."""

    def __init__(self, address: str, ifindex: int):
        self.address = address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        try:
            s.setsockopt(socket.IPPROTO_IP, _IP_MULTICAST_ALL, 0)
        except OSError:
            pass
        s.bind(("", MDNS_PORT))
        mreqn = struct.pack("4s4si", socket.inet_aton(MDNS_GROUP), socket.inet_aton(address), ifindex)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreqn)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, mreqn)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setblocking(False)
        self.socket = s

    def send_multicast(self, payload: bytes) -> None:
        try:
            self.socket.sendto(payload, (MDNS_GROUP, MDNS_PORT))
        except OSError:
            pass

    def send_unicast(self, payload: bytes, destination: Tuple[str, int]) -> None:
        try:
            self.socket.sendto(payload, destination)
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


def addresses_by_name(records: List[Record]) -> Dict[str, str]:
    return {r.name.lower(): r.data for r in records if r.rtype == TYPE_A}  # type: ignore[misc]
