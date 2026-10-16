# Private routed copy with current video configuration scope loaded.
# Every physical second-stage copy must have its own ordinary timed path.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh replica audit output"}
set fault [lindex $quartus(args) 1]
if {$fault ni {{} --fault-hidden-replica --fault-relaxed-hold}} {error "Unknown replica audit fault"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh replica audit output required"}
file mkdir $output
project_open apf_top
try {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    if {[llength [info commands video_cfg_regs]]!=1} {error "Missing video configuration scope"}
    set prefix {*|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|dual_clock_fifo:snapshots|}
    set targets {}
    set replica {}
    foreach {first second clock} {
        wr_addr_gray_rd wr_addr_gray_rd_r video_cfg_dst_clock
        rd_addr_gray_wr rd_addr_gray_wr_r video_cfg_src_clock
    } {
        video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $first] 4
        set stages [video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $second] 4]
        foreach_in_collection n $stages {
            set name [get_node_info -name $n]
            if {![regexp [format {^(.*)\|%s\[([0-3])\](~DUPLICATE[0-9]*)?$} $second] $name ignored instance bit suffix]} {error "Unrecognized physical second stage"}
            # The stage collection above includes ALL physical copies. For
            # this individual row suppress automatic alias expansion only;
            # an explicitly named replica is still queried and checked below.
            set to [get_registers -no_duplicates -nowarn [list $name]]
            if {[get_collection_size $to]!=1} {error "Second-stage singleton query changed"}
            foreach_in_collection node $to {if {[get_node_info -name $node] ne $name} {error "Second-stage physical singleton changed"}}
            set legal [get_registers -nowarn [list [format {%s%s[%d]} $prefix $first $bit]]]
            set expected {}
            foreach_in_collection a $legal {lappend expected [get_node_info -name $a]}
            if {![llength $expected]} {error "Missing same-bit first stage"}
            set incoming {}
            # Restrict the structural query to data edges; the default query
            # also returns the clock's root input port as a fanin.
            foreach_in_collection a [get_fanins -synch $to] {lappend incoming [get_node_info -name $a]}
            if {![llength $incoming]} {error "Missing physical second-stage fanin"}
            foreach a $incoming {if {$a ni $expected} {error "Wrong physical second-stage fanin: $name <- $a"}}
            lappend targets [list $name $to $legal $expected [set $clock]]
            if {$suffix ne ""} {set replica $to}
        }
    }
    if {$fault ne "" && $replica eq ""} {error "Fault requires an actual replicated second stage"}
    if {$fault eq "--fault-hidden-replica"} {set_false_path -to $replica}
    if {$fault eq "--fault-relaxed-hold"} {set_multicycle_path -hold 1 -to $replica}
    set table [open [file join $output paths.tsv] w]
    puts $table "corner\tkind\tfrom\tto\tclock\tslack_ns\trelationship_ns\tsetup_end\tsetup_start\thold_end\thold_start"
    set corner 0
    foreach_in_collection condition [get_available_operating_conditions] {
        set_operating_conditions $condition
        update_timing_netlist
        foreach target $targets {
            lassign $target name to legal expected clock
            foreach kind {setup hold} {
                if {[get_collection_size [get_timing_paths -false_path -$kind -to $to -npaths 1]]} {error "Physical video second-stage path hidden: $name"}
                set paths [get_timing_paths -$kind -to $to -npaths 1]
                if {[get_collection_size $paths]!=1} {error "Missing physical video second-stage timing: $name"}
                foreach_in_collection p $paths {
                    set from [get_node_info -name [get_path_info -from $p]]
                    if {$from ni $expected || [get_node_info -name [get_path_info -to $p]] ne $name} {error "Unexpected second-stage timing endpoints"}
                    set clocks {}
                    foreach field {from_clock to_clock} {lappend clocks [get_clock_info -name [get_path_info -$field $p]]}
                    foreach_in_collection c $clock {set clock_name [get_clock_info -name $c];set period [get_clock_info -period $c]}
                    if {$clocks ne [list $clock_name $clock_name]} {error "Second-stage clock changed"}
                    set edge {}
                    foreach field {clock_relationship setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle} {lappend edge [get_path_info -$field $p]}
                    if {[lrange $edge 1 4] ne {1 1 0 0}} {error "Physical video second-stage edge relaxed: $name"}
                    set relationship [expr {$kind eq "setup" ? $period : 0.0}]
                    if {abs([lindex $edge 0]-$relationship)>0.002} {error "Wrong physical video second-stage relationship"}
                    set slack [get_path_info -slack $p]
                    if {$slack<0} {error "Physical video second-stage timing negative: $name/$kind/$slack"}
                    puts $table [join [concat [list $corner $kind $from $name $clock_name $slack] $edge] "\t"]
                }
            }
        }
        incr corner
    }
    close $table
    if {$corner!=4} {error "Expected four replica timing corners"}
    puts "PASS_VIDEO_CONFIG_REPLICAS physical_[llength $targets] logical_8 corners_4 ordinary_setup_hold"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
