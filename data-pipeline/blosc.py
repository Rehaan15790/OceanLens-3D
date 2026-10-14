#!/usr/bin/env python3
"""Blosc (version 1 frames) with the LZ4 codec and byte shuffle, in pure Python: enough to read the Copernicus Marine
ARCO Zarr stores, which tests use as an independent check of the NetCDF-4 reader (nc4.py) on the same GLORYS values.

    raw = decompress(frame, max_bytes=1 << 28)

Frames with another codec (blosclz, zlib, zstd, snappy) or with bit shuffle raise BloscError. Every size in the frame is
checked against the frame and against max_bytes, so damaged or hostile input raises BloscError and nothing else.
"""
import numpy as np


class BloscError(ValueError):
    """The bytes are not a Blosc frame this module can decode."""


def lz4_block(src, size):
    """One LZ4 block (no frame header) -> exactly size bytes."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if i >= n:
                    raise BloscError('LZ4 literal length runs past the block')
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n or len(out) + lit > size:
            raise BloscError('LZ4 literals run past the block')
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break                                   # the last sequence has literals only
        if i + 2 > n:
            raise BloscError('LZ4 match offset cut short')
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = token & 15
        if ml == 15:
            while True:
                if i >= n:
                    raise BloscError('LZ4 match length runs past the block')
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        if off == 0 or off > len(out):
            raise BloscError('LZ4 match points before the start of the output')
        if len(out) + ml > size:
            raise BloscError('LZ4 output is larger than declared')
        start = len(out) - off
        if off >= ml:
            out += out[start:start + ml]
        else:                                       # an overlapping match repeats the last off bytes
            while ml > 0:
                take = min(ml, off)
                out += out[start:start + take]
                ml -= take
                start += take
    if len(out) != size:
        raise BloscError(f'LZ4 block gave {len(out)} bytes, {size} expected')
    return bytes(out)


def _unshuffle(b, typesize):
    """Undo Blosc's byte shuffle over one block; the bytes past the last whole element are not shuffled."""
    n = len(b) // typesize * typesize
    if typesize <= 1 or n == 0:
        return b
    a = np.frombuffer(b[:n], np.uint8).reshape(typesize, -1).T.tobytes()
    return a + b[n:]


def decompress(frame, max_bytes=1 << 28):
    """A Blosc 1 frame -> its bytes."""
    frame = bytes(frame)
    if len(frame) < 16:
        raise BloscError('Blosc frame shorter than its header')
    ver, flags, typesize = frame[0], frame[2], frame[3]
    nbytes = int.from_bytes(frame[4:8], 'little')
    blocksize = int.from_bytes(frame[8:12], 'little')
    cbytes = int.from_bytes(frame[12:16], 'little')
    if ver not in (1, 2) or cbytes > len(frame) or nbytes > max_bytes:
        raise BloscError(f'Blosc header is not plausible (version {ver}, {nbytes} bytes in {cbytes})')
    if flags & 0x02:                                # stored uncompressed
        if 16 + nbytes > len(frame):
            raise BloscError('Blosc frame is cut short')
        return frame[16:16 + nbytes]
    if flags & 0x04:
        raise BloscError('bit-shuffled Blosc frames are not supported')
    codec = flags >> 5
    if codec != 1:
        raise BloscError(f'Blosc codec {codec} is not supported (only LZ4)')
    if nbytes == 0:
        return b''
    if blocksize == 0 or typesize == 0:
        raise BloscError('Blosc block size or type size is zero')
    nblocks = -(-nbytes // blocksize)
    if 16 + 4 * nblocks > len(frame):
        raise BloscError('Blosc block table is cut short')
    starts = [int.from_bytes(frame[16 + 4 * k:20 + 4 * k], 'little') for k in range(nblocks)]
    ends = starts[1:] + [cbytes]
    out = []
    for k, (s, e) in enumerate(zip(starts, ends)):
        bsize = min(blocksize, nbytes - k * blocksize)
        if not 16 + 4 * nblocks <= s <= e <= len(frame):
            raise BloscError('Blosc block outside the frame')
        # a block is split into typesize streams, or not; the stream sizes say which (each split carries its size)
        block = None
        for nsplit in ([typesize, 1] if typesize > 1 and bsize % typesize == 0 else [1]):
            parts, p, ok = [], s, True
            for _ in range(nsplit):
                if p + 4 > e:
                    ok = False
                    break
                cs = int.from_bytes(frame[p:p + 4], 'little', signed=True)
                p += 4
                if cs < 0 or p + cs > e:
                    ok = False
                    break
                parts.append((frame[p:p + cs], bsize // nsplit))
                p += cs
            if ok and p == e:
                block = parts
                break
        if block is None:
            raise BloscError('Blosc block streams do not add up')
        data = b''.join(c if len(c) == usize else lz4_block(c, usize) for c, usize in block)
        out.append(_unshuffle(data, typesize) if flags & 0x01 else data)
    return b''.join(out)
