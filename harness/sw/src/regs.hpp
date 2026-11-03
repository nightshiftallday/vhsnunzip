// CSR map of the harness vFPGA (harness/hw/src/vfpga_top.svh).
// Keep in sync with harness/python/vhsnunzip_harness/regs.py.
#pragma once

#include <cstdint>

namespace vsz {

constexpr uint32_t REG_CTRL      = 0; // W: bit 0 clears the counters
constexpr uint32_t REG_THROTTLE  = 1; // RW: [15:0] input gate mask, [31:16] output gate mask
constexpr uint32_t REG_INFO      = 2;
constexpr uint32_t REG_TIMESTAMP = 3;

constexpr uint64_t INFO_MAGIC = 0x56535A31;

struct Counter {
    const char *name;
    uint32_t    reg;
};

constexpr Counter COUNTERS[] = {
    {"cycles", 4},      {"chunks_in", 5},   {"chunks_out", 6}, {"co_lines", 7},
    {"co_bytes", 8},    {"co_stall", 9},    {"de_lines", 10},  {"de_bytes", 11},
    {"de_blocked", 12}, {"core_stall", 13}, {"in_empty", 14},  {"in_only", 15},
    {"lat_count", 16},  {"lat_sum", 17},    {"lat_max", 18},   {"in_flight", 19},
};
constexpr int N_COUNTERS = sizeof(COUNTERS) / sizeof(COUNTERS[0]);

// Mutually exclusive classes of an active cycle; they sum to "cycles"
constexpr const char *CYCLE_CLASSES[] = {"de_lines", "de_blocked", "core_stall", "in_empty",
                                         "in_only"};

} // namespace vsz
