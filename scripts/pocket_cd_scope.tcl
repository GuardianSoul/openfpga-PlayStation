# Source-bound live disc-swap CDC. Requires the before-map preparer and
# all-corner physical flight/settling/local-clock verification after routing.
proc cd_cdc_regs {stem width {equivalent_bits {}} {optional_bits {}}} {
    set owner {core_top:ic|psx_cd_stream:disc_stream}
    set regs [get_registers -nowarn [list "${owner}|${stem}*"]]
    set bits {}
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {$width==1 && $stem in {m_valid m_tb req_t done_t sel_t mount_done_t resp_t mount_t sel_ack_t pending_file}} {
            # Owner contains literal Tcl/VHDL hierarchy separators.
            set suffix [string range $name [expr {[string length $owner]+1}] end]
            if {![regexp [format {^%s(~DUPLICATE[0-9]*)?$} $stem] $suffix]} {error "Unknown CD scalar $name"}
            set bit 0
        } else {
            set suffix [string range $name [expr {[string length $owner]+1}] end]
            if {![regexp [format {^%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $stem] $suffix -> bit replica]} {error "Unknown CD bus $name"}
        }
        if {![string equal [string range $name 0 [expr {[string length $owner]-1}]] $owner]} {error "Unknown CD owner $name"}
        lappend bits $bit
    }
    set expected {};for {set i 0} {$i<$width} {incr i} {lappend expected $i}
    if {![llength $equivalent_bits]} {
        if {[lsort -integer -unique $bits] ne $expected} {error "Missing CD geometry $stem: $bits"}
    } else {
        # Slot101/201/301/401: bits2/5 are equal, bit7 their inverse;
        # bits6/8 are inverses. Synthesis may retain different representatives
        # of these classes. Every actual FF/copy is included. Bit0 can remain
        # as the constant-one mount predicate seen in the fixed-slot profile.
        set allowed [concat {0} $optional_bits]
        foreach group $equivalent_bits {lappend allowed {*}$group}
        foreach bit $bits {if {$bit ni $allowed} {error "Unknown CD menu slot bit $stem $bit"}}
        foreach group $equivalent_bits {
            set found 0;foreach bit $bits {if {$bit in $group} {set found 1}}
            if {!$found} {error "Missing CD menu slot class $stem $group"}
        }
    }
    return $regs
}
proc cd_cdc_exact {names} {
    set names [lsort -unique $names]
    if {![llength $names]} {error "Empty CD payload collection"}
    set regs [get_registers -no_duplicates -nowarn $names]
    if {[get_collection_size $regs]!=[llength $names]} {error "Missing exact CD physical endpoints"}
    return $regs
}
proc cd_cdc_cross_target {direction name} {
    global cd_cdc_disc_menu
    set stream {core_top:ic|psx_cd_stream:disc_stream|}
    set cd {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|cd_top:icd_top|}
    if {[string first $stream $name]==0} {
        set suffix [string range $name [string length $stream] end]
        if {$direction eq "core_host"} {
            if {[regexp {^dem_lba\[([0-9]|1[0-9])\](~DUPLICATE[0-9]*)?$} $suffix]} {return 1}
            return [expr {[info exists cd_cdc_disc_menu] && $cd_cdc_disc_menu &&
                [regexp {^(next_slot\[[0-8]\]|have_size)(~DUPLICATE[0-9]*)?$} $suffix]}]
        }
        if {[info exists cd_cdc_disc_menu] && $cd_cdc_disc_menu &&
                [regexp {^discs_present\[[0-3]\](~DUPLICATE[0-9]*)?$} $suffix]} {return 1}
        if {[regexp {^(has_disc|disc_settled|mount_done_t|disc_change_done|cstate\.state_bit_([0-9]|10)|kind\[[01]\]|rd_addr\[([0-9]|10)\]|lc_b\[([0-9]|1[01])\]|lc_key\[([0-9]|1[0-5])\]|lc_w0\[([0-9]|[12][0-9]|3[01])\])(~DUPLICATE[0-9]*)?$} $suffix]} {return 1}
        if {[regexp {^altsyncram:sectors_rtl_0\|altsyncram_[^|]+:auto_generated\|ram_block[0-9a-z]+~portb_address_reg[0-9]+$} $suffix]} {return 1}
        return 0
    }
    if {$direction eq "host_core" && [string first $cd $name]==0} {
        set suffix [string range $name [string length $cd] end]
        if {![regexp {^(isAudioCD|resetFromCD|region_out\[[01]\]|trackcountBCD\[[0-7]\]|total(Minutes|Seconds)BCD\[[0-7]\]|libcryptKey\[([0-9]|1[0-5])\]|trackInfo_DataA\[([0-9]|[1-4][0-9]|5[0-4])\])(~DUPLICATE[0-9]*)?$} $suffix]} {error "Unknown CD-controller payload destination $name"}
        return 1
    }
    if {[string first $stream $name]!=0} {error "Unknown CD payload fanout owner $name"}
    return 0
}
proc cd_cdc_bit {stem width bit} {
    set names {}
    foreach_in_collection r [cd_cdc_regs $stem $width] {
        set name [get_node_info -name $r]
        if {[regexp [format {\|%s\[%d\](~DUPLICATE[0-9]*)?$} $stem $bit] $name]} {lappend names $name}
    }
    return [cd_cdc_exact $names]
}
proc cd_cdc_names {collection} {
    set names {};foreach_in_collection r $collection {lappend names [get_node_info -name $r]}
    return [lsort -unique $names]
}
proc cd_cdc_slot_fields {} {
    if {[get_collection_size [get_registers -nowarn {*|psx_cd_stream:disc_stream|sel_slot*}]]!=0} {error "CD constant slot did not specialize sel_slot"}
    if {[get_collection_size [get_registers -nowarn {*|psx_cd_stream:disc_stream|m_slot*}]]==0} {return {}}
    # With constant slot101, the new stream's mapper retains bit0 as a
    # mount-completion predicate. It is still a held host payload, not a
    # reason to cut a path. Accept only the observed bit0 and its replicas.
    cd_cdc_regs m_slot 1
    return {m_slot 1}
}
proc cd_cdc_inactive_presence {} {
    set owner {core_top:ic|psx_cd_stream:disc_stream}
    foreach stem {m_present discs_present} {
        if {[get_collection_size [get_registers -nowarn [list [format {%s|%s*} $owner $stem]]]]!=0} {
            error "Disabled DISC_STEP field did not specialize: $stem"
        }
    }
}
proc cd_cdc_menu_slot_regs {stem} {
    if {$stem ni {m_slot sel_slot}} {error "Unknown CD menu slot bank"}
    return [cd_cdc_regs $stem 16 {{2 5 7} {3} {4} {6 8}}]
}
proc cd_cdc_one_file_count_regs {} {
    # MAX_TRACKS=1: mtrack stays zero; m_ntracks is assigned only 0 or 1.
    # Its declared two-bit bank can retain a constant upper FF. Require the
    # live bit and time every actual copy, admitting no bit outside [1:0].
    set regs [cd_cdc_regs m_ntracks 2 {{0}} {1}]
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {![regexp {\|m_ntracks\[[01]\](~DUPLICATE[0-9]*)?$} $name]} {
            error "Unknown one-file track-count bit $name"
        }
    }
    return $regs
}
proc cd_menu_regs {stem bit} {
    set root {core_top:ic|psx_disc_menu:disc_menu}
    if {$stem eq "disc_setting"} {set root core_top:ic}
    set candidates [get_registers -nowarn [list [format {%s|%s*} $root $stem]]]
    set names {};set seen {}
    foreach_in_collection r $candidates {
        set name [get_node_info -name $r]
        set suffix [string range $name [expr {[string length $root]+1}] end]
        if {[string range $name 0 [expr {[string length $root]-1}]] ne $root ||
                ![regexp [format {^%s\[([01])\](~DUPLICATE[0-9]*)?$} $stem] $suffix -> lane replica]} {
            error "Unknown disc menu synchronizer register $name"
        }
        lappend seen $lane
        if {$lane==$bit} {lappend names $name}
    }
    if {[lsort -unique $seen] ne {0 1}} {error "Missing disc menu synchronizer bank $stem"}
    return [cd_cdc_exact $names]
}
proc cd_menu_domain_reset {} {
    set owner {core_top:ic|psx_mem_cdc:loader_cdc}
    set names {};set bits {}
    foreach_in_collection r [get_registers -nowarn [list "${owner}|dst_reset_pipe*"]] {
        set name [get_node_info -name $r]
        set suffix [string range $name [expr {[string length $owner]+1}] end]
        if {[string range $name 0 [expr {[string length $owner]-1}]] ne $owner ||
                ![regexp {^dst_reset_pipe\[([0-2])\](~DUPLICATE[0-9]*)?$} $suffix -> bit replica]} {
            error "Unknown disc menu domain reset $name"
        }
        lappend bits $bit
        if {$bit==2} {lappend names $name}
    }
    if {[lsort -unique $bits] ne {0 1 2}} {error "Missing disc menu local reset pipeline"}
    return $names
}
proc cd_menu_check_inputs {actual allowed reset} {
    set data 0
    foreach name $actual {
        if {$name in $allowed} {set data 1} elseif {$name ni $reset} {
            error "Unknown disc menu synchronizer input $name"
        }
    }
    if {!$data} {error "Missing disc menu synchronizer data input"}
}
if {![info exists cd_cdc_libcrypt] || $cd_cdc_libcrypt ni {0 1}} {error "Missing CD LibCrypt specialization"}
if {![info exists cd_cdc_disc_menu]} {set cd_cdc_disc_menu 0}
if {$cd_cdc_disc_menu ni {0 1}} {error "Missing CD menu specialization"}
if {![info exists cd_cdc_table_offset]} {set cd_cdc_table_offset 0}
if {$cd_cdc_table_offset ni {0 1} || ($cd_cdc_table_offset && $cd_cdc_libcrypt)} {
    error "Unsupported CD table/LibCrypt specialization"
}
if {!$cd_cdc_disc_menu} {cd_cdc_inactive_presence}
set cd_cdc_core_clock {ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}
foreach {name expected} [list clk_74a 13.468 $cd_cdc_core_clock 29.526] {
    set c [get_clocks -nowarn [list $name]]
    if {[get_collection_size $c]!=1 || abs([get_clock_info -period $c]-$expected)>.002} {error "CD clock contract differs $name"}
}
# The source-bound top drives DISC_SLOT=DEFAULT_SLOT=101. A surviving mount
# predicate is constrained with the other payloads; every local path stays timed.
if {$cd_cdc_disc_menu} {
    set cd_cdc_slot_payload {m_slot 16 m_present 4}
    cd_cdc_menu_slot_regs m_slot
    cd_cdc_menu_slot_regs sel_slot
    cd_cdc_regs discs_present 4
} else {set cd_cdc_slot_payload [cd_cdc_slot_fields]}
set fields {m_valid 1 m_end 19 m_minutes 8 m_seconds 8 resp_buf 1 resp_kind 2}
if {$cd_cdc_table_offset} {
    lappend fields m_ntracks 2 m_tb 1
} else {
    lappend fields m_ntracks 6
    if {[get_collection_size [get_registers -nowarn {*|psx_cd_stream:disc_stream|m_tb*}]]!=0} {
        error "Disabled TABLE_OFFSET payload did not specialize"
    }
}
lappend fields {*}$cd_cdc_slot_payload
if {$cd_cdc_libcrypt} {
    lappend fields m_lc_bytes 12
} elseif {[get_collection_size [get_registers -nowarn {*|disc_stream|m_lc_bytes*}]]!=0} {
    error "Disabled LibCrypt payload did not specialize"
}
set core_fields {req_lba 20}
if {$cd_cdc_disc_menu} {lappend core_fields sel_slot 16}
foreach {direction fields} [list host_core $fields core_host $core_fields] {
    set sources {};set cross {};set locals {};set pairs {}
    foreach {stem width} $fields {
        if {$cd_cdc_disc_menu && $stem in {m_slot sel_slot}} {
            set registers [cd_cdc_menu_slot_regs $stem]
        } elseif {$cd_cdc_table_offset && $stem eq "m_ntracks"} {
            set registers [cd_cdc_one_file_count_regs]
        } else {set registers [cd_cdc_regs $stem $width]}
        foreach_in_collection r $registers {
            set name [get_node_info -name $r];lappend sources $name
            foreach_in_collection out [get_fanouts [cd_cdc_exact [list $name]]] {
                set target [get_node_info -name $out]
                if {[get_node_info -type $out] ne "reg"} {error "Non-register CD payload destination $target"}
                lappend pairs [list $name $target]
                if {[cd_cdc_cross_target $direction $target]} {lappend cross $target} else {lappend locals $target}
            }
        }
    }
    set cd_cdc_source($direction) [cd_cdc_exact $sources]
    set cd_cdc_target($direction) [cd_cdc_exact $cross]
    set cd_cdc_local_names($direction) [lsort -unique $locals]
    set cd_cdc_pairs($direction) [lsort -unique $pairs]
    set from $cd_cdc_source($direction);set to $cd_cdc_target($direction)
    set_max_delay -from $from -to $to 13
    set_min_delay -from $from -to $to -13
    if {$direction eq "core_host"} {
        set_net_delay -max -from $from -to $to 13
        set_net_delay -min -from $from -to $to 0
    }
    set_max_skew -from $from -to $to 13
    puts "CD_CDC_SCOPE $direction [get_collection_size $from] [get_collection_size $to] local_[llength $cd_cdc_local_names($direction)]"
}
if {$cd_cdc_disc_menu} {
    # A separate ordinary level synchronizer, not a held-payload bus. The
    # first stage alone is cut, lane by lane; filter and event logic stay timed.

    set local_reset [cd_menu_domain_reset]
    for {set bit 0} {$bit<2} {incr bit} {
        foreach {stem previous} {disc_meta disc_setting disc_sync disc_meta disc_last disc_sync} {
            set stage [cd_menu_regs $stem $bit];set prior [cd_menu_regs $previous $bit]
            set allowed [cd_cdc_names $prior]
            foreach_in_collection r $stage {
                set actual [cd_cdc_names [get_fanins -synch [cd_cdc_exact [list [get_node_info -name $r]]]]]
                cd_menu_check_inputs $actual $allowed $local_reset
            }
        }
        set first [cd_menu_regs disc_meta $bit]
        set allowed [cd_cdc_names [cd_menu_regs disc_sync $bit]]
        foreach name [cd_cdc_names [get_fanouts $first]] {
            if {$name ni $allowed} {error "Disc menu first-stage fanout escapes $bit $name"}
        }
        set_false_path -from [cd_menu_regs disc_setting $bit] -to $first
    }
    puts CD_MENU_LEVEL_CDC_TWO_LANES_EXACT_FIRST_STAGE_ONLY
}
set cd_cdc_chains {
    h_req_s 3 req_t host h_done_s 3 done_t host h_sel_s 3 sel_t host
    h_mdone_s 3 mount_done_t host c_resp_s 3 resp_t core
    c_mount_s 3 mount_t core c_selack_s 3 sel_ack_t core c_pending_s 2 pending_file core
}
foreach {stem width origin domain} $cd_cdc_chains {
    set first [cd_cdc_bit $stem $width 0]
    set src [cd_cdc_regs $origin 1]
    set src_names [cd_cdc_names $src]
    # No combinational or extra-register consumer may leave the metastability
    # stage. Every physical stage is explicit FF storage, never shift-tap RAM.
    for {set bit 0} {$bit<$width} {incr bit} {
        set stage [cd_cdc_bit $stem $width $bit]
        foreach_in_collection r $stage {
            set one [cd_cdc_exact [list [get_node_info -name $r]]]
            if {$bit==0} {set allowed $src_names} else {set allowed [cd_cdc_names [cd_cdc_bit $stem $width [expr {$bit-1}]]]}
            set inputs [cd_cdc_names [get_fanins -synch $one]]
            if {![llength $inputs]} {error "Missing CD synchronizer input $stem $bit"}
            foreach input $inputs {if {$input ni $allowed} {error "Unknown CD synchronizer input $stem $bit $input"}}
        }
        if {$bit==0} {
            set allowed [cd_cdc_names [cd_cdc_bit $stem $width 1]]
            set outputs [cd_cdc_names [get_fanouts $stage]]
            if {![llength $outputs]} {error "Missing CD synchronizer settling consumer $stem"}
            foreach out $outputs {if {$out ni $allowed} {error "CD first-stage fanout escapes $stem $out"}}
        }
    }
    # Exact registered launch -> exact first stage only. No local stage,
    # local reset release, toggle edge detector or event consumer is cut.
    set_false_path -from $src -to $first
    puts "CD_CDC_CHAIN $stem $width [get_collection_size $src] [get_collection_size $first] $domain"
}
