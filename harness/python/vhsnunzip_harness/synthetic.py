"""Synthetic raw Snappy chunks with a controlled element mix.

Each generator returns (compressed, decompressed) for one chunk. They target
the corners of the vhsnunzip datapath: the element rate (short elements), the
literal path (long literals), overlapping copies (period < line width), long-
term history reads (far offsets) and stream boundaries (edge sizes).
"""

import random

from .snappy import emit_copy, emit_literal, write_varint


class ChunkBuilder:
    def __init__(self):
        self.elements = bytearray()
        self.raw = bytearray()

    def __len__(self):
        return len(self.raw)

    def literal(self, data: bytes) -> None:
        if data:
            emit_literal(self.elements, data)
            self.raw += data

    def copy(self, offset: int, length: int) -> None:
        """Copy of any length >= 4; split into <= 64-byte elements like snappy."""
        assert 4 <= length and 0 < offset <= len(self.raw)
        emit_copy(self.elements, offset, length)
        start = len(self.raw) - offset
        for i in range(length):
            self.raw.append(self.raw[start + i])

    def finish(self):
        return write_varint(len(self.raw)) + bytes(self.elements), bytes(self.raw)


def mix(size: int, copy_fraction: float = 0.5, literal_len: int = 32, copy_len: int = 32,
        seed: int = 0):
    """Random-byte literals of literal_len and non-overlapping copies of copy_len
    (random offsets, at most 64 KiB back) such that about copy_fraction of the
    output comes from copies. Short lengths stress the element rate."""
    assert 0.0 <= copy_fraction < 1.0 and literal_len >= 1 and copy_len >= 4
    rng = random.Random(seed)
    b = ChunkBuilder()
    copied = 0
    while len(b) < size:
        remaining = size - len(b)
        if len(b) >= copy_len and remaining >= copy_len and copied < copy_fraction * len(b):
            b.copy(rng.randint(copy_len, min(len(b), 65535)), copy_len)
            copied += copy_len
        else:
            b.literal(rng.randbytes(min(literal_len, remaining)))
    return b.finish()


def literals(size: int, literal_len: int = 65536, seed: int = 0):
    """Incompressible data as literals of literal_len bytes."""
    rng = random.Random(seed)
    b = ChunkBuilder()
    while len(b) < size:
        b.literal(rng.randbytes(min(literal_len, size - len(b))))
    return b.finish()


def rle(size: int, period: int = 1, copy_len: int = 64, seed: int = 0):
    """A period-byte random pattern repeated with overlapping copies
    (offset = period < length when period < copy_len)."""
    rng = random.Random(seed)
    b = ChunkBuilder()
    b.literal(rng.randbytes(min(period, size)))
    while len(b) < size:
        n = min(copy_len, size - len(b))
        if n < 4:
            b.literal(bytes(b.raw[len(b) - period + i % period] for i in range(n)))
        else:
            b.copy(period, n)
    return b.finish()


def far(size: int, copy_len: int = 32, min_offset: int = 2048, history: int = 4096, seed: int = 0):
    """history bytes of random data, then copies from at least min_offset bytes
    back (up to 64 KiB). The default is well beyond the short-term SRL window,
    so every copy reads the long-term history RAM."""
    rng = random.Random(seed)
    b = ChunkBuilder()
    b.literal(rng.randbytes(min(max(history, min_offset), size)))
    while len(b) < size:
        n = min(copy_len, size - len(b))
        if n < 4:
            b.literal(rng.randbytes(n))
        else:
            b.copy(rng.randint(min_offset, min(len(b), 65535)), n)
    return b.finish()


def edges(seed: int = 0):
    """Chunks for correctness tests: decompressed sizes around the 16-byte line
    and 64-byte beat boundaries (including an empty chunk), with mixed
    literals, short/overlapping/long copies and compressed sizes that are also
    near those boundaries."""
    rng = random.Random(seed)
    sizes = sorted({0, 1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 33, 47, 48, 49, 63, 64, 65,
                    95, 96, 97, 127, 128, 129, 255, 256, 257, 1000, 4096, 4097, 65535, 65536, 65537})
    chunks = []
    for size in sizes:
        b = ChunkBuilder()
        while len(b) < size:
            remaining = size - len(b)
            kind = rng.random()
            if len(b) >= 1 and remaining >= 4 and kind < 0.5:
                length = min(remaining, rng.choice([4, 5, 7, 8, 11, 12, 15, 16, 17, 31, 32, 33, 64, 67, 68, 130]))
                if length >= 4:
                    b.copy(rng.randint(1, min(len(b), 65535)), length)
                    continue
            b.literal(rng.randbytes(min(remaining, rng.choice([1, 2, 3, 15, 16, 17, 60, 61, 64, 256, 300]))))
        chunks.append(b.finish())
    return chunks


GENERATORS = {
    "mix": mix,
    "literals": literals,
    "rle": rle,
    "far": far,
}
