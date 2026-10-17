# Private routed copy; no exceptions are introduced by the audit.
# quartus_sta -t <script> <fresh-output> [--faults]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected output and optional --faults"}
set faults 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--faults"} {error "Unknown audit option"}
    set faults 1
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh report audit output required"}
file mkdir $output
proc reports_audit_initialized {} {
    # This logical scalar can acquire physical replicas. Do not weaken the
    # fixed synchronizer-stage counts in the project's constraint scope.
    set owner {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|gpu:igpu|gpu_videoout:igpu_videoout|gpu_videoout_async:igpu_videoout_async|psx_video_reports_cdc:video_reports}
    set regs [get_registers -nowarn [list [format {%s|dst_initialized*} $owner]]]
    set physical [dict create];set original 0
    foreach_in_collection reg $regs {
        set name [get_node_info -name $reg]
        if {[string first ${owner}| $name]!=0 ||
            ![regexp {^dst_initialized(~DUPLICATE[0-9]*)?$} [string range $name [expr {[string length $owner]+1}] end]]} {
            error "Unknown report initialization copy $name"
        }
        if {$name eq "${owner}|dst_initialized"} {set original 1}
        set cell [get_node_info -cell $reg]
        if {[dict exists $physical $cell]} {error "Aliased report initialization copy"}
        dict set physical $cell 1
    }
    if {!$original || [dict size $physical]<1 || [dict size $physical]>4} {
        error "Missing report initialization geometry"
    }
    return $regs
}
project_open apf_top
try {
create_timing_netlist
read_sdc
update_timing_netlist
if {[llength [info commands reports_regs]]!=1} {error "Missing report scoped constraints"}
set dst [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
set fifo [get_keepers {*|psx_video_reports_cdc:video_reports|dual_clock_fifo:snapshots|*}]
if {[get_collection_size $dst]!=1 || [get_collection_size $fifo]<20} {error "Missing report FIFO geometry"}
set fault_meta [get_registers ${reports_parent}fault_meta]
set fault_sync [get_registers ${reports_parent}fault_sync]
if {[get_collection_size $fault_meta]!=1 || [get_collection_size $fault_sync]!=1} {error "Missing report fault stages"}
# The old asynchronous record samplers must actually disappear. The timer's
# obsolete edge-history register must also prune in the paired strobe build.
set old [get_registers -nowarn [list {*|gpu_videoout_async:igpu_videoout_async|videoout_reports_s1*} {*|gpu_videoout_async:igpu_videoout_async|videoout_reports_s2*} {*|gpu_videoout_async:igpu_videoout_async|allowunpause_s1*} {*|gpu_videoout_async:igpu_videoout_async|allowunpause_s2*} {*|timer:itimer|dotclock_1}]]
if {[get_collection_size $old]!=0} {error "Old report sampler or timer edge detector remains"}
if {[get_collection_size [get_registers -nowarn {*|timer:itimer|*}]]<100} {error "Missing connected timer"}
reports_regs read_valid 1
set initialized [reports_audit_initialized]
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach {gray binary first second} {wr_addr_gray wr_addr wr_addr_gray_rd wr_addr_gray_rd_r rd_addr_gray rd_addr rd_addr_gray_wr rd_addr_gray_wr_r} {
        set from [reports_gray $gray $binary]
        set a [reports_regs [format {dual_clock_fifo:snapshots|%s[*]} $first] 4]
        set b [reports_regs [format {dual_clock_fifo:snapshots|%s[*]} $second] 4]
        foreach kind {setup hold} {
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $from -to $a -npaths 1]]!=0} {error "Report Gray crossing hidden"}
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $a -to $b -npaths 1]]!=0} {error "Report second stage hidden"}
            report_timing -$kind -from $from -to $a -npaths 100 -detail summary -file [file join $output corner${corner}-${gray}-first-${kind}.txt]
            report_timing -$kind -to $b -npaths 100 -detail full_path -file [file join $output corner${corner}-${gray}-second-${kind}.txt]
            foreach_in_collection p [get_timing_paths -$kind -to $b -npaths 100] {
                set found 0;set name [get_node_info -name [get_path_info -from $p]]
                foreach_in_collection r $a {if {[get_node_info -name $r] eq $name} {set found 1}}
                if {!$found} {error "Unexpected report second-stage fanin $name"}
            }
        }
    }
    foreach kind {setup hold} {
        # All launch clocks, including the real video record driving the RAM
        # write port. A positive FIFO->CPU subset alone is insufficient.
        report_timing -$kind -to $fifo -npaths 100 -detail full_path -file [file join $output corner${corner}-fifo-all-input-${kind}.txt]
        report_timing -$kind -from $fifo -to_clock $dst -npaths 100 -detail summary -file [file join $output corner${corner}-fifo-consumers-${kind}.txt]
        if {[get_collection_size [get_timing_paths -false_path -$kind -from $fault_meta -to $fault_sync -npaths 1]]!=0} {error "Report fault second stage hidden"}
        report_timing -$kind -from $fault_meta -to $fault_sync -npaths 20 -detail full_path -file [file join $output corner${corner}-fault-second-${kind}.txt]
        foreach {meta sync} {source_online_meta source_online_sync destination_online_meta destination_online_sync} {
            set a [reports_regs $meta 1];set b [reports_regs $sync 1]
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $a -to $b -npaths 1]]!=0} {error "Report online second stage hidden"}
            report_timing -$kind -from $a -to $b -npaths 20 -detail full_path -file [file join $output corner${corner}-${meta}-second-${kind}.txt]
        }
        report_timing -$kind -to [reports_regs src_fault 1] -npaths 100 -detail full_path -file [file join $output corner${corner}-src_fault-${kind}.txt]
        report_timing -$kind -to $initialized -npaths 100 -detail full_path -file [file join $output corner${corner}-dst_initialized-${kind}.txt]
        set expected {};foreach_in_collection reg $initialized {lappend expected [get_node_info -name $reg]}
        set seen {}
        if {[get_collection_size [get_timing_paths -false_path -$kind -to $initialized -npaths 1]]} {error "Report initialization path was hidden"}
        foreach_in_collection path [get_timing_paths -$kind -to $initialized -npaths 100 -nworst 1] {
            lappend seen [get_node_info -name [get_path_info -to $path]]
        }
        if {[lsort -unique $seen] ne [lsort $expected]} {error "Report initialization timing coverage incomplete"}
    }
    foreach pipe {src_reset_pipe dst_reset_pipe} {
        set p [reports_regs [format {%s[*]} $pipe] 3]
        foreach kind {setup hold} {
            report_timing -$kind -from $p -to $p -npaths 20 -detail full_path -file [file join $output corner${corner}-${pipe}-shift-${kind}.txt]
        }
        foreach kind {recovery removal} {
            report_timing -$kind -from [reports_regs [format {%s[2]} $pipe] 1] -npaths 100 -detail full_path -file [file join $output corner${corner}-${pipe}-${kind}.txt]
        }
    }
    report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four report corners"}
if {$faults} {
    set from [reports_gray wr_addr_gray wr_addr]
    set to [reports_regs {dual_clock_fifo:snapshots|wr_addr_gray_rd[*]} 4]
    set_net_delay -max -from $from -to $to .001
    set_max_skew -from $from -to $to .001
    update_timing_netlist
    report_net_delay -nworst 500 -file [file join $output planted-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output planted-skew.txt]
}
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
