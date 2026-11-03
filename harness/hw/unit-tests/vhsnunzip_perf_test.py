"""Cycle-level throughput of the core in the Coyote simulation, one test per
corpus. The host stream is not randomized, so the counters show what the core
sustains when it is never starved or blocked by the host.

Environment:
  VHSNUNZIP_CORPUS      os.pathsep-separated corpus files (scripts/make_corpus.py);
                        default: a small synthetic set
  VHSNUNZIP_PERF_SIZE   bytes per default corpus (default 65536)
  VHSNUNZIP_PERF_CHUNK  chunk size of the default corpora (default 32768)
  VHSNUNZIP_PERF_MODEL  1: also run the Python reference model for comparison
  VHSNUNZIP_DUAL_ISSUE  0/1: override the build's datapath
  VHSNUNZIP_RESULTS     result directory (default <build>/results)
"""

import os
import re

from coyote_test import fpga_performance_test_case

from vsz_test_case import HarnessTestMixin
from vhsnunzip_harness import regs, synthetic
from vhsnunzip_harness.corpus import Corpus, build

SIZE = int(os.environ.get("VHSNUNZIP_PERF_SIZE", 65536))
CHUNK = int(os.environ.get("VHSNUNZIP_PERF_CHUNK", 32768))
MODEL = os.environ.get("VHSNUNZIP_PERF_MODEL", "0") == "1"
DUAL = os.environ.get("VHSNUNZIP_DUAL_ISSUE")

DEFAULT_CORPORA = [
    ("mix_l4_c4", "mix", dict(literal_len=4, copy_len=4)),
    ("mix_l8_c8", "mix", dict(literal_len=8, copy_len=8)),
    ("mix_l32_c32", "mix", dict(literal_len=32, copy_len=32)),
    ("copy95_l8_c32", "mix", dict(copy_fraction=0.95, literal_len=8, copy_len=32)),
    ("literals", "literals", {}),
    ("rle_p1", "rle", dict(period=1)),
    ("rle_p7", "rle", dict(period=7)),
    ("rle_p24", "rle", dict(period=24)),
    ("far_c16", "far", dict(copy_len=16)),
]


class PerformanceTest(HarnessTestMixin, fpga_performance_test_case.FPGAPerformanceTestCase):
    dual_issue = int(DUAL) if DUAL is not None else None

    def run_corpus(self, corpus: Corpus):
        chunks = self.supported(corpus.chunks)
        self.queue_chunks(chunks)
        values = self.run_and_read_counters()
        self.assert_simulation_output()
        self.check_counters(values, chunks)

        res = self.result(values, corpus.header())
        path = self.write_result(corpus.name, res)
        d, c = res["derived"], res["counters"]
        elements = corpus.meta["stats"]["elements"]
        print("%s: %.3f B/cycle out (%.0f%% of %d), %.3f elements/cycle, latency %s cycles; "
              "core_stall %.1f%% in_empty %.1f%% in_only %.1f%% de_blocked %.1f%% -> %s" % (
                  corpus.name, d["out_bytes_per_cycle"], 100 * d["peak_fraction"],
                  self.config["c_bytes"], elements / max(c["cycles"], 1),
                  "%.0f" % d["lat_avg"] if "lat_avg" in d else "n/a",
                  100 * d["frac_core_stall"], 100 * d["frac_in_empty"], 100 * d["frac_in_only"],
                  100 * d["frac_de_blocked"], path))


def _add_test(name, make):
    def test(self):
        self.run_corpus(make())
    test.__name__ = "test_" + re.sub(r"\W", "_", name)
    setattr(PerformanceTest, test.__name__, test)


def _synthetic(name, gen, params):
    fn = synthetic.GENERATORS[gen]
    chunks = [fn(min(CHUNK, SIZE - start), seed=i, **params)
              for i, start in enumerate(range(0, SIZE, CHUNK))]
    return build(name, chunks, MODEL, source="synthetic:" + gen, params=params, chunk_size=CHUNK)


_paths = [p for p in os.environ.get("VHSNUNZIP_CORPUS", "").split(os.pathsep) if p]
if _paths:
    for _path in _paths:
        _add_test(os.path.splitext(os.path.basename(_path))[0], lambda p=_path: Corpus.read(p))
else:
    for _name, _gen, _params in DEFAULT_CORPORA:
        _add_test(_name, lambda n=_name, g=_gen, p=_params: _synthetic(n, g, p))
