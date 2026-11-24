# pixcodec

PNG and baseline/progressive JPEG encoders and decoders written from the specs, plus a
browser viewer that shows images decoded by these codecs.

## Status

Early scaffold. Done so far:

- A DEFLATE decoder (stored, fixed and dynamic Huffman blocks) and the zlib wrapper with
  Adler-32, written from RFC 1950/1951 and checked against the standard library's `zlib` at
  every compression level.
- CRC-32 from scratch.
- PNG decoder: chunk parsing with CRC checks, all five filter types, grey/RGB/RGBA/grey+alpha
  at 8 and 16 bits, and palette images with tRNS transparency. A test matches Pillow's pixels
  exactly when Pillow is installed.
- A simple PNG encoder (one filter type per image, still using stdlib zlib).

## Planned milestones

1. The rest of PNG decoding: bit depths 1/2/4, Adam7 interlacing, gamma/sRGB chunks. Pass PngSuite.
2. DEFLATE encoder (LZ77 with hash chains, dynamic Huffman) and adaptive per-row filter choice.
3. Baseline JPEG decoder: markers, Huffman tables, IDCT, chroma upsampling, restart markers.
4. Progressive JPEG and a JPEG encoder with quality settings.
5. A self-written fuzzer and a localhost viewer page.

## Success bar

Every PngSuite image matches Pillow pixel for pixel, test JPEGs match within one level, Pillow
reads every file written, and an hour of fuzzing finds no crashes.

## Development

```bash
pip install -e .[dev]
pytest
```
