# DIAGNOSTIC FOR A PRIVATE POST-FIT ANALYSIS COPY ONLY. Not release-selected.
# The source-bound wrapper supports the captured Native 19-track/two-buffer
# top; selection-slot payload is constant in that single reloadable slot.
proc cd_bundle_regs {stem width} {
    set owner {core_top:ic|psx_cd_stream:disc_stream}
    set regs [get_registers -nowarn [list "${owner}|${stem}*"]]
    set bits {}
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {$width==1 && $stem eq "m_valid"} {
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
proc cd_bundle_exact {names} {
    set names [lsort -unique $names]
    if {![llength $names]} {error "Empty CD payload collection"}
    set regs [get_registers -no_duplicates -nowarn $names]
    if {[get_collection_size $regs]!=[llength $names]} {error "Missing exact CD physical endpoints"}
    return $regs
}
proc cd_bundle_cross_target {direction name} {
    set stream {core_top:ic|psx_cd_stream:disc_stream|}
    set cd {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|cd_top:icd_top|}
    if {[string first $stream $name]==0} {
        set suffix [string range $name [string length $stream] end]
        if {$direction eq "core_host"} {return [regexp {^dem_lba\[([0-9]|1[0-9])\](~DUPLICATE[0-9]*)?$} $suffix]}
        if {[regexp {^(has_disc|mount_done_t|disc_change_done|cstate\.state_bit_[0-6]|kind\[[01]\]|rd_addr\[([0-9]|10)\])(~DUPLICATE[0-9]*)?$} $suffix]} {return 1}
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
foreach {direction fields} {
    host_core {m_valid 1 m_end 19 m_minutes 8 m_seconds 8 m_ntracks 6 resp_buf 1 resp_kind 2}
    core_host {req_lba 20}
} {
    set sources {};set cross {};set locals {};set pairs {}
    foreach {stem width} $fields {
        foreach_in_collection r [cd_bundle_regs $stem $width] {
            set name [get_node_info -name $r];lappend sources $name
            foreach_in_collection out [get_fanouts [cd_bundle_exact [list $name]]] {
                set target [get_node_info -name $out]
                if {[get_node_info -type $out] ne "reg"} {error "Non-register CD payload destination $target"}
                lappend pairs [list $name $target]
                if {[cd_bundle_cross_target $direction $target]} {lappend cross $target} else {lappend locals $target}
            }
        }
    }
    set cd_bundle_source($direction) [cd_bundle_exact $sources]
    set cd_bundle_target($direction) [cd_bundle_exact $cross]
    set cd_bundle_local_names($direction) [lsort -unique $locals]
    set cd_bundle_pairs($direction) [lsort -unique $pairs]
    set from $cd_bundle_source($direction);set to $cd_bundle_target($direction)
    set_max_delay -from $from -to $to 13
    set_min_delay -from $from -to $to -13
    # Only the direct request->dem_lba nets match a net-delay constraint.
    # Metadata/response paths pass through logic; their mandatory physical
    # flight check uses get_path_info -data_delay in the diagnostic auditor.
    if {$direction eq "core_host"} {
        set_net_delay -max -from $from -to $to 13
        set_net_delay -min -from $from -to $to 0
    }
    set_max_skew -from $from -to $to 13
    puts "CD_BUNDLE_SCOPE $direction [get_collection_size $from] [get_collection_size $to] local_[llength $cd_bundle_local_names($direction)]"
}
