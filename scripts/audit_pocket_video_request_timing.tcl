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
if {[file exists $output]} {error "Fresh request audit output required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
if {[llength [info commands request_regs]]!=1} {error "Missing request scoped constraints"}
set dst [get_clocks {*cpu_pll*general[1]*OUTPUT_COUNTER*divclk}]
set fifo [get_keepers {*|psx_video_request_cdc:video_request|psx_video_config_cdc:channel|dual_clock_fifo:snapshots|*}]
if {[get_collection_size $dst]!=1 || [get_collection_size $fifo]<20} {error "Missing request FIFO geometry"}
set fault_meta [get_registers ${request_parent}fault_meta]
set fault_sync [get_registers ${request_parent}fault_sync]
if {[get_collection_size $fault_meta]!=1 || [get_collection_size $fault_sync]!=1} {error "Missing request fault stages"}
proc request_initialized_regs {} {
    global request_channel
    # This exact pre-existing query can return a named physical copy. Keep
    # every returned target; one logical state/instance must still be present.
    set r [get_registers -nowarn ${request_channel}dst_initialized]
    set instances {}
    foreach_in_collection node $r {
        set name [get_node_info -name $node]
        if {![regexp {^(.*)\|dst_initialized(~DUPLICATE[0-9]*)?$} $name -> instance replica]} {
            error "Unrecognized request initialized register $name"
        }
        lappend instances $instance
    }
    if {[get_collection_size $r]==0 || [llength [lsort -unique $instances]]!=1} {
        error "Missing/ambiguous request initialized geometry"
    }
    return $r
}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach {gray binary first second} {wr_addr_gray wr_addr wr_addr_gray_rd wr_addr_gray_rd_r rd_addr_gray rd_addr rd_addr_gray_wr rd_addr_gray_wr_r} {
        set from [request_gray $gray $binary]
        set a [request_regs [format {dual_clock_fifo:snapshots|%s[*]} $first] 4]
        set b [request_regs [format {dual_clock_fifo:snapshots|%s[*]} $second] 4]
        foreach kind {setup hold} {
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $from -to $a -npaths 1]]!=0} {error "Request Gray crossing hidden"}
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $a -to $b -npaths 1]]!=0} {error "Request second stage hidden"}
            report_timing -$kind -from $from -to $a -npaths 100 -detail summary -file [file join $output corner${corner}-${gray}-first-${kind}.txt]
            report_timing -$kind -to $b -npaths 100 -detail full_path -file [file join $output corner${corner}-${gray}-second-${kind}.txt]
            foreach_in_collection p [get_timing_paths -$kind -to $b -npaths 100] {
                set found 0;set name [get_node_info -name [get_path_info -from $p]]
                foreach_in_collection r $a {if {[get_node_info -name $r] eq $name} {set found 1}}
                if {!$found} {error "Unexpected request second-stage fanin $name"}
            }
        }
    }
    foreach kind {setup hold} {
        # All launch clocks, including the real video record driving the RAM
        # write port. A positive FIFO->CPU subset alone is insufficient.
        report_timing -$kind -to $fifo -npaths 100 -detail full_path -file [file join $output corner${corner}-fifo-all-input-${kind}.txt]
        report_timing -$kind -from $fifo -to_clock $dst -npaths 100 -detail summary -file [file join $output corner${corner}-fifo-consumers-${kind}.txt]
        if {[get_collection_size [get_timing_paths -false_path -$kind -from $fault_meta -to $fault_sync -npaths 1]]!=0} {error "Request fault second stage hidden"}
        report_timing -$kind -from $fault_meta -to $fault_sync -npaths 20 -detail full_path -file [file join $output corner${corner}-fault-second-${kind}.txt]
        foreach {meta sync} {source_online_meta source_online_sync destination_online_meta destination_online_sync} {
            set a [request_regs $meta 1];set b [request_regs $sync 1]
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $a -to $b -npaths 1]]!=0} {error "Request online second stage hidden"}
            report_timing -$kind -from $a -to $b -npaths 20 -detail full_path -file [file join $output corner${corner}-${meta}-second-${kind}.txt]
        }
        foreach name {src_fault dst_initialized} {
            if {$name eq "dst_initialized"} {set state [request_initialized_regs]} else {set state [request_regs $name 1]}
            report_timing -$kind -to $state -npaths 100 -detail full_path -file [file join $output corner${corner}-${name}-${kind}.txt]
        }
    }
    foreach pipe {src_reset_pipe dst_reset_pipe} {
        set p [request_regs [format {%s[*]} $pipe] 3]
        foreach kind {setup hold} {
            report_timing -$kind -from $p -to $p -npaths 20 -detail full_path -file [file join $output corner${corner}-${pipe}-shift-${kind}.txt]
        }
        foreach kind {recovery removal} {
            report_timing -$kind -from [request_regs [format {%s[2]} $pipe] 1] -npaths 100 -detail full_path -file [file join $output corner${corner}-${pipe}-${kind}.txt]
        }
    }
    report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four request corners"}
if {$faults} {
    set from [request_gray wr_addr_gray wr_addr]
    set to [request_regs {dual_clock_fifo:snapshots|wr_addr_gray_rd[*]} 4]
    set_net_delay -max -from $from -to $to .001
    set_max_skew -from $from -to $to .001
    update_timing_netlist
    report_net_delay -nworst 500 -file [file join $output planted-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output planted-skew.txt]
}
delete_timing_netlist
project_close -dont_export_assignments
