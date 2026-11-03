# Out-of-context synthesis and implementation of vhsnunzip_unbuffered for a
# quick utilization and timing estimate (minutes instead of a Coyote build).
#
#   vivado -mode batch -source harness/scripts/ooc.tcl -tclargs <out_dir> [key=value ...]
#
# Keys (defaults in brackets):
#   part   [xcu55c-fsvh2892-2L-e]   target device (Alveo U55C)
#   period [3.333]                  clock period in ns
#   dual   [0]  long [1]  ram [ultra|block]   core generics
#   bytes  []                       override C_BYTES (empty: RTL value)
#   src    [<repo>/src/hdl]         vhsnunzip RTL
#   impl   [1]                      0: stop after synthesis
#   place_directive / route_directive [Default]
#
# Reports go to <out_dir> (see extract_reports.tcl); summarize.py reads them.

set script_dir [file dirname [file normalize [info script]]]
set harness_dir [file dirname $script_dir]
set repo_dir [file dirname $harness_dir]

if {[llength $argv] < 1} {
    error "usage: vivado -mode batch -source ooc.tcl -tclargs <out_dir> \[key=value ...\]"
}
set out_dir [file normalize [lindex $argv 0]]
array set opt [list part xcu55c-fsvh2892-2L-e period 3.333 dual 0 long 1 ram ultra bytes "" \
    src "$repo_dir/src/hdl" impl 1 place_directive Default route_directive Default]
foreach arg [lrange $argv 1 end] {
    set kv [split $arg =]
    if {[llength $kv] != 2 || ![info exists opt([lindex $kv 0])]} {
        error "unknown option $arg"
    }
    set opt([lindex $kv 0]) [lindex $kv 1]
}
set ram_block [expr {$opt(ram) eq "block" ? 1 : 0}]
file mkdir "$out_dir/src"

# -- Sources: src/hdl minus hw/sources.exclude, C_BYTES patched if requested ----
set excludes {}
set f [open "$harness_dir/hw/sources.exclude"]
foreach line [split [read $f] "\n"] {
    set line [string trim $line]
    if {$line ne "" && ![string match "#*" $line]} {
        lappend excludes $line
    }
}
close $f

set c_bytes ""
foreach src [lsort [glob "[file normalize $opt(src)]/*.vhd"]] {
    set name [file tail $src]
    set skip 0
    foreach pattern $excludes {
        if {[string match $pattern $name]} {
            set skip 1
        }
    }
    if {$skip} {
        continue
    }
    if {$name eq "vhsnunzip_utils_pkg.vhd"} {
        set f [open $src]
        set pkg [read $f]
        close $f
        regexp {constant C_BYTES\s*:\s*positive\s*:=\s*([0-9]+)} $pkg -> c_bytes
        if {$opt(bytes) ne "" && $opt(bytes) != $c_bytes} {
            regsub {(constant C_BYTES\s*:\s*positive\s*:=\s*)[0-9]+} $pkg "\\1$opt(bytes)" pkg
            set src "$out_dir/src/$name"
            set f [open $src w]
            puts -nonewline $f $pkg
            close $f
            set c_bytes $opt(bytes)
        }
    }
    read_vhdl $src
}
read_vhdl "$harness_dir/hw/src/hdl/vhsnunzip_harness_core.vhd"
read_vhdl "$harness_dir/ooc/vhsnunzip_harness_ooc.vhd"

set xdc "$out_dir/src/clock.xdc"
set f [open $xdc w]
puts $f "create_clock -period $opt(period) -name clk \[get_ports clk\]"
close $f
read_xdc -mode out_of_context $xdc

# -- Flow -----------------------------------------------------------------------
synth_design -top vhsnunzip_harness_ooc -part $opt(part) -mode out_of_context \
    -generic DUAL_ISSUE=$opt(dual) -generic LONG_CHUNKS=$opt(long) -generic RAM_BLOCK=$ram_block
# Model a global clock buffer so clock skew is realistic
catch {set_property HD.CLK_SRC BUFGCE_X0Y0 [get_ports clk]}
write_checkpoint -force "$out_dir/post_synth.dcp"
report_utilization -hierarchical -hierarchical_depth 6 -file "$out_dir/post_synth_utilization_hier.rpt"

set stage synth
if {$opt(impl)} {
    opt_design
    place_design -directive $opt(place_directive)
    phys_opt_design
    route_design -directive $opt(route_directive)
    write_checkpoint -force "$out_dir/post_route.dcp"
    set stage route
}

set vsz_sourced 1
source "$script_dir/extract_reports.tcl"
vsz_reports $out_dir

set git ""
catch {
    set git [string trim [exec git -C $repo_dir rev-parse --short HEAD]]
    if {[string trim [exec git -C $repo_dir status --porcelain --untracked-files=no -- src]] ne ""} {
        append git "-dirty"
    }
}
set f [open "$out_dir/harness_config.json" w]
puts $f "{\n  \"flow\": \"ooc\",\n  \"stage\": \"$stage\",\n  \"part\": \"$opt(part)\",\n  \"period_ns\": $opt(period),"
puts $f "  \"clk_mhz\": [format %.1f [expr {1000.0 / $opt(period)}]],\n  \"c_bytes\": $c_bytes,"
puts $f "  \"dual_issue\": $opt(dual),\n  \"long_chunks\": $opt(long),\n  \"ram_style\": \"$opt(ram)\","
puts $f "  \"git\": \"$git\"\n}"
close $f
