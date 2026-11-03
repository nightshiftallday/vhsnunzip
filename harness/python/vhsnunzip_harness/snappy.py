"""Raw Snappy format helpers.

decompress() is a reference decoder that can also collect element statistics.
compress() uses python-snappy, cramjam or snzip when available and otherwise a
pure-Python port of the reference compressor (snappy.cc, CompressFragment): the
same 64 KiB fragments, hash-table matching, skip heuristic and literal/copy
encoding, so the element mix is close to what libsnappy produces.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict


def write_varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_varint(buf: bytes, pos: int = 0):
    value = shift = 0
    while True:
        if pos >= len(buf) or shift > 35:
            raise ValueError("truncated or oversized varint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


# Bucket upper bounds for the element length histogram (decompressed bytes)
LEN_BUCKETS = (4, 8, 16, 32, 64, 256, 65536, 1 << 32)


@dataclass
class ElementStats:
    literals: int = 0
    literal_bytes: int = 0
    copies: int = 0
    copy_bytes: int = 0
    copy1: int = 0
    copy2: int = 0
    copy4: int = 0
    overlapping_copies: int = 0   # offset < length
    max_offset: int = 0
    len_hist: dict = field(default_factory=lambda: {str(b): 0 for b in LEN_BUCKETS})

    @property
    def elements(self) -> int:
        return self.literals + self.copies

    def add(self, other: "ElementStats") -> None:
        for key in ("literals", "literal_bytes", "copies", "copy_bytes", "copy1", "copy2",
                    "copy4", "overlapping_copies"):
            setattr(self, key, getattr(self, key) + getattr(other, key))
        self.max_offset = max(self.max_offset, other.max_offset)
        for key, count in other.len_hist.items():
            self.len_hist[key] += count

    def _bucket(self, length: int) -> None:
        for bound in LEN_BUCKETS:
            if length <= bound:
                self.len_hist[str(bound)] += 1
                return

    def to_dict(self) -> dict:
        d = asdict(self)
        d["elements"] = self.elements
        return d


def decompress(comp: bytes, stats: ElementStats = None) -> bytes:
    """Decodes one raw Snappy chunk; raises ValueError on malformed input."""
    size, pos = read_varint(comp)
    out = bytearray()
    end = len(comp)
    while pos < end:
        tag = comp[pos]
        kind = tag & 3
        pos += 1
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                nbytes = length - 59
                length = int.from_bytes(comp[pos:pos + nbytes], "little")
                pos += nbytes
            length += 1
            if pos + length > end:
                raise ValueError("literal runs past the end of the chunk")
            out += comp[pos:pos + length]
            pos += length
            if stats is not None:
                stats.literals += 1
                stats.literal_bytes += length
                stats._bucket(length)
            continue
        if kind == 1:
            length = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | comp[pos]
            pos += 1
        elif kind == 2:
            length = (tag >> 2) + 1
            offset = int.from_bytes(comp[pos:pos + 2], "little")
            pos += 2
        else:
            length = (tag >> 2) + 1
            offset = int.from_bytes(comp[pos:pos + 4], "little")
            pos += 4
        if pos > end or offset == 0 or offset > len(out):
            raise ValueError("bad copy element")
        start = len(out) - offset
        if offset >= length:
            out += out[start:start + length]
        else:
            out += (bytes(out[start:]) * (length // offset + 1))[:length]
        if stats is not None:
            stats.copies += 1
            stats.copy_bytes += length
            if kind == 1:
                stats.copy1 += 1
            elif kind == 2:
                stats.copy2 += 1
            else:
                stats.copy4 += 1
            stats.overlapping_copies += offset < length
            stats.max_offset = max(stats.max_offset, offset)
            stats._bucket(length)
    if len(out) != size:
        raise ValueError("decompressed %d bytes, header says %d" % (len(out), size))
    return bytes(out)


# -- Encoder ---------------------------------------------------------------------

def emit_literal(out: bytearray, data: bytes) -> None:
    n = len(data) - 1
    if n < 60:
        out.append(n << 2)
    else:
        nbytes = (n.bit_length() + 7) // 8
        out.append((59 + nbytes) << 2)
        out += n.to_bytes(nbytes, "little")
    out += data


def _emit_copy_at_most_64(out: bytearray, offset: int, length: int) -> None:
    if length < 12 and offset < 2048:
        out.append(1 | ((length - 4) << 2) | ((offset >> 8) << 5))
        out.append(offset & 0xFF)
    elif offset < 65536:
        out.append(2 | ((length - 1) << 2))
        out += offset.to_bytes(2, "little")
    else:
        out.append(3 | ((length - 1) << 2))
        out += offset.to_bytes(4, "little")


def emit_copy(out: bytearray, offset: int, length: int) -> None:
    """Emits a copy of any length >= 4 the way snappy.cc does (64-byte pieces)."""
    while length >= 68:
        _emit_copy_at_most_64(out, offset, 64)
        length -= 64
    if length > 64:
        _emit_copy_at_most_64(out, offset, 60)
        length -= 60
    _emit_copy_at_most_64(out, offset, length)


_BLOCK = 1 << 16
_MAX_TABLE = 1 << 14
_MARGIN = 15


def _compress_fragment(frag: bytes, out: bytearray) -> None:
    n = len(frag)
    next_emit = 0
    if n >= _MARGIN:
        table_size = 256
        while table_size < _MAX_TABLE and table_size < n:
            table_size <<= 1
        shift = 33 - table_size.bit_length()
        table = [0] * table_size

        def load(i):
            return int.from_bytes(frag[i:i + 4], "little")

        def hsh(v):
            return ((v * 0x1E35A7BD) & 0xFFFFFFFF) >> shift

        ip_limit = n - _MARGIN
        ip = 1
        next_hash = hsh(load(ip))
        done = False
        while not done:
            # Find a 4-byte match, skipping faster through incompressible data
            skip = 32
            next_ip = ip
            while True:
                ip = next_ip
                h = next_hash
                next_ip = ip + (skip >> 5)
                skip += 1
                if next_ip > ip_limit:
                    done = True
                    break
                next_hash = hsh(load(next_ip))
                candidate = table[h]
                table[h] = ip
                if load(ip) == load(candidate):
                    break
            if done:
                break

            emit_literal(out, frag[next_emit:ip])
            while True:
                base = ip
                m = 4
                while ip + m + 8 <= n and frag[candidate + m:candidate + m + 8] == frag[ip + m:ip + m + 8]:
                    m += 8
                while ip + m < n and frag[candidate + m] == frag[ip + m]:
                    m += 1
                ip += m
                emit_copy(out, base - candidate, m)
                next_emit = ip
                if ip >= ip_limit:
                    done = True
                    break
                table[hsh(load(ip - 1))] = ip - 1
                h = hsh(load(ip))
                candidate = table[h]
                table[h] = ip
                if load(ip) != load(candidate):
                    break
            if not done:
                next_hash = hsh(load(ip + 1))
                ip += 1
    if next_emit < n:
        emit_literal(out, frag[next_emit:])


def compress_builtin(data: bytes) -> bytes:
    out = bytearray(write_varint(len(data)))
    for start in range(0, len(data), _BLOCK):
        _compress_fragment(data[start:start + _BLOCK], out)
    return bytes(out)


def _find_snzip():
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, "..", "..", "..", "tests", "tools", "bin", "snzip")
    if os.access(local, os.X_OK):
        return os.path.realpath(local)
    return shutil.which("snzip")


def _compress_snzip(snzip: str, data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data")
        with open(path, "wb") as f:
            f.write(data)
        subprocess.check_call([snzip, "-traw", path])
        with open(path + ".raw", "rb") as f:
            return f.read()


def make_compressor(backend: str = "auto"):
    """Returns (name, function) for the requested backend: auto, python-snappy,
    cramjam, snzip or builtin."""
    if backend in ("auto", "python-snappy"):
        try:
            import snappy as pysnappy
            return "python-snappy", pysnappy.compress
        except ImportError:
            if backend != "auto":
                raise
    if backend in ("auto", "cramjam"):
        try:
            import cramjam
            return "cramjam", lambda d: bytes(cramjam.snappy.compress_raw(d))
        except ImportError:
            if backend != "auto":
                raise
    if backend in ("auto", "snzip"):
        snzip = _find_snzip()
        if snzip:
            return "snzip", lambda d: _compress_snzip(snzip, d)
        if backend != "auto":
            raise RuntimeError("snzip not found (run make -C tests/tools)")
    if backend in ("auto", "builtin"):
        return "builtin", compress_builtin
    raise ValueError("unknown backend " + backend)
