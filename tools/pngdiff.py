"""Pixel-compare two 8-bit RGB/RGBA PNGs with the standard library only (adopted from architect's round-1 tooling).

    python tools/pngdiff.py a.png b.png [--tol 2]

Prints how many pixels differ by more than `tol` in any channel. decode() is also used by the tests to read
single pixels from screenshots.
"""
import argparse
import struct
import zlib


def decode(path):
    """(width, height, bytes per pixel, rows) for an 8-bit, non-interlaced RGB or RGBA PNG."""
    data = open(path, 'rb').read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{path} is not a PNG')
    pos, idat, w = 8, b'', None
    while pos < len(data):
        n, typ = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b'IHDR':
            w, h, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', body[:13])
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError(f'{path}: unsupported PNG (depth {depth}, colour type {ctype}, interlace {interlace})')
            bpp = 3 if ctype == 2 else 4
        elif typ == b'IDAT':
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, bpp, rows


def pixel(img, x, y):
    """The RGB of pixel (x, y) in an image from decode()."""
    w, h, bpp, rows = img
    return tuple(rows[y][x * bpp:x * bpp + 3])


def diff(a, b, tol=0):
    """(differing pixels, total pixels); raises ValueError if the sizes differ."""
    wa, ha, ba, ra = decode(a)
    wb, hb, bb, rb = decode(b)
    if (wa, ha) != (wb, hb):
        raise ValueError(f'size differs: {(wa, ha)} vs {(wb, hb)}')
    n = 0
    for y in range(ha):
        if ra[y] == rb[y]:
            continue
        for x in range(wa):
            pa, pb = ra[y][x * ba:x * ba + 3], rb[y][x * bb:x * bb + 3]
            if pa != pb and max(abs(p - q) for p, q in zip(pa, pb)) > tol:
                n += 1
    return n, wa * ha


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--tol', type=int, default=0, help='largest channel difference still counted as equal')
    args = ap.parse_args()
    n, total = diff(args.a, args.b, args.tol)
    print(f'{n} of {total} pixels differ ({100 * n / total:.3f}%)')
