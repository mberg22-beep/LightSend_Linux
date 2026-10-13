import threading
import time
import unittest
import uuid

from lightsend import mdns, network
from lightsend.discovery import Advertiser, Discovery


class AddressParsingTests(unittest.TestCase):
    def test_accepts_address_with_port(self):
        cases = [
            ("192.168.1.20:41483", ("192.168.1.20", 41483)),
            ("  10.0.0.5:80 ", ("10.0.0.5", 80)),
            ("127.0.0.1:65535", ("127.0.0.1", 65535)),
            ("[fe80::1]:1234", ("fe80::1", 1234)),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, network.try_parse_address(text))

    def test_rejects_incomplete_addresses(self):
        for text in [None, "", "192.168.1.20", "192.168.1.20:0", "192.168.1.20:70000", "1:80",
                     "192.168:80", "phone:80", "fe80::1", "192.168.1.20:80x"]:
            with self.subTest(text=text):
                self.assertIsNone(network.try_parse_address(text))


class DnsTests(unittest.TestCase):
    def test_keeps_only_lightsend_instances(self):
        cases = [
            ("Michael's S25 Ultra._lightsend._tcp.local", True),
            ("ORTHO._LightSend._TCP.local", True),
            ("Chromecast-5f4c70d2._googlecast._tcp.local", False),
            ("5f4c70d2-d90c._googlezone._tcp.local", False),
            ("_lightsend._tcp.local", False),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(expected, Discovery.is_lightsend(name))

    def test_records_survive_a_round_trip_with_dotted_names(self):
        instance = mdns.instance_name("My.Phone", "_lightsend._tcp")
        records = [
            mdns.Record("_lightsend._tcp.local", mdns.TYPE_PTR, 120, instance),
            mdns.Record(instance, mdns.TYPE_SRV, 120, (0, 0, 4242, "host.local")),
            mdns.Record("host.local", mdns.TYPE_A, 120, "192.168.1.9"),
        ]
        message = mdns.parse_message(mdns.build_response(records[:1], records[1:]))
        self.assertTrue(message.is_response)
        self.assertEqual(records, message.records)
        self.assertEqual("My.Phone", mdns.split_instance(message.records[0].data, "_lightsend._tcp"))


@unittest.skipUnless(network.is_connected(), "needs a LAN adapter with multicast")
class DiscoveryTests(unittest.TestCase):
    def test_finds_a_receiver_advertised_on_this_pc(self):
        name = "LightSendTest-" + uuid.uuid4().hex[:6]
        advertiser = Advertiser()
        advertiser.start(name, 45678)
        self.addCleanup(advertiser.stop)
        discovery = Discovery()
        discovery.start()
        self.addCleanup(discovery.stop)

        deadline = time.monotonic() + 10
        while name not in discovery.names() and time.monotonic() < deadline:
            time.sleep(0.2)

        self.assertIn(name, discovery.names())
        host, port = discovery.resolve(name)
        self.assertEqual(45678, port)
        self.assertIn(host, network.lan_ipv4_addresses())


if __name__ == "__main__":
    unittest.main()
