"""Format and size of an image from its header alone. T1c.

    sniff_image(b"...") -> {"format": "png"|"jpeg"|"webp", "width": int, "height": int} | None

Nothing here decodes a picture and nothing reads past the header: the caller
pays for the first few dozen bytes of a file, not for the file. Every branch is
length-guarded before it reads, so truncated data returns None rather than
raising, and a size that parses to zero or less is refused as a format we cannot
believe.

Two things this deliberately does NOT claim:
  * a header is not the picture. A PNG whose IHDR says 1280x960 and whose IDAT is
    garbage sniffs as 1280x960; "sniffed" is not "valid".
  * JPEG is walked to the first SOFn, so an EXIF thumbnail before it cannot be
    mistaken for the frame -- but a file with two frames (progressive rewrites,
    MPO) reports the first one, which is not necessarily the one a viewer shows.
"""
import struct

IMPLEMENTED = True

_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_STANDALONE = {0x01, 0xD8} | set(range(0xD0, 0xD8))


def _png(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return {"format": "png", "width": width, "height": height}


def _jpeg(data):
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 1 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in _STANDALONE:
            i += 2
            continue
        if marker in (0xD9, 0xDA) or i + 4 > len(data):
            return None
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if seg_len < 2:
            return None
        if marker in _SOF:
            if i + 9 > len(data):
                return None
            return {"format": "jpeg",
                    "height": int.from_bytes(data[i + 5:i + 7], "big"),
                    "width": int.from_bytes(data[i + 7:i + 9], "big")}
        i += 2 + seg_len
    return None


def _webp(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP" or len(data) < 20:
        return None
    fourcc = data[12:16]
    if fourcc == b"VP8X":
        if len(data) < 30:
            return None
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
    elif fourcc == b"VP8 ":
        if len(data) < 30 or data[23:26] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
    elif fourcc == b"VP8L":
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    else:
        return None
    return {"format": "webp", "width": width, "height": height}


def sniff_image(data):
    """Return {"format", "width", "height"} or None. Never raises."""
    if isinstance(data, memoryview):
        data = data.tobytes()
    if not isinstance(data, (bytes, bytearray)):
        return None
    for reader in (_png, _jpeg, _webp):
        try:
            head = reader(bytes(data))
        except (IndexError, ValueError, TypeError, struct.error):
            head = None
        if head and head["width"] > 0 and head["height"] > 0:
            return head
    return None
