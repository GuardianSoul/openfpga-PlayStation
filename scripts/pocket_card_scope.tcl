# Source-bound Native formatter CDC. No source-only bus bounds, clock-level
# exceptions, local-stage cuts or related-clock hold relaxation.
proc card_cdc_exact {names} {
    set names [lsort -unique $names]
    if {![llength $names]} {error "Empty card endpoint collection"}
    set regs [get_registers -no_duplicates -nowarn $names]
    if {[get_collection_size $regs]!=[llength $names]} {error "Missing exact card endpoints"}
    return $regs
}
proc card_cdc_names {regs} {
    set names {};foreach_in_collection r $regs {lappend names [get_node_info -name $r]}
    return [lsort -unique $names]
}
proc card_cdc_regs {owner stem width} {
    if {$width==1} {
        set patterns [list "${owner}|${stem}" "${owner}|${stem}~DUPLICATE*"]
    } else {
        set patterns [list "${owner}|${stem}*"]
    }
    set regs [get_registers -nowarn $patterns]
    set bits {}
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {[string range $name 0 [expr {[string length $owner]-1}]] ne $owner} {error "Wrong card owner $name"}
        set suffix [string range $name [expr {[string length $owner]+1}] end]
        if {$width==1} {
            if {![regexp [format {^%s(~DUPLICATE[0-9]*)?$} $stem] $suffix]} {error "Unknown card scalar $name"}
            set bit 0
        } else {
            if {![regexp [format {^%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $stem] $suffix -> bit replica]} {error "Unknown card bus $name"}
        }
        lappend bits $bit
    }
    set expected {};for {set i 0} {$i<$width} {incr i} {lappend expected $i}
    if {[lsort -integer -unique $bits] ne $expected} {error "Missing card geometry $owner $stem: $bits"}
    return $regs
}
proc card_cdc_bit {owner stem width bit} {
    set names {}
    foreach_in_collection r [card_cdc_regs $owner $stem $width] {
        set name [get_node_info -name $r]
        if {[regexp [format {\|%s\[%d\](~DUPLICATE[0-9]*)?$} $stem $bit] $name]} {lappend names $name}
    }
    return [card_cdc_exact $names]
}
proc card_cdc_class {direction name} {
    set owner {core_top:ic|pocket_memcard_format:memcard_slot|}
    if {[string first $owner $name]==0} {
        set suffix [string range $name [string length $owner] end]
        if {$direction eq "host_core"} {
            if {[regexp {^rd_word\[([0-9]|1[0-4])\](~DUPLICATE[0-9]*)?$} $suffix]} {return local}
            if {[regexp {^(pf_word\[([0-9]|1[0-4])\]|rd_data\[([0-9]|[12][0-9]|3[01])\]|pf_valid|rd_ack|aux_req|aux_we|state\.state_bit_[0-7])(~DUPLICATE[0-9]*)?$} $suffix]} {return cross}
        } elseif {$direction eq "core_host"} {
            if {[regexp {^rd_data\[([0-9]|[12][0-9]|3[01])\](~DUPLICATE[0-9]*)?$} $suffix]} {return local}
            if {[regexp {^bridge_rd_data\[([0-9]|[12][0-9]|3[01])\](~DUPLICATE[0-9]*)?$} $suffix]} {return cross}
        }
    }
    set vram {core_top:ic|vram_psram:vram|}
    if {$direction eq "host_core" && [string first $vram $name]==0} {
        set suffix [string range $name [string length $vram] end]
        if {[regexp {^o_dq[01]\[([0-9]|1[0-5])\](~DUPLICATE[0-9]*)?$} $suffix]} {return cross}
    }
    error "Unknown card payload destination $direction $name"
}

set card_cdc_owner {core_top:ic|pocket_memcard_format:memcard_slot}
set card_cdc_core_clock {ic|cpu_pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}
foreach {name expected} [list clk_74a 13.468 $card_cdc_core_clock 14.76255] {
    set c [get_clocks -nowarn [list $name]]
    if {[get_collection_size $c]!=1 || abs([get_clock_info -period $c]-$expected)>.003} {error "Card clock contract differs $name"}
}
foreach {direction stem width} {host_core rd_word 15 core_host rd_data 32} {
    set sources {};set crossed {};set locals {};set pairs {}
    foreach_in_collection r [card_cdc_regs $card_cdc_owner $stem $width] {
        set name [get_node_info -name $r];lappend sources $name
        set outputs [get_fanouts [card_cdc_exact [list $name]]]
        set cross_count 0
        foreach_in_collection out $outputs {
            set target [get_node_info -name $out]
            if {[get_node_info -type $out] ne "reg"} {error "Non-register card payload destination $target"}
            lappend pairs [list $name $target]
            if {[card_cdc_class $direction $target] eq "cross"} {lappend crossed $target;incr cross_count} else {lappend locals $target}
        }
        if {!$cross_count} {error "Missing card crossing consumer $name"}
    }
    set card_cdc_source($direction) [card_cdc_exact $sources]
    set card_cdc_target($direction) [card_cdc_exact $crossed]
    set card_cdc_local_names($direction) [lsort -unique $locals]
    set card_cdc_pairs($direction) [lsort -unique $pairs]
    set from $card_cdc_source($direction);set to $card_cdc_target($direction)
    # Keep the previous numeric max13/min0 requirement, but ONLY at the
    # actual crossing endpoints. Local consumers regain ordinary clocks.
    set_max_delay -from $from -to $to 13
    set_min_delay -from $from -to $to 0
    set_net_delay -from $from -to $to -max 13
    set_net_delay -from $from -to $to -min 0
    set_max_skew -from $from -to $to 13
    puts "CARD_CDC_SCOPE $direction [get_collection_size $from] [get_collection_size $to] local_[llength $card_cdc_local_names($direction)]"
}
if {![info exists card_cdc_boot_disc_gate]} {set card_cdc_boot_disc_gate 0}
if {$card_cdc_boot_disc_gate ni {0 1}} {error "Unknown card load-done launch profile"}
if {$card_cdc_boot_disc_gate} {
    set card_load_owner core_top:ic;set card_load_stem boot_disc_gate
} else {
    set card_load_owner {core_top:ic|psx_bios_patch:bios_patch};set card_load_stem bios_ready
}
set card_cdc_chains [list \
    $card_cdc_owner rd_tog_s 3 $card_cdc_owner rd_tog host_core \
    $card_cdc_owner rd_ack_s 3 $card_cdc_owner rd_ack core_host \
    $card_cdc_owner done_s 3 $card_load_owner $card_load_stem host_core \
    core_top:ic mc_idle74 2 $card_cdc_owner busy core_host]
foreach {owner stem width source_owner source_stem direction} $card_cdc_chains {
    set src [card_cdc_regs $source_owner $source_stem 1]
    for {set bit 0} {$bit<$width} {incr bit} {
        set stage [card_cdc_bit $owner $stem $width $bit]
        if {$bit==0} {set allowed [card_cdc_names $src]} else {set allowed [card_cdc_names [card_cdc_bit $owner $stem $width [expr {$bit-1}]]]}
        foreach_in_collection r $stage {
            set one [card_cdc_exact [list [get_node_info -name $r]]]
            set inputs [card_cdc_names [get_fanins -synch $one]]
            if {![llength $inputs]} {error "Missing card synchronizer input $stem $bit"}
            foreach input $inputs {if {$input ni $allowed} {error "Unknown card synchronizer input $stem $bit $input"}}
        }
        if {$bit==0} {
            set allowed [card_cdc_names [card_cdc_bit $owner $stem $width 1]]
            set outputs [card_cdc_names [get_fanouts $stage]]
            if {![llength $outputs]} {error "Missing card settling consumer $stem"}
            foreach out $outputs {if {$out ni $allowed} {error "Card first-stage fanout escapes $stem $out"}}
        }
    }
    set first [card_cdc_bit $owner $stem $width 0]
    set_false_path -from $src -to $first
    puts "CARD_CDC_CHAIN $stem $width [get_collection_size $src] [get_collection_size $first] $direction"
}
