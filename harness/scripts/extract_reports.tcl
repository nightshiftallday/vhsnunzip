# Utilization and timing reports of the vhsnunzip core in an implemented design.
#
# Standalone, on a checkpoint (e.g. a Coyote build):
#   vivado -mode batch -source extract_reports.tcl \
#     -tclargs <build>/checkpoints/config_0/shell_routed_c0.dcp <out_dir>
# or sourced by ooc.tcl, which calls vsz_reports on the open design.
#
# Writes to <out_dir>:
#   core_utilization_hier.rpt  hierarchical utilization of the core (parsed by summarize.py)
#   core_utilization.rpt       flat utilization of the core
#   timing_summary.rpt         timing summary of the whole design
#   core_paths.csv             worst setup path per endpoint into, out of and inside the core
#   core_paths.rpt             detailed report of the 20 worst core paths
#   core_logic_levels.rpt      logic level distribution of the core paths
#   design.csv                 design-level WNS/TNS/WHS

proc vsz_find_core {pattern} {
    set cells [get_cells -quiet -hierarchical -filter "NAME =~ $pattern"]
    if {[llength $cells] != 1} {
        error "expected one cell matching $pattern, found [llength $cells]"
    }
    return $cells
}

# Slack as a number; unconstrained paths sort last
proc vsz_slack {path} {
    set slack [get_property -quiet SLACK $path]
    if {[string is double -strict $slack]} {
        return $slack
    }
    return 1e9
}

proc vsz_csv_field {value} {
    if {[string match {*[,\"]*} $value]} {
        return "\"[string map {\" \"\"} $value]\""
    }
    return $value
}

proc vsz_reports {out_dir {pattern "*inst_vhsnunzip/core_inst"} {max_paths 500}} {
    file mkdir $out_dir
    set core [vsz_find_core $pattern]
    puts "** vhsnunzip core: $core"

    report_utilization -hierarchical -hierarchical_depth 6 -cells $core -file "$out_dir/core_utilization_hier.rpt"
    report_utilization -cells $core -file "$out_dir/core_utilization.rpt"
    report_timing_summary -max_paths 10 -report_unconstrained -file "$out_dir/timing_summary.rpt"

    # Setup paths starting or ending at a sequential cell of the core
    set seq [get_cells -quiet -hierarchical -filter "NAME =~ $core/* && IS_SEQUENTIAL"]
    set paths [get_timing_paths -quiet -setup -from $seq -max_paths $max_paths -nworst 1 -unique_pins]
    foreach p [get_timing_paths -quiet -setup -to $seq -max_paths $max_paths -nworst 1 -unique_pins] {
        lappend paths $p
    }
    set seen [dict create]
    set unique {}
    foreach p $paths {
        set key "[get_property STARTPOINT_PIN $p]>[get_property ENDPOINT_PIN $p]"
        if {![dict exists $seen $key]} {
            dict set seen $key 1
            lappend unique $p
        }
    }
    set unique [lsort -real -index 0 [lmap p $unique {list [vsz_slack $p] $p}]]
    set unique [lmap entry $unique {lindex $entry 1}]

    set f [open "$out_dir/core_paths.csv" w]
    puts $f "slack,requirement,datapath_delay,logic_delay,net_delay,logic_levels,skew,start_clock,end_clock,startpoint,endpoint"
    foreach p $unique {
        set row {}
        foreach prop {SLACK REQUIREMENT DATAPATH_DELAY DATAPATH_LOGIC_DELAY DATAPATH_NET_DELAY LOGIC_LEVELS SKEW STARTPOINT_CLOCK ENDPOINT_CLOCK STARTPOINT_PIN ENDPOINT_PIN} {
            lappend row [vsz_csv_field [get_property -quiet $prop $p]]
        }
        puts $f [join $row ,]
    }
    close $f

    if {[llength $unique]} {
        report_timing -of_objects [lrange $unique 0 19] -file "$out_dir/core_paths.rpt"
        report_design_analysis -logic_level_distribution -of_timing_paths $unique -file "$out_dir/core_logic_levels.rpt"
    }

    set f [open "$out_dir/design.csv" w]
    puts $f "metric,value"
    set wns [get_timing_paths -quiet -setup -max_paths 1]
    set whs [get_timing_paths -quiet -hold -max_paths 1]
    puts $f "design_wns,[expr {[llength $wns] ? [get_property SLACK $wns] : {}}]"
    puts $f "design_whs,[expr {[llength $whs] ? [get_property SLACK $whs] : {}}]"
    puts $f "core_cell,$core"
    puts $f "core_paths,[llength $unique]"
    close $f
}

# Standalone use
if {![info exists vsz_sourced] && [info exists argv] && [llength $argv] >= 2} {
    lassign $argv dcp out_dir pattern
    open_checkpoint $dcp
    if {$pattern eq ""} {
        vsz_reports $out_dir
    } else {
        vsz_reports $out_dir $pattern
    }
}
