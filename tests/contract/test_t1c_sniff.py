"""T1c contract: app/intake/sniff.py -> sniff_image(data: bytes) -> dict | None"""
import struct
import unittest
import zlib

from app.intake import sniff as m


def png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk + b"\x00" * 20


def jpeg(w, h, with_app0=True):
    out = b"\xff\xd8"
    if with_app0:
        app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        out += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    sof = struct.pack(">BHHB", 8, h, w, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return out + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9"


def webp_vp8x(w, h):
    payload = b"\x00" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


@unittest.skipUnless(getattr(m, "IMPLEMENTED", False), "T1c not implemented yet")
class Sniff(unittest.TestCase):
    def test_png(self):
        self.assertEqual(m.sniff_image(png(1280, 960)), {"format": "png", "width": 1280, "height": 960})

    def test_jpeg_with_app0(self):
        self.assertEqual(m.sniff_image(jpeg(800, 1200)), {"format": "jpeg", "width": 800, "height": 1200})

    def test_jpeg_without_app0(self):
        self.assertEqual(m.sniff_image(jpeg(640, 480, with_app0=False)), {"format": "jpeg", "width": 640, "height": 480})

    def test_webp_vp8x(self):
        self.assertEqual(m.sniff_image(webp_vp8x(2000, 3000)), {"format": "webp", "width": 2000, "height": 3000})

    def test_unknown_and_garbage(self):
        for data in (b"", b"GIF89a" + b"\x00" * 20, b"hello world", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"):
            self.assertIsNone(m.sniff_image(data), data[:10])

    def test_truncated_headers_do_not_raise(self):
        for data in (png(10, 10)[:20], jpeg(10, 10)[:12], webp_vp8x(10, 10)[:18]):
            self.assertIsNone(m.sniff_image(data))

    def test_zero_dimensions_rejected(self):
        self.assertIsNone(m.sniff_image(png(0, 10)))


if __name__ == "__main__":
    unittest.main()
