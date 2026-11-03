"""CSR map of the harness vFPGA (harness/hw/src/vfpga_top.svh). Keep in sync
with harness/sw/src/regs.hpp."""

CTRL = 0          # W: bit 0 clears the counters
THROTTLE = 1      # RW: [15:0] input gate mask, [31:16] output gate mask
INFO = 2
TIMESTAMP = 3

INFO_MAGIC = 0x56535A31

# Counters, cleared through CTRL; read in this order
COUNTERS = {
    "cycles": 4,
    "chunks_in": 5,
    "chunks_out": 6,
    "co_lines": 7,
    "co_bytes": 8,
    "co_stall": 9,
    "de_lines": 10,
    "de_bytes": 11,
    "de_blocked": 12,
    "core_stall": 13,
    "in_empty": 14,
    "in_only": 15,
    "lat_count": 16,
    "lat_sum": 17,
    "lat_max": 18,
    "in_flight": 19,
}

# Mutually exclusive classes of an active cycle; they sum to "cycles"
CYCLE_CLASSES = ("de_lines", "de_blocked", "core_stall", "in_empty", "in_only")


def decode_info(value: int) -> dict:
    if value >> 32 != INFO_MAGIC:
        raise ValueError("INFO register 0x%016x: not the vhsnunzip harness" % value)
    return {
        "c_bytes": value & 0xFF,
        "dual_issue": (value >> 8) & 1,
        "long_chunks": (value >> 9) & 1,
        "ram_style": "block" if (value >> 10) & 1 else "ultra",
    }


def throttle_value(in_mask: int = 0, out_mask: int = 0) -> int:
    return (in_mask & 0xFFFF) | ((out_mask & 0xFFFF) << 16)


def derive(counters: dict, c_bytes: int) -> dict:
    """Derived metrics from a counter snapshot."""
    cycles = counters["cycles"] or 1
    d = {
        "out_bytes_per_cycle": counters["de_bytes"] / cycles,
        "in_bytes_per_cycle": counters["co_bytes"] / cycles,
        "line_utilization": counters["de_lines"] / cycles,
        "check_class_sum": sum(counters[k] for k in CYCLE_CLASSES) == counters["cycles"],
    }
    for k in CYCLE_CLASSES:
        d["frac_" + k] = counters[k] / cycles
    if counters["lat_count"]:
        d["lat_avg"] = counters["lat_sum"] / counters["lat_count"]
    d["peak_fraction"] = d["out_bytes_per_cycle"] / c_bytes
    return d
