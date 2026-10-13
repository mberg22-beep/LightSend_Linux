"""Wire format shared with the LightSend Android app (com.lightsend.wifi.WifiProtocol).

The receiver advertises SERVICE_TYPE with DNS-SD/mDNS and listens on a TCP port; the sender
connects and streams:

    magic u32 "LSW1" | name (Java modified UTF-8, u16 length) | size u32 | CRC-32 u32 | data

All integers are big-endian. The receiver answers with one byte: ACK_OK if the CRC-32 matched,
otherwise ACK_BAD.
"""

import struct
import zlib
from typing import Callable, NamedTuple, Optional

SERVICE_TYPE = "_lightsend._tcp"
MAGIC = 0x4C535731  # "LSW1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_NAME_CHARS = 200
ACK_OK = 1
ACK_BAD = 2
CHUNK = 64 * 1024

Progress = Callable[[int, int], None]


class Received(NamedTuple):
    name: str
    data: bytes


def crc32(data: bytes, value: int = 0) -> int:
    return zlib.crc32(data, value) & 0xFFFFFFFF


def encode_modified_utf8(text: str) -> bytes:
    """Java's DataOutputStream.writeUTF: NUL and non-BMP characters take the 2/3-byte forms."""
    out = bytearray()
    raw = text.encode("utf-16-le", "surrogatepass")
    for (c,) in struct.iter_unpack("<H", raw):
        if 0x0001 <= c <= 0x007F:
            out.append(c)
        elif c <= 0x07FF:
            out += bytes((0xC0 | (c >> 6), 0x80 | (c & 0x3F)))
        else:
            out += bytes((0xE0 | (c >> 12), 0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F)))
    if len(out) > 0xFFFF:
        raise ValueError("Name is too long")
    return bytes(out)


def decode_modified_utf8(data: bytes) -> str:
    units = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            units.append(b)
            i += 1
        elif (b & 0xE0) == 0xC0 and i + 1 < n and (data[i + 1] & 0xC0) == 0x80:
            units.append(((b & 0x1F) << 6) | (data[i + 1] & 0x3F))
            i += 2
        elif (b & 0xF0) == 0xE0 and i + 2 < n and (data[i + 1] & 0xC0) == 0x80 and (data[i + 2] & 0xC0) == 0x80:
            units.append(((b & 0x0F) << 12) | ((data[i + 1] & 0x3F) << 6) | (data[i + 2] & 0x3F))
            i += 3
        else:
            raise IOError("Malformed file name")
    raw = struct.pack(f"<{len(units)}H", *units)
    return raw.decode("utf-16-le", "replace")


def _read_exactly(sock, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise IOError("Connection closed by sender")
        buf += chunk
    return bytes(buf)


def send(sock, name: str, data: bytes, on_progress: Optional[Progress] = None) -> bool:
    """Sends one file and returns True if the receiver confirmed it arrived intact."""
    if len(data) < 1 or len(data) > MAX_FILE_BYTES:
        raise ValueError("File size not supported")
    if len(name) > MAX_NAME_CHARS:
        name = name[:MAX_NAME_CHARS]

    name_bytes = encode_modified_utf8(name)
    sock.sendall(struct.pack(">IH", MAGIC, len(name_bytes)) + name_bytes
                 + struct.pack(">iI", len(data), crc32(data)))

    view = memoryview(data)
    off = 0
    while off < len(data):
        end = min(off + CHUNK, len(data))
        sock.sendall(view[off:end])
        off = end
        if on_progress:
            on_progress(off, len(data))

    ack = sock.recv(1)
    if not ack:
        raise IOError("Receiver closed the connection")
    return ack[0] == ACK_OK


def receive(sock,
            on_start: Optional[Callable[[str, int], None]] = None,
            on_progress: Optional[Progress] = None) -> Received:
    """Reads one file. Raises IOError if the stream is invalid or the checksum fails."""
    if struct.unpack(">I", _read_exactly(sock, 4))[0] != MAGIC:
        raise IOError("Not a LightSend sender")

    name_len = struct.unpack(">H", _read_exactly(sock, 2))[0]
    name = decode_modified_utf8(_read_exactly(sock, name_len))

    size = struct.unpack(">i", _read_exactly(sock, 4))[0]
    if size < 1 or size > MAX_FILE_BYTES:
        raise IOError(f"Unsupported file size {size}")
    expected_crc = struct.unpack(">I", _read_exactly(sock, 4))[0]
    if on_start:
        on_start(name, size)

    data = bytearray()
    crc = 0
    while len(data) < size:
        chunk = sock.recv(min(CHUNK, size - len(data)))
        if not chunk:
            raise IOError(f"Connection closed after {len(data)} of {size} bytes")
        data += chunk
        crc = crc32(chunk, crc)
        if on_progress:
            on_progress(len(data), size)

    ok = crc == expected_crc
    sock.sendall(bytes((ACK_OK if ok else ACK_BAD,)))
    if not ok:
        raise IOError("Checksum mismatch")
    return Received(name, bytes(data))
