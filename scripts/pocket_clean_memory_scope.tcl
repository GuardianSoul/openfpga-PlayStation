# Source-bound psx_memcard_slot + psx_vram_psram CDC integration. Only actual
# asynchronous payload endpoints receive bounds. Local/related-clock paths,
# including all synchronizer settling stages, retain ordinary requirements.
proc clean_mem_exact {names} {
    set names [lsort -unique $names]
    if {![llength $names]} {error "Empty clean memory endpoint collection"}
    set regs [get_registers -no_duplicates -nowarn $names]
    if {[get_collection_size $regs]!=[llength $names]} {error "Missing exact clean memory endpoints"}
    return $regs
}
proc clean_mem_names {regs} {
    set names {};foreach_in_collection r $regs {lappend names [get_node_info -name $r]}
    return [lsort -unique $names]
}
proc clean_mem_regs {owner stem width} {
    if {$width==1} {
        set patterns [list "${owner}|${stem}" "${owner}|${stem}~DUPLICATE*"]
    } else {set patterns [list "${owner}|${stem}*"]}
    set regs [get_registers -nowarn $patterns];set bits {}
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        set prefix "${owner}|"
        if {[string first $prefix $name]!=0} {error "Wrong clean memory owner $name"}
        set suffix [string range $name [string length $prefix] end]
        if {$width==1} {
            if {![regexp [format {^%s(~DUPLICATE[0-9]*)?$} $stem] $suffix]} {error "Unknown clean memory scalar $name"}
            set bit 0
        } else {
            if {![regexp [format {^%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $stem] $suffix -> bit replica]} {error "Unknown clean memory bus $name"}
        }
        lappend bits $bit
    }
    set expected {};for {set i 0} {$i<$width} {incr i} {lappend expected $i}
    if {[lsort -integer -unique $bits] ne $expected} {error "Missing clean memory geometry $owner $stem: $bits"}
    return $regs
}
proc clean_mem_bit {owner stem bit} {
    set names {}
    foreach_in_collection r [clean_mem_regs $owner $stem 2] {
        set name [get_node_info -name $r]
        if {[regexp [format {\|%s\[%d\](~DUPLICATE[0-9]*)?$} $stem $bit] $name]} {lappend names $name}
    }
    return [clean_mem_exact $names]
}
proc clean_mem_class {direction name} {
    set card {core_top:ic|psx_memcard_slot:memcard_slot|}
    set vram {core_top:ic|psx_vram_psram:vram|}
    # The source-bound VRAM implementation has one sequential process and no
    # child instances. Every register below this exact owner is clk2x.
    if {[string first $vram $name]==0} {
        set tail [string range $name [string length $vram] end]
        if {[string first | $tail]>=0} {error "Unknown clean VRAM child $name"}
        if {$direction eq "host_core"} {return cross} else {return local}
    }
    if {[string first $card $name]==0} {
        set tail [string range $name [string length $card] end]
        set host {^(req_tgl_74|ready_tgl_74|ready_74|take_seen_74|req_we_74|req_addr_74\[([0-9]|1[0-4])\]|req_data_74\[([0-9]|[12][0-9]|3[01])\]|bridge_rd_data\[([0-9]|[12][0-9]|3[01])\]|overflow)(~DUPLICATE[0-9]*)?$}
        set core {^(aux_req|busy|checked|fmt_active|fmt_idx\[([0-9]|10)\]|own|ready_seen|req_seen_2x|take_tgl_2x|w0_magic|w0_seen)(~DUPLICATE[0-9]*)?$}
        if {[regexp $host $tail]} {
            if {$direction eq "host_core"} {return local}
            if {[regexp {^bridge_rd_data\[([0-9]|[12][0-9]|3[01])\](~DUPLICATE[0-9]*)?$} $tail]} {return cross}
        }
        if {[regexp $core $tail]} {
            if {$direction eq "host_core"} {return cross} else {return local}
        }
    }
    error "Unknown clean memory payload destination $direction $name"
}
set clean_mem_card {core_top:ic|psx_memcard_slot:memcard_slot}
set clean_mem_vram {core_top:ic|psx_vram_psram:vram}
set clean_mem_core_clock {ic|cpu_pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}
foreach {name period} [list clk_74a 13.468 $clean_mem_core_clock 14.76255] {
    set c [get_clocks -nowarn [list $name]]
    if {[get_collection_size $c]!=1 || abs([get_clock_info -period $c]-$period)>.003} {error "Clean memory clock contract differs $name"}
}
foreach {direction owner stem width} [list \
        host_core $clean_mem_card req_we_74 1 \
        host_core $clean_mem_card req_addr_74 15 \
        host_core $clean_mem_card req_data_74 32 \
        host_core $clean_mem_card ready_tgl_74 1 \
        core_host $clean_mem_vram aux_dout 32] {
    set sources {};set crossed {};set locals {};set pairs {}
    foreach_in_collection r [clean_mem_regs $owner $stem $width] {
        set name [get_node_info -name $r];lappend sources $name;set count 0
        foreach_in_collection out [get_fanouts [clean_mem_exact [list $name]]] {
            set target [get_node_info -name $out]
            if {[get_node_info -type $out] ne "reg"} {error "Non-register clean memory payload destination $target"}
            lappend pairs [list $name $target]
            if {[clean_mem_class $direction $target] eq "cross"} {lappend crossed $target;incr count} else {lappend locals $target}
        }
        if {!$count} {error "Missing clean memory crossing consumer $name"}
    }
    set from [clean_mem_exact $sources];set to [clean_mem_exact $crossed]
    set clean_mem_source($stem) $from;set clean_mem_target($stem) $to
    set clean_mem_local_names($stem) [lsort -unique $locals]
    set clean_mem_pairs($stem) [lsort -unique $pairs]
    # Two-stage toggles plus the consumer edge provide the settling interval.
    # The held payload has no same-edge relationship between unrelated clocks.
    # Bound only real crossing consumers; keep all local paths fully timed.
    set_max_delay -from $from -to $to 13
    set_min_delay -from $from -to $to -13
    set_net_delay -from $from -to $to -max 13
    set_net_delay -from $from -to $to -min 0
    set_max_skew -from $from -to $to 13
    puts "CLEAN_MEMORY_CDC_SCOPE $stem [get_collection_size $from] [get_collection_size $to] local_[llength $clean_mem_local_names($stem)]"
}
set clean_mem_chains [list \
    $clean_mem_card s2x_req [clean_mem_regs $clean_mem_card req_tgl_74 1] \
    $clean_mem_card s2x_ready [clean_mem_regs $clean_mem_card ready_74 1] \
    $clean_mem_card s2x_rack [clean_mem_bit $clean_mem_card s74_rst 1] \
    $clean_mem_card s74_rst [clean_mem_regs $clean_mem_card rst_req_2x 1] \
    $clean_mem_card s74_take [clean_mem_regs $clean_mem_card take_tgl_2x 1] \
    core_top:ic mc_idle74 [clean_mem_regs $clean_mem_card busy 1]]
foreach {owner stem source} $clean_mem_chains {
    set clean_mem_first($stem) [clean_mem_bit $owner $stem 0]
    set clean_mem_second($stem) [clean_mem_bit $owner $stem 1]
    set clean_mem_launch($stem) $source
}
set clean_mem_first(park) [clean_mem_regs $clean_mem_vram park_m 1]
set clean_mem_second(park) [clean_mem_regs $clean_mem_vram park_s 1]
set clean_mem_launch(park) [clean_mem_regs {core_top:ic|core_bridge_cmd:commands} reset_n 1]
foreach stem [lsort [array names clean_mem_first]] {
    set source $clean_mem_launch($stem);set first $clean_mem_first($stem);set second $clean_mem_second($stem)
    foreach {stage previous} [list $first $source $second $first] {
        set allowed [clean_mem_names $previous]
        foreach_in_collection r $stage {
            set one [clean_mem_exact [list [get_node_info -name $r]]]
            set ins [clean_mem_names [get_fanins -synch $one]]
            if {![llength $ins]} {error "Missing clean memory synchronizer input $stem"}
            foreach input $ins {if {$input ni $allowed} {error "Unknown clean memory synchronizer input $stem $input"}}
        }
    }
    set outs [clean_mem_names [get_fanouts $first]];set allowed [clean_mem_names $second]
    if {![llength $outs]} {error "Missing clean memory settling consumer $stem"}
    foreach out $outs {if {$out ni $allowed} {error "Clean memory first-stage fanout escapes $stem $out"}}
    set_false_path -from $source -to $first
    puts "CLEAN_MEMORY_CDC_CHAIN $stem [get_collection_size $source] [get_collection_size $first] [get_collection_size $second]"
}
