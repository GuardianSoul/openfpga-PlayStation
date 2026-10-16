# Source-bound live disc-swap CDC. Requires the before-map preparer and
# all-corner physical flight/settling/local-clock verification after routing.
proc cd_cdc_regs {stem width} {
    set owner {core_top:ic|psx_cd_stream:disc_stream}
    set regs [get_registers -nowarn [list "${owner}|${stem}*"]]
    set bits {}
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {$width==1 && $stem in {m_valid req_t done_t sel_t mount_done_t resp_t mount_t sel_ack_t pending_file}} {
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
    if {[lsort -integer -unique $bits] ne $expected} {error "Missing CD geometry $stem: $bits"}
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
    set stream {core_top:ic|psx_cd_stream:disc_stream|}
    set cd {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|cd_top:icd_top|}
    if {[string first $stream $name]==0} {
        set suffix [string range $name [string length $stream] end]
        if {$direction eq "core_host"} {return [regexp {^dem_lba\[([0-9]|1[0-9])\](~DUPLICATE[0-9]*)?$} $suffix]}
        if {[regexp {^(has_disc|mount_done_t|disc_change_done|cstate\.state_bit_([0-9]|10)|kind\[[01]\]|rd_addr\[([0-9]|10)\]|lc_b\[([0-9]|1[01])\]|lc_key\[([0-9]|1[0-5])\]|lc_w0\[([0-9]|[12][0-9]|3[01])\])(~DUPLICATE[0-9]*)?$} $suffix]} {return 1}
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
if {![info exists cd_cdc_libcrypt] || $cd_cdc_libcrypt ni {0 1}} {error "Missing CD LibCrypt specialization"}
cd_cdc_inactive_presence
set cd_cdc_core_clock {ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}
foreach {name expected} [list clk_74a 13.468 $cd_cdc_core_clock 29.526] {
    set c [get_clocks -nowarn [list $name]]
    if {[get_collection_size $c]!=1 || abs([get_clock_info -period $c]-$expected)>.002} {error "CD clock contract differs $name"}
}
# The source-bound top drives DISC_SLOT=DEFAULT_SLOT=101. A surviving mount
# predicate is constrained with the other payloads; every local path stays timed.
set cd_cdc_slot_payload [cd_cdc_slot_fields]
set fields {m_valid 1 m_end 19 m_minutes 8 m_seconds 8 m_ntracks 6 resp_buf 1 resp_kind 2}
lappend fields {*}$cd_cdc_slot_payload
if {$cd_cdc_libcrypt} {
    lappend fields m_lc_bytes 12
} elseif {[get_collection_size [get_registers -nowarn {*|disc_stream|m_lc_bytes*}]]!=0} {
    error "Disabled LibCrypt payload did not specialize"
}
foreach {direction fields} [list host_core $fields core_host {req_lba 20}] {
    set sources {};set cross {};set locals {};set pairs {}
    foreach {stem width} $fields {
        foreach_in_collection r [cd_cdc_regs $stem $width] {
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
