"""PNG decoder (non-interlaced, bit depth 8 and 16, all five colour types) and a simple encoder.

The decoder uses this package's own inflate and CRC. The encoder still uses
the standard library's zlib for compression; replacing that with an own
DEFLATE encoder is a later milestone.
"""

from __future__ import annotations

import struct
import zlib as _stdlib_zlib
from dataclasses import dataclass

from .inflate import zlib_decompress

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PNGError(ValueError):
    pass


def _crc_table() -> list[int]:
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = 0xEDB88320 ^ (c >> 1) if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc_table()


def crc32(data: bytes, crc: int = 0) -> int:
    c = crc ^ 0xFFFFFFFF
    for b in data:
        c = _CRC[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


@dataclass
class Image:
    width: int
    height: int
    channels: int          # 1 gray, 2 gray+alpha, 3 RGB, 4 RGBA (palettes are expanded)
    bit_depth: int         # 8 or 16
    pixels: bytes          # row-major samples, big-endian for 16-bit

    def pixel(self, x: int, y: int) -> tuple[int, ...]:
        bps = self.bit_depth // 8
        i = (y * self.width + x) * self.channels * bps
        raw = self.pixels[i : i + self.channels * bps]
        if bps == 1:
            return tuple(raw)
        return tuple(int.from_bytes(raw[j : j + 2], "big") for j in range(0, len(raw), 2))


def read_chunks(data: bytes):
    if not data.startswith(SIGNATURE):
        raise PNGError("not a PNG file")
    pos = 8
    while pos < len(data):
        if pos + 8 > len(data):
            raise PNGError("truncated chunk header")
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise PNGError("truncated chunk")
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        if crc32(kind + body) != crc:
            raise PNGError(f"CRC mismatch in {kind!r} chunk")
        yield kind, body
        pos += 12 + length
        if kind == b"IEND":
            return
    raise PNGError("missing IEND")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if len(line) != stride:
            raise PNGError("image data too short")
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        elif ftype != 0:
            raise PNGError(f"bad filter type {ftype}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return out


def decode(data: bytes) -> Image:
    ihdr = None
    palette = None
    trns = None
    idat = bytearray()
    for kind, body in read_chunks(data):
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i : i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
    if ihdr is None:
        raise PNGError("missing IHDR")
    width, height, depth, ctype, comp, filt, interlace = ihdr
    if ctype not in CHANNELS or comp or filt:
        raise PNGError("unsupported IHDR values")
    if interlace:
        raise NotImplementedError("Adam7 interlacing is a later milestone")
    if depth not in (8, 16):
        raise NotImplementedError(f"bit depth {depth} is a later milestone")
    if ctype == 3 and (palette is None or depth != 8):
        raise PNGError("palette image without PLTE")

    ch = CHANNELS[ctype]
    bpp = ch * depth // 8
    pixels = unfilter(zlib_decompress(bytes(idat)), height, width * bpp, bpp)

    if ctype == 3:
        alpha = list(trns or b"")
        has_alpha = bool(alpha)
        out = bytearray()
        for idx in pixels:
            if idx >= len(palette):
                raise PNGError("palette index out of range")
            out += bytes(palette[idx])
            if has_alpha:
                out.append(alpha[idx] if idx < len(alpha) else 255)
        return Image(width, height, 4 if has_alpha else 3, 8, bytes(out))
    return Image(width, height, ch, depth, bytes(pixels))


def encode(img: Image, filter_type: int = 0) -> bytes:
    """Encode an Image as PNG using a single filter type for every row."""
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[img.channels]
    bpp = img.channels * img.bit_depth // 8
    stride = img.width * bpp
    raw = bytearray()
    prev = bytes(stride)
    for y in range(img.height):
        line = img.pixels[y * stride : (y + 1) * stride]
        raw.append(filter_type)
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up = prev[i]
            upleft = prev[i - bpp] if i >= bpp else 0
            pred = (0, left, up, (left + up) >> 1, _paeth(left, up, upleft))[filter_type]
            raw.append((line[i] - pred) & 0xFF)
        prev = line

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", img.width, img.height, img.bit_depth, ctype, 0, 0, 0)
    return (SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", _stdlib_zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
