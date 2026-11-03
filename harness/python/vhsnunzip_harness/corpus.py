"""Benchmark corpus: a list of raw Snappy chunks with their decompressed data.

File format (little-endian), read by harness/sw/src/main.cpp as well:
    b"VSZCORP1"
    u32 header length, header (UTF-8 JSON, see Corpus.header())
    u32 chunk count
    per chunk: u32 compressed length, u32 decompressed length, compressed, decompressed
"""

import json
import os
import struct
import sys
from dataclasses import dataclass, field

from .snappy import ElementStats, decompress

MAGIC = b"VSZCORP1"
REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


@dataclass
class Corpus:
    name: str
    chunks: list                       # [(compressed, decompressed)]
    meta: dict = field(default_factory=dict)

    @property
    def raw_bytes(self) -> int:
        return sum(len(r) for _, r in self.chunks)

    @property
    def comp_bytes(self) -> int:
        return sum(len(c) for c, _ in self.chunks)

    def verify(self) -> ElementStats:
        """Decodes every chunk with the reference decoder, checks it and
        returns the element statistics."""
        stats = ElementStats()
        for i, (comp, raw) in enumerate(self.chunks):
            if decompress(comp, stats) != raw:
                raise ValueError("%s: chunk %d does not decompress to its data" % (self.name, i))
        return stats

    def header(self) -> dict:
        h = {
            "name": self.name,
            "chunks": len(self.chunks),
            "raw_bytes": self.raw_bytes,
            "comp_bytes": self.comp_bytes,
            "max_chunk": max((len(r) for _, r in self.chunks), default=0),
        }
        h.update(self.meta)
        return h

    def write(self, path: str) -> None:
        header = json.dumps(self.header(), sort_keys=True).encode()
        with open(path, "wb") as f:
            f.write(MAGIC)
            f.write(struct.pack("<I", len(header)))
            f.write(header)
            f.write(struct.pack("<I", len(self.chunks)))
            for comp, raw in self.chunks:
                f.write(struct.pack("<II", len(comp), len(raw)))
                f.write(comp)
                f.write(raw)

    @staticmethod
    def read(path: str) -> "Corpus":
        with open(path, "rb") as f:
            data = f.read()
        if data[:8] != MAGIC:
            raise ValueError("%s is not a vhsnunzip corpus" % path)
        (hlen,) = struct.unpack_from("<I", data, 8)
        header = json.loads(data[12:12 + hlen])
        pos = 12 + hlen
        (count,) = struct.unpack_from("<I", data, pos)
        pos += 4
        chunks = []
        for _ in range(count):
            clen, rlen = struct.unpack_from("<II", data, pos)
            pos += 8
            comp = data[pos:pos + clen]
            pos += clen
            raw = data[pos:pos + rlen]
            pos += rlen
            chunks.append((comp, raw))
        meta = {k: v for k, v in header.items()
                if k not in ("name", "chunks", "raw_bytes", "comp_bytes", "max_chunk")}
        return Corpus(header["name"], chunks, meta)


def split_and_compress(data: bytes, chunk_size: int, compress) -> list:
    chunks = []
    for start in range(0, max(len(data), 1), chunk_size):
        raw = data[start:start + chunk_size]
        chunks.append((compress(raw), raw))
    return chunks


def model_cycles(chunks: list) -> dict:
    """Runs the Python reference model of tests/emu on the chunks and returns
    the cycles it predicts: single issue (one command per cycle), the dual-
    issue model and the SRL-faithful fold model that the dual RTL mirrors.
    The model width is WI of tests/emu/streams.py."""
    tests_dir = os.path.join(REPO_DIR, "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from emu import operators as op
    from emu.streams import WI

    comp = [c for c, _ in chunks]
    raw = [r for _, r in chunks]

    def commands():
        return op.cmd_gen_2(op.cmd_gen_1(op.decoder(op.pre_decoder(op.data_source(comp)))))

    cm = op.Counter(commands())
    op.drain(op.verifier(op.datapath(cm), raw))
    dual, fold = {}, {}
    op.drain(op.verifier(op.datapath_dual(commands(), dual), raw))
    op.drain(op.verifier(op.datapath_fold(commands(), fold), raw))
    return {
        "wi": WI,
        "single_cycles": cm.count,
        "dual_cycles": dual.get("cycles"),
        "fold_cycles": fold.get("cycles"),
        "fold_pairs": fold.get("pairs"),
    }


def build(name: str, chunks: list, run_model: bool = False, **meta) -> Corpus:
    """Creates a corpus, checks every chunk with the reference decoder and
    records the element statistics (and optionally the model's cycles)."""
    corpus = Corpus(name, chunks, dict(meta))
    corpus.meta["stats"] = corpus.verify().to_dict()
    if run_model:
        corpus.meta["model"] = model_cycles(chunks)
    return corpus
