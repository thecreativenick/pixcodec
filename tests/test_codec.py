import io
import random
import struct
import zlib

import pytest

from pixcodec import Image, InflateError, crc32, decode, encode, zlib_decompress


def sample_payloads():
    rng = random.Random(1)
    text = b"the quick brown fox jumps over the lazy dog " * 200
    noise = bytes(rng.randrange(256) for _ in range(5000))
    runs = b"a" * 3000 + b"ab" * 500
    return [b"", b"x", text, noise, runs, text + noise + runs]


@pytest.mark.parametrize("level", [0, 1, 6, 9])  # 0 = stored, low = fixed/dynamic mix, 9 = dynamic
def test_inflate_matches_stdlib(level):
    for payload in sample_payloads():
        assert zlib_decompress(zlib.compress(payload, level)) == payload


def test_inflate_detects_corruption():
    good = bytearray(zlib.compress(b"hello hello hello hello", 9))
    bad_checksum = bytes(good[:-1] + bytes([good[-1] ^ 1]))
    with pytest.raises(InflateError):
        zlib_decompress(bad_checksum)
    with pytest.raises(InflateError):
        zlib_decompress(bytes(good[:5]))


def test_crc32_matches_stdlib():
    for p in sample_payloads():
        assert crc32(p) == zlib.crc32(p)


def gradient(w, h, channels, depth=8):
    rng = random.Random(w * h * channels)
    px = bytearray()
    for y in range(h):
        for x in range(w):
            for c in range(channels):
                v = (x * 7 + y * 13 + c * 50 + rng.randrange(4)) % (256 if depth == 8 else 65536)
                px += v.to_bytes(depth // 8, "big")
    return Image(w, h, channels, depth, bytes(px))


@pytest.mark.parametrize("filter_type", range(5))
@pytest.mark.parametrize("channels", [1, 2, 3, 4])
def test_png_roundtrip_all_filters(filter_type, channels):
    img = gradient(17, 9, channels)
    out = decode(encode(img, filter_type))
    assert (out.width, out.height, out.channels, out.pixels) == (17, 9, channels, img.pixels)


def test_png_16_bit():
    img = gradient(5, 4, 3, depth=16)
    out = decode(encode(img, 4))
    assert out.pixels == img.pixels and out.pixel(1, 1) == img.pixel(1, 1)


def test_palette_png_with_transparency():
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"\x00\x00\x01" + b"\x00\x02\x00"  # 2x2, filter 0, palette indices
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 3, 0, 0, 0))
           + chunk(b"PLTE", bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
           + chunk(b"tRNS", bytes([255, 128]))
           + chunk(b"IDAT", zlib.compress(rows))
           + chunk(b"IEND", b""))
    img = decode(png)
    assert img.channels == 4
    assert [img.pixel(x, y) for y in range(2) for x in range(2)] == [
        (255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 255), (255, 0, 0, 255)]


def test_matches_pillow_if_installed():
    PIL = pytest.importorskip("PIL.Image")
    src = PIL.new("RGBA", (31, 23))
    src.putdata([(x * 8 % 256, y * 11 % 256, (x ^ y) % 256, 200) for y in range(23) for x in range(31)])
    buf = io.BytesIO()
    src.save(buf, "PNG", optimize=True)
    img = decode(buf.getvalue())
    assert img.pixels == src.tobytes()
