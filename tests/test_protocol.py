import io
import os
import shutil
import tempfile
import threading
import unittest

from lightsend import protocol
from lightsend.transfer import ReceiverListener, ReceiverServer, Sender
from lightsend.util import safe_file_name, save_received, short_size


class Duplex:
    """Reads from a fixed buffer and records everything written, like a socket."""

    def __init__(self, data: bytes = b"", ack: int = None):
        self._in = io.BytesIO(data if ack is None else bytes((ack,)))
        self.written = bytearray()

    def recv(self, n):
        return self._in.read(n)

    def sendall(self, b):
        self.written += bytes(b)


def capture(name: str, data: bytes) -> bytes:
    tx = Duplex(ack=protocol.ACK_OK)
    protocol.send(tx, name, data)
    return bytes(tx.written)


def random_bytes(n: int, seed: int) -> bytes:
    import random
    return random.Random(seed).randbytes(n)


class ProtocolTests(unittest.TestCase):
    def test_crc32_matches_java_util_zip(self):
        self.assertEqual(0xCBF43926, protocol.crc32(b"123456789"))
        self.assertEqual(0, protocol.crc32(b""))

    def test_modified_utf8_matches_java_write_utf(self):
        cases = [
            ("photo.jpg", "70686f746f2e6a7067"),
            ("café", "636166c3a9"),
            ("a\0b", "61c08062"),
            ("日", "e697a5"),
            ("😀", "eda0bdedb880"),  # surrogate pair, each half encoded on its own like Java
        ]
        for text, hex_value in cases:
            with self.subTest(text=text):
                encoded = protocol.encode_modified_utf8(text)
                self.assertEqual(hex_value, encoded.hex())
                self.assertEqual(text, protocol.decode_modified_utf8(encoded))

    def test_header_layout_matches_android(self):
        data = bytes([1, 2, 3])
        wire = capture("a.jpg", data)
        self.assertEqual("4c535731", wire[0:4].hex())
        self.assertEqual("0005612e6a7067", wire[4:11].hex())
        self.assertEqual("00000003", wire[11:15].hex())
        self.assertEqual(f"{protocol.crc32(data):08x}", wire[15:19].hex())
        self.assertEqual(data, wire[19:])

    def test_round_trip_acknowledges_intact_file(self):
        data = random_bytes(300_000, 1)
        rx = Duplex(capture("IMG_0001.jpg", data))
        started = []
        last = []
        r = protocol.receive(rx, lambda n, s: started.append(n), lambda got, total: last.append(got))
        self.assertEqual("IMG_0001.jpg", r.name)
        self.assertEqual(data, r.data)
        self.assertEqual(["IMG_0001.jpg"], started)
        self.assertEqual(len(data), last[-1])
        self.assertEqual(bytes([protocol.ACK_OK]), bytes(rx.written))

    def test_corrupted_data_is_rejected(self):
        wire = bytearray(capture("x.jpg", random_bytes(1000, 2)))
        wire[-10] ^= 0xFF
        rx = Duplex(bytes(wire))
        with self.assertRaises(IOError):
            protocol.receive(rx)
        self.assertEqual(bytes([protocol.ACK_BAD]), bytes(rx.written))

    def test_rejects_other_protocols_and_truncated_streams(self):
        with self.assertRaises(IOError):
            protocol.receive(Duplex(b"GET / HTTP/1.1\r\n\r\n"))
        wire = capture("x.jpg", random_bytes(5000, 3))
        with self.assertRaises(IOError):
            protocol.receive(Duplex(wire[:2000]))

    def test_long_names_are_trimmed_like_android(self):
        rx = Duplex(capture("n" * 500 + ".jpg", bytes([9])))
        self.assertEqual(protocol.MAX_NAME_CHARS, len(protocol.receive(rx).name))


class Recording(ReceiverListener):
    def __init__(self):
        self.received = []
        self.failures = []
        self.senders = []
        self.cond = threading.Condition()

    def on_incoming(self, name, size, sender):
        self.senders.append(sender)

    def on_progress(self, received, total):
        pass

    def on_received(self, name, data):
        with self.cond:
            self.received.append((name, data))
            self.cond.notify_all()

    def on_failed(self, message):
        self.failures.append(message)

    def wait_for(self, count, timeout=10):
        with self.cond:
            return self.cond.wait_for(lambda: len(self.received) >= count, timeout)


class TcpTests(unittest.TestCase):
    def test_sends_several_photos_over_tcp(self):
        listener = Recording()
        server = ReceiverServer(listener)
        port = server.start("127.0.0.1")
        self.addCleanup(server.stop)
        photos = [(f"photo_{i}.jpg", random_bytes(200_000 + i, 10 + i)) for i in range(3)]
        sender = Sender()
        for name, data in photos:
            self.assertTrue(sender.send(("127.0.0.1", port), name, data))
        self.assertTrue(listener.wait_for(3))
        self.assertEqual(photos, listener.received)
        self.assertEqual([], listener.failures)
        self.assertEqual({"127.0.0.1"}, set(listener.senders))

    def test_connecting_to_nothing_fails_quickly(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(IOError):
            Sender().send(("127.0.0.1", port), "a.jpg", b"\x01")


class SaverTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="LightSendTests_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_makes_names_safe(self):
        cases = [
            ("IMG_1.jpg", "IMG_1.jpg"),
            ("../../Windows/evil.jpg", "evil.jpg"),
            ("C:\\temp\\x.png", "x.png"),
            ("a:b*c?.jpg", "a_b_c_.jpg"),
            ("CON.jpg", "_CON.jpg"),
            ("noext", "noext.jpg"),
        ]
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(expected, safe_file_name(given))

    def test_empty_names_get_a_generated_one(self):
        self.assertTrue(safe_file_name("..").startswith("LightSend_"))

    def test_never_overwrites_existing_files(self):
        first = save_received(self.dir, "photo.jpg", b"\x01")
        second = save_received(self.dir, "photo.jpg", b"\x02")
        third = save_received(self.dir, "photo.jpg", b"\x03")
        self.assertEqual(os.path.join(self.dir, "photo.jpg"), first)
        self.assertEqual(os.path.join(self.dir, "photo (1).jpg"), second)
        self.assertEqual(os.path.join(self.dir, "photo (2).jpg"), third)
        with open(first, "rb") as f:
            self.assertEqual(b"\x01", f.read())
        with open(third, "rb") as f:
            self.assertEqual(b"\x03", f.read())

    def test_formats_sizes_like_android(self):
        for size, expected in [(0, "0 B"), (512, "512 B"), (1500, "1.5 kB"),
                               (2_400_000, "2.4 MB"), (45_000_000, "45 MB")]:
            with self.subTest(size=size):
                self.assertEqual(expected, short_size(size))


if __name__ == "__main__":
    unittest.main()
