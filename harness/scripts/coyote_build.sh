#!/bin/bash
# Configures and runs a Coyote build of the vhsnunzip harness.
#
#   coyote_build.sh [options] [-- extra cmake arguments]
#
#   --sim          simulation project only (make sim) for unit-tests/ and the sim benchmark
#   --dual         dual-issue datapath
#   --block        block RAM history instead of UltraRAM
#   --short        LONG_CHUNKS=0 (chunks <= 64 KiB)
#   --bytes N      override C_BYTES of vhsnunzip_utils_pkg.vhd
#   --uclk MHZ     clock the vFPGA from the user clock (EN_UCLK=1, UCLK_F=MHZ); default: aclk (250 MHz)
#   --fdev DEV     target card (default u55c)
#   --cores N      parallel Vivado jobs (COMP_CORES)
#   --name NAME    build directory name (default derived from the options)
#   --dir DIR      parent of the build directories (default harness/builds)
#   --detach       run in a detached tmux session; follow <build>/build.log
#
# A hardware build runs `project` and `bitgen`, then extracts the core's
# utilization and timing from the routed checkpoint into <build>/reports_vsz
# (scripts/extract_reports.tcl). Bitstream: <build>/bitstreams/cyt_top.bit.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
harness_dir="$(dirname "$script_dir")"

sim=0 dual=OFF ram=ultra long=ON bytes="" uclk="" fdev=u55c cores="" name="" detach=0
parent="$harness_dir/builds"
extra=()
while [ $# -gt 0 ]; do
    case "$1" in
        --sim) sim=1 ;;
        --dual) dual=ON ;;
        --block) ram=block ;;
        --short) long=OFF ;;
        --bytes) bytes="$2"; shift ;;
        --uclk) uclk="$2"; shift ;;
        --fdev) fdev="$2"; shift ;;
        --cores) cores="$2"; shift ;;
        --name) name="$2"; shift ;;
        --dir) parent="$2"; shift ;;
        --detach) detach=1 ;;
        --) shift; extra=("$@"); break ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -z "$name" ]; then
    name="b${bytes:-rtl}-$([ $dual = ON ] && echo dual || echo single)-$ram$([ $long = OFF ] && echo -short || true)"
    name="$([ $sim = 1 ] && echo sim || echo $fdev)-$name$([ -n "$uclk" ] && echo "-u$uclk" || true)"
fi
build="$(mkdir -p "$parent" && cd "$parent" && pwd)/$name"
mkdir -p "$build"

args=(-S "$harness_dir/hw" -B "$build" -DFDEV_NAME="$fdev" -DVHSNUNZIP_DUAL_ISSUE=$dual
      -DVHSNUNZIP_LONG_CHUNKS=$long -DVHSNUNZIP_RAM_STYLE=$ram)
[ -n "$bytes" ] && args+=(-DVHSNUNZIP_BYTES="$bytes")
[ -n "$uclk" ] && args+=(-DEN_UCLK=1 -DUCLK_F="$uclk")
[ -n "$cores" ] && args+=(-DCOMP_CORES="$cores")
cmake "${args[@]}" ${extra[@]+"${extra[@]}"}

run="$build/run.sh"
if [ $sim = 1 ]; then
    cat > "$run" <<EOF
#!/bin/bash
set -eo pipefail
cmake --build "$build" --target sim
EOF
else
    cat > "$run" <<EOF
#!/bin/bash
set -eo pipefail
date
cmake --build "$build" --target project
cmake --build "$build" --target bitgen
vivado -mode batch -nojournal -log "$build/reports_vsz.log" -source "$script_dir/extract_reports.tcl" \\
    -tclargs "$build/checkpoints/config_0/shell_routed_c0.dcp" "$build/reports_vsz"
cp "$build/harness_config.json" "$build/reports_vsz/"
python3 "$script_dir/summarize.py" impl "$build/reports_vsz"
date
EOF
fi
chmod +x "$run"

if [ $detach = 1 ]; then
    session="vsz-$name"
    tmux new-session -d -s "$session" "bash -c '$run > $build/build.log 2>&1'"
    echo "Running in tmux session $session; log: $build/build.log"
else
    "$run" 2>&1 | tee "$build/build.log"
fi
