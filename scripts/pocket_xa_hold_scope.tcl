# Routing lower bound only. Leave all original clock setup/hold checks active.
if {![info exists pocket_xa_min_net_ns] || $pocket_xa_min_net_ns<0.25 || $pocket_xa_min_net_ns>2} {error "Expected .25..2ns minimum XA net delay"}
set pocket_xa_sources [get_registers -nowarn {*|cd_xa:icd_xa|clamped[*]}]
set pocket_xa_targets [get_keepers -nowarn {*|cd_xa:icd_xa|SyncFifo:ififoADPCM_*|*~PORT_A_DATA_IN_* *|cd_xa:icd_xa|SyncFifo:ififoADPCM_*|*~porta_datain_reg0}]
set pocket_xa_bits {};set pocket_xa_instances {};set pocket_xa_banks [dict create];set pocket_xa_counts [dict create L 0 R 0]
foreach_in_collection r $pocket_xa_sources {
    set name [get_node_info -name $r]
    if {![regexp {^(.*\|cd_xa:icd_xa)\|clamped\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $name -> instance bit replica] || $bit<0 || $bit>15} {error "Unknown XA data source $name"}
    lappend pocket_xa_bits $bit;lappend pocket_xa_instances $instance
}
if {[lsort -integer -unique $pocket_xa_bits] ne {0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15} || [llength [lsort -unique $pocket_xa_instances]]!=1} {error "Missing/ambiguous XA data source geometry"}
set pocket_xa_instance [lindex $pocket_xa_instances 0]
set pocket_xa_forms {}
foreach_in_collection r $pocket_xa_targets {
    set name [get_node_info -name $r]
    if {![regexp {^(.*\|cd_xa:icd_xa)\|SyncFifo:ififoADPCM_([LR])\|altsyncram:memory_rtl_0\|altsyncram_m9q1:auto_generated\|(ram_block[0-9]+a[0-9]+)~(PORT_A_DATA_IN_|porta_datain_reg)([0-9]+)$} $name -> instance channel bank form bit] ||
        $instance ne $pocket_xa_instance || $bit<0 || $bit>19} {error "Unknown XA RAM input $name"}
    lappend pocket_xa_forms $form
    dict incr pocket_xa_counts [list $form $channel]
    dict lappend pocket_xa_banks [list $form $channel $bank] $bit
}
set pocket_xa_forms [lsort -unique $pocket_xa_forms]
if {$pocket_xa_forms ni {{PORT_A_DATA_IN_} {porta_datain_reg} {PORT_A_DATA_IN_ porta_datain_reg}}} {error "Missing/unknown XA RAM input representation"}
set pocket_xa_physical_banks {};set pocket_xa_logical_banks {}
dict for {key bits} $pocket_xa_banks {
    lassign $key form channel bank
    if {$form eq "PORT_A_DATA_IN_"} {
        if {[llength [lsort -unique $bits]]!=8} {error "Missing XA RAM bank data bits"}
        lappend pocket_xa_physical_banks [list $channel $bank]
    } else {
        if {$bits ne {0}} {error "Unknown mapped XA RAM bit"}
        lappend pocket_xa_logical_banks [list $channel $bank]
    }
}
if {[lsearch -exact $pocket_xa_forms PORT_A_DATA_IN_]>=0} {
    if {[dict get $pocket_xa_counts [list PORT_A_DATA_IN_ L]]!=64 || [dict get $pocket_xa_counts [list PORT_A_DATA_IN_ R]]!=64 || [llength $pocket_xa_physical_banks]!=16} {error "Missing XA stereo RAM input geometry"}
}
if {$pocket_xa_forms eq {porta_datain_reg}} {
    # Mapped form: one logical register per data-bit bank, 16 per channel.
    if {[get_collection_size $pocket_xa_targets]!=32 || [dict get $pocket_xa_counts [list porta_datain_reg L]]!=16 || [dict get $pocket_xa_counts [list porta_datain_reg R]]!=16 || [llength $pocket_xa_logical_banks]!=32} {error "Missing mapped XA RAM input geometry"}
} elseif {[llength $pocket_xa_forms]==2} {
    # Fitted form also returns one named input alias for each physical bank.
    # Require the EXACT same 16 banks; never accept an incomplete/hybrid map.
    if {[get_collection_size $pocket_xa_targets]!=144 || [lsort $pocket_xa_logical_banks] ne [lsort $pocket_xa_physical_banks]} {error "Missing/mismatched fitted XA bank aliases"}
} elseif {[get_collection_size $pocket_xa_targets]!=128} {error "Missing physical XA RAM inputs"}
set pocket_xa_clock [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $pocket_xa_clock]!=1} {error "Missing XA clock"}
foreach_in_collection c $pocket_xa_clock {if {abs([get_clock_info -period $c]-29.5251)>0.003} {error "Unreviewed XA clock period"}}
set_net_delay -min -from $pocket_xa_sources -to $pocket_xa_targets $pocket_xa_min_net_ns
puts "XA_MIN_NET $pocket_xa_min_net_ns SOURCES_[get_collection_size $pocket_xa_sources] TARGETS_[get_collection_size $pocket_xa_targets] FORM_$pocket_xa_forms BOTH_CHANNELS NO_CLOCK_EXCEPTION"
