#!/usr/bin/env python3
"""Creates and inspects benchmark corpora for the vhsnunzip Coyote harness.

  make_corpus.py file  INPUT -o OUT.corpus [--chunk 65536] [--backend auto]
  make_corpus.py synth mix -o OUT.corpus [--size 1M] [--set literal_len=8 copy_len=8]
  make_corpus.py suite -o DIR [--testdata tests/tools/snappy/testdata]
  make_corpus.py info A.corpus [B.corpus ...]

--model also runs the Python reference model (tests/emu) and stores the cycle
counts it predicts, which summarize.py compares with the measured cycles.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from vhsnunzip_harness import synthetic                       # noqa: E402
from vhsnunzip_harness.corpus import (Corpus, REPO_DIR, build,  # noqa: E402
                                      model_cycles, split_and_compress)
from vhsnunzip_harness.snappy import make_compressor           # noqa: E402

# Synthetic part of the suite: name, generator, parameters
SUITE = [
    ("mix_l4_c4", "mix", dict(literal_len=4, copy_len=4)),
    ("mix_l8_c8", "mix", dict(literal_len=8, copy_len=8)),
    ("mix_l16_c16", "mix", dict(literal_len=16, copy_len=16)),
    ("mix_l32_c32", "mix", dict(literal_len=32, copy_len=32)),
    ("mix_l64_c64", "mix", dict(literal_len=64, copy_len=64)),
    ("copy95_l8_c32", "mix", dict(copy_fraction=0.95, literal_len=8, copy_len=32)),
    ("literals", "literals", {}),
    ("rle_p1", "rle", dict(period=1)),
    ("rle_p3", "rle", dict(period=3)),
    ("rle_p7", "rle", dict(period=7)),
    ("rle_p13", "rle", dict(period=13)),
    ("rle_p24", "rle", dict(period=24)),
    ("far_c16", "far", dict(copy_len=16)),
]

DEFAULT_TESTDATA = os.path.join(REPO_DIR, "tests", "tools", "snappy", "testdata")
TESTDATA_FILES = ["alice29.txt", "asyoulik.txt", "lcet10.txt", "plrabn12.txt", "html", "html_x_4",
                  "urls.10K", "fireworks.jpeg", "paper-100k.pdf", "kppkn.gtb", "geo.protodata"]


def parse_size(text: str) -> int:
    units = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30}
    text = text.strip().lower().rstrip("ib")
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text, 0)


def parse_params(items) -> dict:
    params = {}
    for item in items or []:
        key, value = item.split("=", 1)
        params[key] = float(value) if "." in value else int(value, 0)
    return params


def synth_chunks(gen: str, size: int, chunk: int, seed: int, params: dict) -> list:
    fn = synthetic.GENERATORS[gen]
    chunks = []
    for i, start in enumerate(range(0, size, chunk)):
        chunks.append(fn(min(chunk, size - start), seed=seed + i, **params))
    return chunks


def file_corpus(path: str, chunk: int, backend: str, model: bool, name: str = None) -> Corpus:
    backend_name, compress = make_compressor(backend)
    with open(path, "rb") as f:
        data = f.read()
    chunks = split_and_compress(data, chunk, compress)
    return build(name or os.path.basename(path), chunks, model, source=os.path.abspath(path),
                 backend=backend_name, chunk_size=chunk)


def synth_corpus(name: str, gen: str, size: int, chunk: int, seed: int, params: dict,
                 model: bool) -> Corpus:
    chunks = synth_chunks(gen, size, chunk, seed, params)
    return build(name, chunks, model, source="synthetic:" + gen, params=params, seed=seed,
                 chunk_size=chunk)


def describe(corpus: Corpus) -> str:
    h = corpus.header()
    s = h["stats"]
    elements = max(s["elements"], 1)
    lines = [
        "%s: %d chunks, %d -> %d bytes (ratio %.2f), %d elements, %.2f B/element" % (
            h["name"], h["chunks"], h["comp_bytes"], h["raw_bytes"],
            h["raw_bytes"] / max(h["comp_bytes"], 1), s["elements"], h["raw_bytes"] / elements),
        "  literals %d (%d B), copies %d (%d B; copy1/2/4 %d/%d/%d, overlapping %d, max offset %d)" % (
            s["literals"], s["literal_bytes"], s["copies"], s["copy_bytes"], s["copy1"],
            s["copy2"], s["copy4"], s["overlapping_copies"], s["max_offset"]),
        "  element length <= " + ", ".join(
            "%s: %d" % kv for kv in sorted(s["len_hist"].items(), key=lambda kv: int(kv[0])) if kv[1]),
    ]
    m = h.get("model")
    if m:
        lines.append("  model (WI=%d): single %.3f B/cycle, dual %.3f B/cycle, fold %.3f B/cycle" % (
            m["wi"], h["raw_bytes"] / m["single_cycles"], h["raw_bytes"] / max(m["dual_cycles"] or 0, 1),
            h["raw_bytes"] / max(m["fold_cycles"] or 0, 1)))
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, out=True):
        if out:
            sp.add_argument("-o", "--out", required=True)
        sp.add_argument("--chunk", type=parse_size, default=65536, help="chunk size (default 64K)")
        sp.add_argument("--model", action="store_true", help="store the reference model's cycles")

    sp = sub.add_parser("file", help="chunk and compress a file")
    sp.add_argument("input")
    sp.add_argument("--name")
    sp.add_argument("--backend", default="auto",
                    choices=["auto", "python-snappy", "cramjam", "snzip", "builtin"])
    common(sp)

    sp = sub.add_parser("synth", help="synthetic chunks")
    sp.add_argument("generator", choices=sorted(synthetic.GENERATORS))
    sp.add_argument("--name")
    sp.add_argument("--size", type=parse_size, default=1 << 20)
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--set", nargs="*", metavar="KEY=VALUE", help="generator parameters")
    common(sp)

    sp = sub.add_parser("suite", help="standard set: Snappy test data and synthetic corners")
    sp.add_argument("--testdata", default=DEFAULT_TESTDATA)
    sp.add_argument("--size", type=parse_size, default=1 << 20, help="size of each synthetic corpus")
    sp.add_argument("--backend", default="auto",
                    choices=["auto", "python-snappy", "cramjam", "snzip", "builtin"])
    common(sp)

    sp = sub.add_parser("info", help="print corpus statistics")
    sp.add_argument("corpora", nargs="+")
    sp.add_argument("--model", action="store_true", help="run the reference model now")

    args = p.parse_args()

    if args.cmd == "info":
        for path in args.corpora:
            corpus = Corpus.read(path)
            corpus.meta.setdefault("stats", corpus.verify().to_dict())
            if args.model:
                corpus.meta["model"] = model_cycles(corpus.chunks)
            print(describe(corpus))
        return

    if args.cmd == "file":
        corpus = file_corpus(args.input, args.chunk, args.backend, args.model, args.name)
        corpus.write(args.out)
        print(describe(corpus))
        return

    if args.cmd == "synth":
        params = parse_params(args.set)
        name = args.name or args.generator + "".join("_%s%s" % kv for kv in params.items())
        corpus = synth_corpus(name, args.generator, args.size, args.chunk, args.seed, params, args.model)
        corpus.write(args.out)
        print(describe(corpus))
        return

    os.makedirs(args.out, exist_ok=True)
    corpora = []
    if os.path.isdir(args.testdata):
        for fname in TESTDATA_FILES:
            path = os.path.join(args.testdata, fname)
            if os.path.exists(path):
                corpora.append(file_corpus(path, args.chunk, args.backend, args.model))
    else:
        print("note: %s not found, skipping the Snappy test data" % args.testdata, file=sys.stderr)
    for name, gen, params in SUITE:
        corpora.append(synth_corpus(name, gen, args.size, args.chunk, 0, params, args.model))
    for corpus in corpora:
        corpus.write(os.path.join(args.out, corpus.name.replace(".", "_") + ".corpus"))
        print(describe(corpus))


if __name__ == "__main__":
    main()
