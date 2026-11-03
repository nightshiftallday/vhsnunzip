#!/bin/bash
# Out-of-context implementation of the core (see ooc.tcl for the keys).
#
#   ooc.sh <out_dir> [key=value ...]          e.g. ooc.sh ooc/dual-300 dual=1 period=3.333
#   ooc.sh --sweep <out_dir> <MHz>... [key=value ...]
#                                             one run per frequency in <out_dir>/<MHz>MHz
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"

run() {
    local out="$1"; shift
    mkdir -p "$out"
    vivado -mode batch -nojournal -log "$out/vivado.log" -source "$script_dir/ooc.tcl" -tclargs "$out" "$@"
    python3 "$script_dir/summarize.py" impl "$out"
}

if [ "${1:-}" = "--sweep" ]; then
    shift
    out="$1"; shift
    freqs=()
    while [ $# -gt 0 ] && [[ "$1" =~ ^[0-9.]+$ ]]; do freqs+=("$1"); shift; done
    for mhz in "${freqs[@]}"; do
        run "$out/${mhz}MHz" period="$(python3 -c "print(round(1000/$mhz, 3))")" "$@"
    done
    python3 "$script_dir/summarize.py" impl "$out"/*MHz
else
    [ $# -ge 1 ] || { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
    run "$@"
fi
