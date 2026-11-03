"""Shared base of the vhsnunzip harness unit tests (Coyote unit-test framework).

Run from a simulation build (cmake -S harness/hw -B build-sim && make -C build-sim sim):
    PYTHONPATH=build-sim python3 -m unittest discover -s harness/hw/unit-tests -p '*_test.py'
"""

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "python"))

from coyote_test import constants, simulation_time                    # noqa: E402
from coyote_test.fpga_register import vFPGARegister                  # noqa: E402
from coyote_test.io_writer import CoyoteOperator                     # noqa: E402

from vhsnunzip_harness import regs                                    # noqa: E402


def build_config() -> dict:
    """Configuration recorded by harness/hw/CMakeLists.txt in the build folder."""
    path = os.path.join(constants.HW_BUILD_FOLDER, "harness_config.json")
    with open(path) as f:
        return json.load(f)


class HarnessTestMixin:
    """Mix into FPGATestCase / FPGAPerformanceTestCase.

    dual_issue / long_chunks override the build configuration through the
    defines of the generated vhsnunzip_harness_pkg (None keeps the build value).
    """

    dual_issue: Optional[int] = None
    long_chunks: Optional[int] = None
    dump_vcd = os.environ.get("VHSNUNZIP_VCD", "0") == "1"

    def setUp(self):
        super().setUp()
        # The default is a fixed 4 us, far too short for whole chunks
        self.overwrite_simulation_time(simulation_time.SimulationTime.till_finished())
        self.config = build_config()
        defines = {}
        if self.dual_issue is not None:
            defines["VHSNUNZIP_DUAL_ISSUE"] = str(int(self.dual_issue))
            self.config["dual_issue"] = int(self.dual_issue)
        if self.long_chunks is not None:
            defines["VHSNUNZIP_LONG_CHUNKS"] = str(int(self.long_chunks))
            self.config["long_chunks"] = int(self.long_chunks)
        if defines:
            self.set_system_verilog_defines(defines)

    def supported(self, chunks: List[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
        """Drops chunks larger than 64 KiB if the core is built without LONG_CHUNKS."""
        if self.config["long_chunks"]:
            return chunks
        return [c for c in chunks if len(c[1]) <= 65536]

    def set_throttle(self, in_mask: int = 0, out_mask: int = 0) -> None:
        value = regs.throttle_value(in_mask, out_mask)
        self.write_register(vFPGARegister(regs.THROTTLE, bytearray(value.to_bytes(8, "little"))))

    def queue_chunks(self, chunks: List[Tuple[bytes, bytes]]) -> None:
        """One LOCAL_READ per compressed chunk and one LOCAL_WRITE per
        decompressed chunk; empty chunks produce no output beats."""
        for comp, raw in chunks:
            self.set_stream_input(0, bytearray(comp))
            if raw:
                self.set_expected_output(0, bytearray(raw))

    def run_and_read_counters(self) -> Dict[str, int]:
        """Simulates until all outputs are written, then reads INFO and the counters."""
        end_event = self.simulate_fpga_non_blocking()
        io = self.get_io_writer()
        if self._expected_completed_write_transfers > 0:
            io.block_till_completed(CoyoteOperator.LOCAL_WRITE,
                                    self._expected_completed_write_transfers, end_event)
        values = {"info": io.ctrl_read(regs.INFO, end_event)}
        for name, index in regs.COUNTERS.items():
            values[name] = io.ctrl_read(index, end_event)
        io.all_input_done()
        self.finish_fpga_simulation()
        return values

    def check_counters(self, values: Dict[str, int], chunks: List[Tuple[bytes, bytes]]) -> None:
        info = regs.decode_info(values["info"])
        self.assertEqual(info["c_bytes"], self.config["c_bytes"])
        self.assertEqual(info["dual_issue"], self.config["dual_issue"])
        self.assertEqual(values["chunks_in"], len(chunks))
        self.assertEqual(values["chunks_out"], len(chunks))
        self.assertEqual(values["in_flight"], 0)
        self.assertEqual(values["co_bytes"], sum(len(c) for c, _ in chunks))
        self.assertEqual(values["de_bytes"], sum(len(r) for _, r in chunks))
        self.assertEqual(sum(values[k] for k in regs.CYCLE_CLASSES), values["cycles"])

    def result(self, values: Dict[str, int], corpus_header: dict, **extra) -> dict:
        counters = {k: v for k, v in values.items() if k != "info"}
        res = {
            "kind": "sim",
            "config": self.config,
            "corpus": corpus_header,
            "counters": counters,
            "derived": regs.derive(counters, self.config["c_bytes"]),
        }
        res.update(extra)
        return res

    def write_result(self, name: str, res: dict) -> str:
        out_dir = os.environ.get("VHSNUNZIP_RESULTS",
                                 os.path.join(constants.HW_BUILD_FOLDER, "results"))
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "sim_%s_%s.json" % (
            "dual" if self.config["dual_issue"] else "single", name))
        with open(path, "w") as f:
            json.dump(res, f, indent=2, sort_keys=True)
        return path
