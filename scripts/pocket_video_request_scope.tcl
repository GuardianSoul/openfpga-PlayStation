# Video->2x request snapshots: Gray pointers are bounded crossings. Data stays
# in the existing dual-clock RAM. Never cut the numeric record or CPU clocks.
set request_parent {*|gpu_videoout_async:igpu_videoout_async|psx_video_request_cdc:video_request|}
set request_channel ${request_parent}psx_video_config_cdc:channel|
proc request_regs {tail n} {
    set request_channel {*|gpu_videoout_async:igpu_videoout_async|psx_video_request_cdc:video_request|psx_video_config_cdc:channel|}
    set r [get_registers -nowarn ${request_channel}${tail}]
    # The exact query can return the sticky flag and named physical copies.
    # Keep every returned node and the existing first-stage cut; only this
    # logical flag may have more than one FF, in one request-channel instance.
    if {$tail eq "src_fault" && $n==1} {
        set instances {}
        foreach_in_collection node $r {
            set name [get_node_info -name $node]
            if {![regexp {^(.*\|gpu_videoout_async:igpu_videoout_async\|psx_video_request_cdc:video_request\|psx_video_config_cdc:channel)\|src_fault(~DUPLICATE[0-9]*)?$} $name ignored instance replica]} {
                error "Unrecognized request source fault register: $name"
            }
            lappend instances $instance
        }
        if {[get_collection_size $r]<1 || [llength [lsort -unique $instances]]!=1} {
            error "Missing/ambiguous request source fault geometry"
        }
        puts "REQUEST_FAULT_PHYSICAL [get_collection_size $r] logical_1"
        return $r
    }
    if {[get_collection_size $r]!=$n} {error "Missing request CDC registers $tail"}
    return $r
}
set request_src_clock [get_clocks {*video_pll*general[0]*OUTPUT_COUNTER*divclk}]
set request_dst_clock [get_clocks {*cpu_pll*general[1]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $request_src_clock]!=1 || [get_collection_size $request_dst_clock]!=1} {error "Ambiguous request clocks"}
foreach_in_collection c $request_src_clock {set request_src_period [get_clock_info -period $c]}
foreach_in_collection c $request_dst_clock {set request_dst_period [get_clock_info -period $c]}
if {$request_src_period<15 || $request_src_period>20 || $request_dst_period<12 || $request_dst_period>16 || $request_src_period<1.2*$request_dst_period} {
    error "Request FIFO clock geometry outside qualified range"
}
proc request_gray {gray binary} {
    set request_channel {*|gpu_videoout_async:igpu_videoout_async|psx_video_request_cdc:video_request|psx_video_config_cdc:channel|}
    set prefix ${request_channel}dual_clock_fifo:snapshots|
    set pattern [format {%s%s[*]} $prefix $gray]
    set r [get_registers -nowarn $pattern]; set bits {}; set instances {}
    foreach_in_collection node $r {
        set name [get_node_info -name $node]
        if {![regexp [format {^(.*)\|%s\[([0-3])\](~DUPLICATE[0-9]*)?$} $gray] $name -> instance bit replica]} {error "Unknown request Gray source $name"}
        lappend bits $bit; lappend instances $instance
    }
    if {[llength [lsort -unique $instances]]!=1} {error "Ambiguous request Gray instance"}
    set bits [lsort -integer -unique $bits]
    if {$bits eq {0 1 2 3}} {return $r}
    if {$bits ne {0 1 2}} {error "Incomplete request Gray source $gray"}
    set alias [format {%s%s[3]} $prefix $binary]
    if {[get_collection_size [get_registers -nowarn $alias]]!=1} {error "Missing request Gray MSB alias"}
    return [get_registers [list $pattern $alias]]
}
foreach {gray binary first second} {wr_addr_gray wr_addr wr_addr_gray_rd wr_addr_gray_rd_r rd_addr_gray rd_addr rd_addr_gray_wr rd_addr_gray_wr_r} {
    set from [request_gray $gray $binary]
    set to [request_regs [format {dual_clock_fifo:snapshots|%s[*]} $first] 4]
    request_regs [format {dual_clock_fifo:snapshots|%s[*]} $second] 4
    set_max_delay -from $from -to $to 10
    set_min_delay -from $from -to $to -10
    set_net_delay -max -from $from -to $to 10
    set_net_delay -min -from $from -to $to 0
    set_max_skew -from $from -to $to 10
    puts "REQUEST_GRAY $gray [get_collection_size $to]"
}
foreach {pipe meta sync} {src_reset_pipe source_online_meta source_online_sync dst_reset_pipe destination_online_meta destination_online_sync} {
    set_false_path -from [request_regs [format {%s[2]} $pipe] 1] -to [request_regs $meta 1]
    request_regs $sync 1
}
set request_reset [get_registers -nowarn {*|gpu_videoout_async:igpu_videoout_async|request_reset}]
if {[get_collection_size $request_reset]!=1} {error "Missing registered request reset"}
foreach pipe {src_reset_pipe dst_reset_pipe} {
    set_false_path -from $request_reset -to [request_regs [format {%s[*]} $pipe] 3]
}
set request_fault_meta [get_registers -nowarn ${request_parent}fault_meta]
set request_fault_sync [get_registers -nowarn ${request_parent}fault_sync]
if {[get_collection_size $request_fault_meta]!=1 || [get_collection_size $request_fault_sync]!=1} {error "Missing request fault FFs"}
set_false_path -from [request_regs src_fault 1] -to $request_fault_meta
# All second stages, reset release, payload-side RAM ports and consumers timed.
