# Fixed physical-top geometry: FIFO_AW=4, MAX_WRITE_WORDS=1024.
# Registered Gray increments are one per source edge. Ten ns is below both
# source periods (GPU 2x 14.762 ns, memory 16 ns). Bound flight AND bus skew.
# First-stage sampling has no synchronous hold relationship. Second stages
# remain fully timed; do not false-path the first stage (Lite disables skew).
proc pocket_vram_gray_source {prefix gray binary width} {
    set patterns [list [format {%s%s[*]} $prefix $gray]]
    set nodes [get_registers -nowarn $patterns]
    set bits {}; set instances {}
    set rx [format {^(.*)\|%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $gray]
    foreach_in_collection node $nodes {
        set name [get_node_info -name $node]
        if {![regexp $rx $name ignored instance bit replica]} {error "Unexpected VRAM Gray source $name"}
        lappend bits $bit; lappend instances $instance
    }
    if {[llength [lsort -unique $instances]] != 1} {error "Missing/ambiguous VRAM Gray source $gray"}
    set expected {}; for {set bit 0} {$bit<$width} {incr bit} {lappend expected $bit}
    set bits [lsort -integer -unique $bits]
    if {$bits ne $expected} {
        # Only the MSB can use its binary name: gray(n-1)==binary(n-1).
        if {$bits ne [lrange $expected 0 end-1]} {error "Incomplete VRAM Gray source $gray: $bits"}
        set alias [format {%s%s[%d]} $prefix $binary [expr {$width-1}]]
        set high [get_registers -nowarn $alias]
        if {[get_collection_size $high]<1} {error "Missing VRAM Gray MSB alias $gray"}
        set binary_rx [format {^(.*)\|%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $binary]
        foreach_in_collection node $high {
            set name [get_node_info -name $node]
            if {![regexp $binary_rx $name ignored instance bit replica] ||
                $bit!=$width-1 || $instance ne [lindex $instances 0]} {
                error "Unproven VRAM Gray MSB replica $name"
            }
        }
        lappend patterns $alias
    }
    return [get_registers $patterns]
}

proc pocket_vram_gray_bounds {} {
    # Stop if a later PLL change invalidates this physical flight budget.
    foreach pattern {{*cpu_pll*general[1]*OUTPUT_COUNTER*divclk} {*memory_clocks*general[0]*OUTPUT_COUNTER*divclk}} {
        set clocks [get_clocks $pattern]
        if {[get_collection_size $clocks]!=1} {error "Missing/ambiguous VRAM source clock $pattern"}
        foreach_in_collection clock $clocks {
            if {[get_clock_info -period $clock]<14.0} {error "VRAM source period too short for 10 ns bounds"}
        }
    }
    set scope {*|psx_vram_stream:vram_memory|}
    set checked 0
    foreach {label suffix gray binary meta width} {
        commands-rd {dual_clock_fifo:commands|} rd_addr_gray rd_addr rd_addr_gray_wr 4
        commands-wr {dual_clock_fifo:commands|} wr_addr_gray wr_addr wr_addr_gray_rd 4
        responses-rd {dual_clock_fifo:responses|} rd_addr_gray rd_addr rd_addr_gray_wr 4
        responses-wr {dual_clock_fifo:responses|} wr_addr_gray wr_addr wr_addr_gray_rd 4
        retired {} retired_command_gray retired_commands retired_command_meta 5
        finished {} finished_word_gray finished_words finished_word_meta 11
        consumed {} consumed_gray consumed_pairs consumed_meta 5
    } {
        set prefix "$scope$suffix"
        set to [get_registers -nowarn [format {%s%s[*]} $prefix $meta]]
        if {$label eq "responses-rd" && [get_collection_size $to]==0} {
            # Full/error output can be dead in the top. Require its complete
            # synchronizer cone to be absent; never accept a partly lost bus.
            set cone [get_registers -nowarn [list [format {%sfull_o*} $prefix] [format {%srd_addr_gray_wr*} $prefix]]]
            if {[get_collection_size $cone]!=0} {error "Partial response-full CDC cone"}
            puts "VRAM_GRAY_PRUNED $label full_output_and_sync_cone_absent"
            continue
        }
        if {[get_collection_size $to]!=$width} {error "Missing/ambiguous VRAM first-stage $meta"}
        set from [pocket_vram_gray_source $prefix $gray $binary $width]
        set_max_delay -from $from -to $to 10.0
        set_net_delay -max -from $from -to $to 10.0
        set_max_skew -from $from -to $to 10.0
        # Lite/Standard disables max-skew even for a hold-only false path.
        # Min-delay includes unrelated clock insertion delays; -10 ns removes
        # that artificial hold requirement. Actual wire delay remains >=0,
        # bounded above by 10 ns, with 10 ns skew; no path is false-pathed.
        set_min_delay -from $from -to $to -10.0
        set_net_delay -min -from $from -to $to 0.0
        incr checked [get_collection_size $to]
        puts "VRAM_GRAY_BOUND $label [get_collection_size $from] [get_collection_size $to] 10ns"
    }
    if {$checked!=33 && $checked!=37} {error "Wrong VRAM CDC geometry: $checked"}
}
pocket_vram_gray_bounds

# Existing independent level synchronizers, not numeric payload buses. The
# first stage may metastabilize; only its following stage drives local logic.
# Leaving their asynchronous hold checks active made the router add delay to
# shared reset/ready logic, breaking real same-clock paths on that same net.
foreach pointer {initialized_sync backend_fault_sync source_online_sync} {
    set vram_status_meta [get_registers -nowarn [format {*|psx_vram_stream:vram_memory|%s[0]} $pointer]]
    set vram_status_sync [get_registers -nowarn [format {*|psx_vram_stream:vram_memory|%s[1]} $pointer]]
    if {$pointer eq "backend_fault_sync" && [get_collection_size $vram_status_meta]==0 && [get_collection_size $vram_status_sync]==0} {
        puts "VRAM_STATUS_PRUNED $pointer"
        continue
    }
    if {[get_collection_size $vram_status_meta]!=1 || [get_collection_size $vram_status_sync]!=1} {
        error "Missing/ambiguous VRAM status synchronizer $pointer"
    }
    set_false_path -to $vram_status_meta
    puts "VRAM_STATUS_FIRST_STAGE $pointer"
}
# No Gray path, raw host payload, or status second stage is false-pathed.
