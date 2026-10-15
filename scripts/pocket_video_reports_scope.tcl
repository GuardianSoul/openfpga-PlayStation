# Ordered video report/tick packets: Gray pointers are bounded crossings. Data stays
# in the existing dual-clock RAM. Never cut the numeric record or CPU clocks.
set reports_parent {*|gpu_videoout_async:igpu_videoout_async|psx_video_reports_cdc:video_reports|}
set reports_channel $reports_parent
proc reports_regs {tail n} {
    set reports_channel {*|gpu_videoout_async:igpu_videoout_async|psx_video_reports_cdc:video_reports|}
    set r [get_registers -nowarn ${reports_channel}${tail}]
    if {[get_collection_size $r]!=$n} {error "Missing report CDC registers $tail"}
    return $r
}
set reports_src_clock [get_clocks {*video_pll*general[0]*OUTPUT_COUNTER*divclk}]
set reports_dst_clock [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $reports_src_clock]!=1 || [get_collection_size $reports_dst_clock]!=1} {error "Ambiguous report clocks"}
foreach_in_collection c $reports_src_clock {set reports_src_period [get_clock_info -period $c]}
foreach_in_collection c $reports_dst_clock {set reports_dst_period [get_clock_info -period $c]}
# Source filtering requires >=4 edges per dot and >=3 per other change.
# This clock range leaves service headroom even if those streams never coincide.
# Finite bursts and real scanout event rates are separately qualified.
if {$reports_src_period<18.5 || $reports_src_period>19.0 || $reports_dst_period<29.0 || $reports_dst_period>30.0} {
    error "Report FIFO clock geometry outside qualified source-rate range"
}

proc reports_gray {gray binary} {
    set reports_channel {*|gpu_videoout_async:igpu_videoout_async|psx_video_reports_cdc:video_reports|}
    set prefix ${reports_channel}dual_clock_fifo:snapshots|
    set pattern [format {%s%s[*]} $prefix $gray]
    set r [get_registers -nowarn $pattern]; set bits {}; set instances {}
    foreach_in_collection node $r {
        set name [get_node_info -name $node]
        if {![regexp [format {^(.*)\|%s\[([0-3])\](~DUPLICATE[0-9]*)?$} $gray] $name -> instance bit replica]} {error "Unknown report Gray source $name"}
        lappend bits $bit; lappend instances $instance
    }
    if {[llength [lsort -unique $instances]]!=1} {error "Ambiguous report Gray instance"}
    set bits [lsort -integer -unique $bits]
    if {$bits eq {0 1 2 3}} {return $r}
    if {$bits ne {0 1 2}} {error "Incomplete report Gray source $gray"}
    set alias [format {%s%s[3]} $prefix $binary]
    if {[get_collection_size [get_registers -nowarn $alias]]!=1} {error "Missing report Gray MSB alias"}
    return [get_registers [list $pattern $alias]]
}
foreach {gray binary first second} {wr_addr_gray wr_addr wr_addr_gray_rd wr_addr_gray_rd_r rd_addr_gray rd_addr rd_addr_gray_wr rd_addr_gray_wr_r} {
    set from [reports_gray $gray $binary]
    set to [reports_regs [format {dual_clock_fifo:snapshots|%s[*]} $first] 4]
    reports_regs [format {dual_clock_fifo:snapshots|%s[*]} $second] 4
    set_max_delay -from $from -to $to 10
    set_min_delay -from $from -to $to -10
    set_net_delay -max -from $from -to $to 10
    set_net_delay -min -from $from -to $to 0
    set_max_skew -from $from -to $to 10
    puts "REPORTS_GRAY $gray [get_collection_size $to]"
}
foreach {pipe meta sync} {src_reset_pipe source_online_meta source_online_sync dst_reset_pipe destination_online_meta destination_online_sync} {
    set_false_path -from [reports_regs [format {%s[2]} $pipe] 1] -to [reports_regs $meta 1]
    reports_regs $sync 1
}
set reports_reset [get_registers -nowarn {*|gpu_videoout_async:igpu_videoout_async|request_reset}]
if {[get_collection_size $reports_reset]!=1} {error "Missing registered report reset"}
foreach pipe {src_reset_pipe dst_reset_pipe} {
    set_false_path -from $reports_reset -to [reports_regs [format {%s[*]} $pipe] 3]
}
set reports_fault_meta [get_registers -nowarn ${reports_parent}fault_meta]
set reports_fault_sync [get_registers -nowarn ${reports_parent}fault_sync]
if {[get_collection_size $reports_fault_meta]!=1 || [get_collection_size $reports_fault_sync]!=1} {error "Missing report fault FFs"}
set_false_path -from [reports_regs src_fault 1] -to $reports_fault_meta
# All second stages, reset release, payload-side RAM ports and consumers timed.
