import unittest

from handler_protocol import (
    HandlerClient, HandlerConfig, HandlerProtocolError, HandlerStatistics,
)
from onlineUpdate import build_frame


class FakeTransport:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def recv(self, timeout_ms):
        return self.chunks.pop(0) if self.chunks else b""


class HandlerProtocolTests(unittest.TestCase):
    def test_store_exact_wire_bytes(self):
        transport = FakeTransport([bytes.fromhex('1B 01 00 02 0E A1 00 B7')])
        HandlerClient(transport).store_config(HandlerConfig(1, 0, 1, 0, 1, 10, 60000))
        self.assertEqual(transport.sent, [bytes.fromhex(
            '1B 01 00 0A 0E A1 01 00 01 00 01 0A 00 60 EA 3E')])

    def test_read_fragmented_response(self):
        response = bytes.fromhex('1B 01 00 0B 0E A0 00 01 00 01 00 01 0A 00 60 EA 3E')
        transport = FakeTransport([response[:2], response[2:8], response[8:]])
        config = HandlerClient(transport).read_config()
        self.assertEqual(config, HandlerConfig(1, 0, 1, 0, 1, 10, 60000))
        self.assertEqual(transport.sent, [bytes.fromhex('1B 01 00 01 0E A0 B5')])

    def test_read_statistics_and_reset_wire_bytes(self):
        data = bytes.fromhex(
            '01 00 00 00 02 00 00 00 03 00 00 00 '
            '04 00 00 00 05 00 00 00 06 00 00 00')
        read_reply = build_frame(1, 0xA2, b'\0' + data)
        reset_reply = build_frame(2, 0xA3, b'\0')
        client = HandlerClient(FakeTransport([read_reply, reset_reply]))
        self.assertEqual(
            client.read_statistics(), HandlerStatistics(1, 2, 3, 4, 5, 6))
        client.reset_statistics()
        self.assertEqual(client.transport.sent, [
            bytes.fromhex('1B 01 00 01 0E A2 B7'),
            bytes.fromhex('1B 02 00 01 0E A3 B5'),
        ])

    def test_skips_noise_corrupt_stale_and_other_command(self):
        valid = build_frame(1, 0xA0, b'\0' + bytes.fromhex('01 01 01 01 01 0A 00 B8 0B'))
        bad = valid[:-1] + bytes([valid[-1] ^ 1])
        stream = b'noise' + bad + build_frame(9, 0xA0, b'\0') + build_frame(1, 0xA1, b'\0') + valid
        self.assertEqual(HandlerClient(FakeTransport([stream])).read_config().delayMsMinTestTime, 3000)

    def test_rejected_commands(self):
        for command in (0xA0, 0xA1):
            client = HandlerClient(FakeTransport([build_frame(1, command, b'\xC0')]))
            with self.assertRaisesRegex(HandlerProtocolError, '0xC0'):
                if command == 0xA0:
                    client.read_config()
                else:
                    client.store_config(HandlerConfig(1, 1, 1, 1, 1, 10, 3000))

    def test_wrong_read_length(self):
        with self.assertRaises(HandlerProtocolError):
            HandlerClient(FakeTransport([build_frame(1, 0xA0, b'\0\1')])).read_config()

    def test_timeout(self):
        with self.assertRaisesRegex(HandlerProtocolError, '超时'):
            HandlerClient(FakeTransport([]), timeout_ms=10).read_config()

    def test_invalid_values(self):
        for values in ((2, 1, 1, 1, 1, 10, 3000), (1, 1, 1, 1, 1, -1, 0),
                       (1, 1, 1, 1, 1, 0, 60001)):
            with self.assertRaises(ValueError):
                HandlerConfig(*values).encode()
        with self.assertRaises(ValueError):
            HandlerConfig.decode(bytes.fromhex('01 01 01 01 01 00 00 FF FF'))
        self.assertEqual(HandlerConfig(0, 0, 0, 0, 0, 0, 60000).encode()[-2:], b'\x60\xEA')

    def test_partial_send(self):
        transport = FakeTransport([])
        transport.send = lambda data: len(data) - 1
        with self.assertRaisesRegex(HandlerProtocolError, '发送'):
            HandlerClient(transport).read_config()


if __name__ == '__main__':
    unittest.main()
