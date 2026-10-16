# Ordered settings FIFO: only Gray pointers cross into synchronizers; payload
# resides in the pinned FIFO's dual-clock RAM under producer/consumer ownership.
proc video_cfg_regs {tail n} {
    set prefix {*|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|}
    set r [get_registers -nowarn "${prefix}${tail}"]
    # Fitter can replicate a pointer sampling bit to drive several consumers.
    # Require all four logical bits in ONE exact FIFO instance, retaining all
    # returned physical copies. A larger count alone is never sufficient.
    if {$n==4 && [regexp {^dual_clock_fifo:snapshots\|(wr_addr_gray_rd|wr_addr_gray_rd_r|rd_addr_gray_wr|rd_addr_gray_wr_r)\[\*\]$} $tail ignored stem]} {
        set bits {};set instances {}
        set expression [format {^(.*\|gpu_videoout_async:igpu_videoout_async\|psx_video_config_cdc:video_config\|dual_clock_fifo:snapshots)\|%s\[([0-3])\](~DUPLICATE[0-9]*)?$} $stem]
        foreach_in_collection node $r {
            set name [get_node_info -name $node]
            if {![regexp $expression $name ignored instance bit replica]} {
                error "Unrecognized video pointer sampling register: $name"
            }
            lappend bits $bit;lappend instances $instance
        }
        if {[lsort -integer -unique $bits] ne {0 1 2 3} ||
            [llength [lsort -unique $instances]]!=1} {
            error "Missing/ambiguous video pointer sampling bits: $tail"
        }
        puts "VIDEO_SAMPLE_PHYSICAL $stem [get_collection_size $r] logical_4"
        return $r
    }
    # Quartus can keep both the primary sticky fault flag and a named replica.
    # Retain this exact query/collection and its existing first-stage cut;
    # validate one logical flag in one instance instead of one physical FF.
    if {$tail eq "src_fault" && $n==1} {
        set instances {}
        foreach_in_collection node $r {
            set name [get_node_info -name $node]
            if {![regexp {^(.*\|gpu_videoout_async:igpu_videoout_async\|psx_video_config_cdc:video_config)\|src_fault(~DUPLICATE[0-9]*)?$} $name ignored instance replica]} {
                error "Unrecognized video source fault register: $name"
            }
            lappend instances $instance
        }
        if {[get_collection_size $r]<1 || [llength [lsort -unique $instances]]!=1} {
            error "Missing/ambiguous video source fault geometry"
        }
        puts "VIDEO_FAULT_PHYSICAL [get_collection_size $r] logical_1"
        return $r
    }
    if {[get_collection_size $r]!=$n} {error "Missing video config registers $tail: [get_collection_size $r], expected$n"}
    return $r
}
set video_cfg_src_clock [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
set video_cfg_dst_clock [get_clocks {*video_pll*general[0]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $video_cfg_src_clock]!=1 || [get_collection_size $video_cfg_dst_clock]!=1} {error "Ambiguous video config clocks"}
foreach_in_collection c $video_cfg_src_clock {set video_cfg_src_period [get_clock_info -period $c]}
foreach_in_collection c $video_cfg_dst_clock {set video_cfg_dst_period [get_clock_info -period $c]}
if {$video_cfg_src_period<25 || $video_cfg_dst_period<15 || $video_cfg_dst_period>20 || $video_cfg_src_period<1.25*$video_cfg_dst_period} {
    error "Video settings snapshot clock geometry outside tested range"
}
proc video_cfg_gray {gray binary} {
    set prefix {*|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|dual_clock_fifo:snapshots|}
    set pattern [format {%s%s[*]} $prefix $gray]
    set r [get_registers -nowarn $pattern];set bits {};set instances {}
    foreach_in_collection node $r {
        set name [get_node_info -name $node]
        if {![regexp [format {^(.*)\|%s\[([0-3])\](~DUPLICATE[0-9]*)?$} $gray] $name ignored instance bit replica]} {error "Unproven video Gray source $name"}
        lappend bits $bit;lappend instances $instance
    }
    if {[llength [lsort -unique $instances]]!=1} {error "Ambiguous video Gray instance"}
    set bits [lsort -integer -unique $bits]
    if {$bits eq {0 1 2 3}} {return $r}
    if {$bits ne {0 1 2}} {error "Incomplete video Gray bus $gray: $bits"}
    set alias [get_registers -nowarn [format {%s%s[3]} $prefix $binary]]
    if {[get_collection_size $alias]!=1} {error "Missing video Gray bit3 alias"}
    return [get_registers [list $pattern [format {%s%s[3]} $prefix $binary]]]
}
foreach {gray binary first second} {wr_addr_gray wr_addr wr_addr_gray_rd wr_addr_gray_rd_r rd_addr_gray rd_addr rd_addr_gray_wr rd_addr_gray_wr_r} {
    set from [video_cfg_gray $gray $binary]
    set to [video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $first] 4]
    video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $second] 4
    set_max_delay -from $from -to $to 10.0
    set_min_delay -from $from -to $to -10.0
    set_net_delay -max -from $from -to $to 10.0
    set_net_delay -min -from $from -to $to 0.0
    set_max_skew -from $from -to $to 10.0
    puts "VIDEO_CONFIG_GRAY $gray [get_collection_size $to]"
}
foreach {pipe meta sync} {src_reset_pipe source_online_meta source_online_sync dst_reset_pipe destination_online_meta destination_online_sync} {
    set from [video_cfg_regs [format {%s[2]} $pipe] 1]
    set to [video_cfg_regs $meta 1];video_cfg_regs $sync 1
    set_false_path -from $from -to $to
}
set video_cfg_scope {*|gpu_videoout_async:igpu_videoout_async|}
set video_cfg_reset [get_registers -nowarn ${video_cfg_scope}video_config_reset]
if {[get_collection_size $video_cfg_reset]!=1} {error "Missing registered video config reset"}
foreach pipe {src_reset_pipe dst_reset_pipe} {
    set to [video_cfg_regs [format {%s[*]} $pipe] 3]
    set_false_path -from $video_cfg_reset -to $to
}
set video_cfg_fault_meta [get_registers -nowarn ${video_cfg_scope}video_config_fault_meta]
set video_cfg_fault_sync [get_registers -nowarn ${video_cfg_scope}video_config_fault_sync]
if {[get_collection_size $video_cfg_fault_meta]!=1 || [get_collection_size $video_cfg_fault_sync]!=1} {error "Missing video fault synchronizer"}
set_false_path -from [video_cfg_regs src_fault 1] -to $video_cfg_fault_meta
foreach {first second third} {reset_s2 reset_s1 reset_sampled} {
    set a [get_registers -nowarn ${video_cfg_scope}${first}]
    set b [get_registers -nowarn ${video_cfg_scope}${second}]
    set c [get_registers -nowarn ${video_cfg_scope}${third}]
    if {[get_collection_size $a]==0 && [get_collection_size $b]==0 && [get_collection_size $c]==0} {
        puts "VIDEO_CONTROL_PRUNED $first";continue
    }
    if {[get_collection_size $a]!=1 || [get_collection_size $b]!=1 || [get_collection_size $c]!=1} {error "Missing video control FF chain $first"}
    set_false_path -to $a
    puts "VIDEO_CONTROL_FIRST $first"
}
# No numeric bus, second stage, local reset release or whole domain is cut.
