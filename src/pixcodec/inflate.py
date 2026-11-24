"""DEFLATE decoder (RFC 1951) and zlib wrapper (RFC 1950), written from the spec.

Huffman decoding uses the canonical-code counting method (as in zlib's
puff.c): no tables of explicit codes are needed, only how many codes there
are of each length and the symbols in code order. It's simple and correct;
a table-driven fast path is a later optimisation milestone.
"""

from __future__ import annotations

LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
            35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
LEN_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
             3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385,
             513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577]
DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7,
              8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]
CODE_LENGTH_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
MAX_BITS = 15


class InflateError(ValueError):
    pass


class BitReader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos      # byte position
        self.bitbuf = 0
        self.bitcnt = 0

    def bits(self, n: int) -> int:
        while self.bitcnt < n:
            if self.pos >= len(self.data):
                raise InflateError("unexpected end of compressed data")
            self.bitbuf |= self.data[self.pos] << self.bitcnt
            self.pos += 1
            self.bitcnt += 8
        val = self.bitbuf & ((1 << n) - 1)
        self.bitbuf >>= n
        self.bitcnt -= n
        return val

    def align(self) -> None:
        self.bitbuf = 0
        self.bitcnt = 0


class Huffman:
    def __init__(self, lengths: list[int]):
        self.count = [0] * (MAX_BITS + 1)
        for n in lengths:
            self.count[n] += 1
        self.count[0] = 0
        left = 1
        for n in range(1, MAX_BITS + 1):
            left = (left << 1) - self.count[n]
            if left < 0:
                raise InflateError("over-subscribed Huffman code")
        offs = [0] * (MAX_BITS + 2)
        for n in range(1, MAX_BITS + 1):
            offs[n + 1] = offs[n] + self.count[n]
        self.symbol = [0] * len(lengths)
        for sym, n in enumerate(lengths):
            if n:
                self.symbol[offs[n]] = sym
                offs[n] += 1

    def decode(self, br: BitReader) -> int:
        code = first = index = 0
        for n in range(1, MAX_BITS + 1):
            code |= br.bits(1)
            count = self.count[n]
            if code - count < first:
                return self.symbol[index + (code - first)]
            index += count
            first = (first + count) << 1
            code <<= 1
        raise InflateError("invalid Huffman code")


_FIXED_LIT = Huffman([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8)
_FIXED_DIST = Huffman([5] * 30)


def _codes(br: BitReader, out: bytearray, lit: Huffman, dist: Huffman) -> None:
    while True:
        sym = lit.decode(br)
        if sym < 256:
            out.append(sym)
        elif sym == 256:
            return
        else:
            sym -= 257
            if sym >= 29:
                raise InflateError("invalid length symbol")
            length = LEN_BASE[sym] + br.bits(LEN_EXTRA[sym])
            dsym = dist.decode(br)
            if dsym >= 30:
                raise InflateError("invalid distance symbol")
            d = DIST_BASE[dsym] + br.bits(DIST_EXTRA[dsym])
            if d > len(out):
                raise InflateError("distance too far back")
            start = len(out) - d
            if d >= length:
                out += out[start : start + length]
            else:  # overlapping copy (e.g. run-length): byte by byte
                for i in range(length):
                    out.append(out[start + i])


def _dynamic_tables(br: BitReader) -> tuple[Huffman, Huffman]:
    nlen = br.bits(5) + 257
    ndist = br.bits(5) + 1
    ncode = br.bits(4) + 4
    if nlen > 286 or ndist > 30:
        raise InflateError("bad dynamic block counts")
    cl = [0] * 19
    for i in range(ncode):
        cl[CODE_LENGTH_ORDER[i]] = br.bits(3)
    clh = Huffman(cl)
    lengths: list[int] = []
    while len(lengths) < nlen + ndist:
        sym = clh.decode(br)
        if sym < 16:
            lengths.append(sym)
        elif sym == 16:
            if not lengths:
                raise InflateError("repeat with no previous length")
            lengths += [lengths[-1]] * (3 + br.bits(2))
        elif sym == 17:
            lengths += [0] * (3 + br.bits(3))
        else:
            lengths += [0] * (11 + br.bits(7))
    if len(lengths) > nlen + ndist:
        raise InflateError("code lengths overflow")
    if lengths[256] == 0:
        raise InflateError("no end-of-block code")
    return Huffman(lengths[:nlen]), Huffman(lengths[nlen:])


def inflate(data: bytes, pos: int = 0) -> tuple[bytes, int]:
    """Decompress raw DEFLATE data. Returns (output, byte position after the last block)."""
    br = BitReader(data, pos)
    out = bytearray()
    while True:
        final = br.bits(1)
        btype = br.bits(2)
        if btype == 0:
            br.align()
            p = br.pos
            if p + 4 > len(data):
                raise InflateError("truncated stored block")
            n = data[p] | data[p + 1] << 8
            nn = data[p + 2] | data[p + 3] << 8
            if n != (~nn & 0xFFFF):
                raise InflateError("stored block length check failed")
            if p + 4 + n > len(data):
                raise InflateError("truncated stored block")
            out += data[p + 4 : p + 4 + n]
            br.pos = p + 4 + n
        elif btype == 1:
            _codes(br, out, _FIXED_LIT, _FIXED_DIST)
        elif btype == 2:
            _codes(br, out, *_dynamic_tables(br))
        else:
            raise InflateError("invalid block type 3")
        if final:
            return bytes(out), br.pos


def adler32(data: bytes) -> int:
    a, b = 1, 0
    for i in range(0, len(data), 5552):  # largest n with no overflow before mod, per zlib
        for byte in data[i : i + 5552]:
            a += byte
            b += a
        a %= 65521
        b %= 65521
    return (b << 16) | a


def zlib_decompress(data: bytes) -> bytes:
    if len(data) < 6:
        raise InflateError("zlib stream too short")
    cmf, flg = data[0], data[1]
    if cmf & 0x0F != 8:
        raise InflateError("not deflate")
    if (cmf << 8 | flg) % 31:
        raise InflateError("zlib header check failed")
    if flg & 0x20:
        raise InflateError("preset dictionaries are not supported")
    out, end = inflate(data, 2)
    if int.from_bytes(data[end : end + 4], "big") != adler32(out):
        raise InflateError("adler32 mismatch")
    return out
