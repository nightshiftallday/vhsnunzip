"""Correctness of the harness and the core in the Coyote simulation. The
testbench randomizes the host stream handshakes; the throttle tests add
pseudo-random gaps inside the vFPGA as well. Every test runs for the single-
and the dual-issue datapath, and single issue without LONG_CHUNKS."""

from coyote_test import fpga_test_case

from vsz_test_case import HarnessTestMixin
from vhsnunzip_harness import synthetic


class SingleIssueTest(HarnessTestMixin, fpga_test_case.FPGATestCase):
    dual_issue = 0

    def run_chunks(self, chunks, in_mask=0, out_mask=0):
        chunks = self.supported(chunks)
        if in_mask or out_mask:
            self.set_throttle(in_mask, out_mask)
        self.queue_chunks(chunks)
        values = self.run_and_read_counters()
        self.assert_simulation_output()
        self.check_counters(values, chunks)

    def test_edge_sizes(self):
        # Decompressed sizes around line and beat boundaries, including an
        # empty chunk and chunks ending in an empty last line
        self.run_chunks(synthetic.edges(seed=1))

    def test_short_elements(self):
        self.run_chunks([synthetic.mix(8192, literal_len=1, copy_len=4, seed=2),
                         synthetic.mix(8192, literal_len=4, copy_len=5, seed=3)])

    def test_overlapping_copies(self):
        self.run_chunks([synthetic.rle(2048 + p, period=p, copy_len=64, seed=p)
                         for p in (1, 2, 3, 5, 7, 13, 16, 24, 33)])

    def test_long_literals_and_far_copies(self):
        self.run_chunks([synthetic.literals(20000, literal_len=5000),
                         synthetic.far(16384, copy_len=20, seed=4),
                         synthetic.mix(65536, copy_fraction=0.9, literal_len=300, copy_len=64, seed=5)])

    def test_input_throttled(self):
        self.run_chunks(synthetic.edges(seed=6)[:24], in_mask=0x7)

    def test_output_throttled(self):
        self.run_chunks([synthetic.mix(4096, literal_len=3, copy_len=6, seed=7),
                         synthetic.rle(3000, period=5)] + synthetic.edges(seed=8)[:16],
                        out_mask=0x3)


class DualIssueTest(SingleIssueTest):
    dual_issue = 1


class ShortChunksTest(SingleIssueTest):
    # vhsnunzip_decoder (chunks <= 64 KiB), which tests/test.py only simulates at WI = 8
    long_chunks = 0
