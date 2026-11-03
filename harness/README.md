# vhsnunzip Coyote harness

Measures throughput, resource use and timing of `vhsnunzip_unbuffered` inside a
[Coyote](https://github.com/fpgasystems/Coyote) vFPGA. The same RTL and the same
counters serve four flows:

| Question | Flow | Turnaround |
| --- | --- | --- |
| Bytes and elements per cycle, where the cycles go | Coyote simulation (`hw/unit-tests`) | minutes |
| LUT/FF/SRL/URAM per module, critical paths, f_max estimate | Out-of-context Vivado run (`scripts/ooc.sh`) | ~10-30 min |
| The same in context, plus a bitstream | Coyote build (`scripts/coyote_build.sh`) | hours |
| Real throughput, clock and host GB/s | Benchmark on an Alveo card (`sw/`) | seconds |

`scripts/summarize.py` turns the results of all flows into Markdown tables and
compares a baseline with a candidate. That is the loop for architecture changes.

## Layout

```
harness/
  hw/                     Coyote project (cmake -S harness/hw)
    src/vfpga_top.svh     vFPGA: host stream -> core -> host stream, CSRs
    src/hdl/              stream adapters, counters, CSR slave, VHDL generic adapter
    cmake/                generated harness package template
    sources.exclude       src/hdl files that are not part of vhsnunzip_unbuffered
    unit-tests/           Coyote unit tests: correctness and performance
  sw/                     host benchmark (libcoyote; real card or simulation)
  python/vhsnunzip_harness/  raw Snappy codec, synthetic chunks, corpus format, CSR map
  scripts/                make_corpus.py, coyote_build.sh, ooc.sh/.tcl, extract_reports.tcl, summarize.py
  ooc/                    register-ring top for the out-of-context run
```

## How the vFPGA works

```
axis_host_recv[0] -> reg -> gate -> 64 B beats to C_BYTES lines -> vhsnunzip_unbuffered
  -> C_BYTES lines to 64 B beats -> gate -> reg -> axis_host_send[0]
```

The host sends each raw Snappy chunk as one `LOCAL_TRANSFER`: the compressed
chunk goes in and exactly the decompressed bytes come out. `tlast` marks the end
of a chunk. The output packer folds the core's possible empty last line into the
previous beat, so every chunk produces `ceil(len/64)` beats, as Coyote requires.
The gates are LFSR throttles for the input and output (CSR `THROTTLE`). The core
sits behind a VHDL adapter (`vhsnunzip_harness_core`) that takes its generics as
naturals.

Build options (CMake, `coyote_build.sh` flags in brackets):

| Option | Default | |
| --- | --- | --- |
| `VHSNUNZIP_DUAL_ISSUE` [`--dual`] | OFF | dual-issue datapath |
| `VHSNUNZIP_LONG_CHUNKS` [`--short` = OFF] | ON | chunks > 64 KiB |
| `VHSNUNZIP_RAM_STYLE` [`--block`] | ultra | UltraRAM or block RAM history |
| `VHSNUNZIP_BYTES` [`--bytes N`] | RTL value (16) | patches `C_BYTES` in a staged copy of `vhsnunzip_utils_pkg.vhd` |
| `EN_UCLK=1 UCLK_F=MHz` [`--uclk MHz`] | aclk, 250 MHz | user clock for the vFPGA (f_max sweeps in context) |
| `FDEV_NAME` [`--fdev`] | u55c | Coyote target |
| `CYT_DIR` | `$CYT_DIR`, `<repo>/coyote`, `<repo>/../Coyote` | Coyote checkout |

The build stages `src/hdl` minus `sources.exclude` into `<build>/vhsnunzip_src/hdl`.
Testbenches, `*.sim.vhd` and the toplevels with hardcoded 8-byte ports (buffered,
multicore, `vhsnunzip_unbuffered_small`) would otherwise be compiled too. The staged files are symlinks, so RTL edits take
effect without reconfiguring. New files need a `cmake` rerun.

## Counters

The CSRs are read with `cThread::getCSR(index)`; the map is in
`hw/src/vfpga_top.svh`. A cycle is **active** while at least one chunk is in
the core, from its first input line to its last output line. Every active cycle
is counted in exactly one class:

| Class | Condition | Reading |
| --- | --- | --- |
| `de_lines` | output line handshake | useful cycles; `de_bytes/cycles` is B/cycle |
| `core_stall` | no output, input offered but not accepted | the core is the bottleneck (element rate, decoder, history hazards) |
| `in_only` | no output, input accepted | pipeline fill, input consumed without output (e.g. literal headers, chunk start) |
| `in_empty` | no output, no input offered | starved by the host, or draining at a chunk end |
| `de_blocked` | output not accepted | backpressure from the host write path |

Also: `co_bytes`, `co_stall`, chunk counts, a free-running `TIMESTAMP`, and the
latency of isolated chunks (first input line to first output line; with
`--depth 1` every chunk is isolated). The tools derive elements per cycle from
the corpus statistics (`1.0` is the single-issue limit) and GB/s at the
measured clock. They also show the B/cycle of the Python reference model in
`tests/emu` when the corpus has it (`--model`) and its `WI` equals `C_BYTES`.
The model gives single-issue cycles for single-issue builds and fold-model
cycles for dual-issue builds.

## Workflows

Prerequisites: a Coyote checkout, Vivado (2022.1 or newer; not 2023.2 for
simulation), CMake ≥ 3.16, Python ≥ 3.10, and for the benchmark the Coyote
software dependencies (Boost). Paths below are relative to the repository root.

### 1. Corpora

```sh
# Snappy test data (tests/tools/snappy/testdata) + synthetic corner cases
harness/scripts/make_corpus.py suite -o corpora --model
# A file, chunked like framed Snappy
harness/scripts/make_corpus.py file data.parquet -o corpora/data.corpus --chunk 64K --model
# Synthetic: mix (literal/copy lengths), literals, rle (overlapping copies), far (long-term reads)
harness/scripts/make_corpus.py synth mix -o corpora/short.corpus --set literal_len=4 copy_len=4
harness/scripts/make_corpus.py info corpora/*.corpus
```

Compression uses python-snappy, cramjam or `tests/tools/bin/snzip` if one is
available. Otherwise it uses a pure-Python port of the reference compressor,
which produces the same sizes as libsnappy on the Snappy test data. Every chunk
is checked with the reference decoder, and the element statistics are stored in
the corpus.

### 2. Cycle-level performance in simulation

```sh
harness/scripts/coyote_build.sh --sim                # harness/builds/sim-brtl-single-ultra
export PYTHONPATH=harness/builds/sim-brtl-single-ultra
python3 -m unittest discover -s harness/hw/unit-tests -p 'vhsnunzip_test.py'        # correctness
python3 -m unittest discover -s harness/hw/unit-tests -p 'vhsnunzip_perf_test.py'   # throughput
VHSNUNZIP_DUAL_ISSUE=1 VHSNUNZIP_CORPUS=corpora/alice29_txt.corpus \
    python3 -m unittest discover -s harness/hw/unit-tests -p 'vhsnunzip_perf_test.py'
harness/scripts/summarize.py perf harness/builds/sim-brtl-single-ultra/results
```

The correctness tests run every case for single and dual issue, and for single
issue without `LONG_CHUNKS` (the short-chunk decoder, which `tests/test.py` only
simulates at 8 bytes). They cover
edge sizes, short elements, overlapping copies, long literals, far copies and
both throttles, with the testbench's handshake randomization enabled. They check
the output and the counter invariants. The performance tests disable the
randomization, so the host never starves or blocks the core. They write one JSON
per corpus. `VHSNUNZIP_VCD=1` dumps waveforms. Keep corpora small (the default
is 64 KiB each), because the whole Coyote testbench is simulated.

### 3. Fast utilization and timing (out of context)

```sh
harness/scripts/ooc.sh ooc/single-300 period=3.333
harness/scripts/ooc.sh ooc/dual-300 dual=1 period=3.333
harness/scripts/ooc.sh --sweep ooc/dual-sweep 250 300 350 dual=1
harness/scripts/summarize.py impl ooc/single-300 ooc/dual-300
```

This implements the core alone on the U55C part inside a register ring, so
every port path is timed too. The output has hierarchical utilization per
module, the worst internal and boundary paths with logic levels, and an
f_max estimate `1000 / (period - WNS)`. Confirm the estimate with a run at that
period.

### 4. Coyote build: in-context numbers and the bitstream

```sh
harness/scripts/coyote_build.sh --detach                  # aclk (250 MHz)
harness/scripts/coyote_build.sh --dual --uclk 300 --detach
```

Builds go to `harness/builds/<fdev>-b<bytes>-<single|dual>-<ram>[-u<MHz>]`. After
`bitgen`, `extract_reports.tcl` writes the core's reports from
`checkpoints/config_0/shell_routed_c0.dcp` to `reports_vsz/`. To rerun it on any
checkpoint:
`vivado -mode batch -source harness/scripts/extract_reports.tcl -tclargs <dcp> <out_dir>`.

### 5. Benchmark on the card

```sh
cmake -S harness/sw -B build-sw && cmake --build build-sw
# program the card with <build>/bitstreams/cyt_top.bit and load the Coyote driver, then
build-sw/vhsnunzip_bench -v -r 10 -d 16 -o results/hw -b <build>/harness_config.json corpora/*.corpus
build-sw/vhsnunzip_bench -r 3 -d 1 -o results/hw-latency corpora/*.corpus      # latency
build-sw/vhsnunzip_bench -t 0x3,0 corpora/alice29_txt.corpus                   # input gated to ~1/4
harness/scripts/summarize.py perf results/hw
```

`-d` sets the chunks in flight, `-v` checks the first repetition's output, and
`clk MHz` is measured from `TIMESTAMP` against wall time. The same program runs
against the simulation: configure with `-DEN_SIM=ON`, set
`COYOTE_SIM_DIR=<sim build>`, and use small corpora with `-r 1`.

### 6. Comparing an architecture change

```sh
harness/scripts/summarize.py compare results/base results/cand    # per corpus: B/cycle, GB/s, stalls
harness/scripts/summarize.py compare ooc/base-300 ooc/cand-300     # utilization and f_max deltas
```

Results carry the configuration and the git revision of the RTL (`-dirty` if
`src/` has uncommitted changes).

## Notes

- Clock: without `--uclk` the vFPGA runs at Coyote's `aclk` (250 MHz on the
  U55C). A design that meets 300 MHz out of context needs `--uclk 300` to show
  it in context.
- Limits of the core that the harness inherits: copy offsets above 64 KiB are
  not supported, and dual issue decodes literals longer than 64 KiB wrongly
  (`docs/architecture/07-performance-and-verification.md`). Chunks over 64 KiB
  need `LONG_CHUNKS`. The benchmark skips chunks that the build cannot run and
  chunks that decompress to 0 bytes, because Coyote cannot write 0 bytes.
- The counters observe the core's ports only. A per-stage breakdown (decoder vs.
  command generators vs. datapath) would need the pipelines' internal
  handshakes as an extra port on `vhsnunzip_unbuffered`.
