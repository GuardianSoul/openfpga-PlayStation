# Strengthen the whole DMA address-bank routing floor. No path exceptions,
# clock changes, multicycles or relaxation of CPU-family hold uncertainty.
proc dma_route_regs {stem} {
    set owner {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|dma:idma}
    set escaped [string map {. {\.}} $stem]
    set regs [get_registers -nowarn [list [format {%s|%s*} $owner $stem]]]
    set bits {}
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {[string range $name 0 [expr {[string length $owner]-1}]] ne $owner} {error "Wrong DMA route owner $name"}
        set suffix [string range $name [expr {[string length $owner]+1}] end]
        if {![regexp [format {^%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $escaped] $suffix -> bit replica]} {error "Unknown DMA route register $name"}
        lappend bits $bit
    }
    set expected {};for {set i 0} {$i<24} {incr i} {lappend expected $i}
    if {[lsort -integer -unique $bits] ne $expected} {error "Missing DMA address-bank geometry $stem: $bits"}
    return $regs
}
proc dma_route_exact {names} {
    set names [lsort -unique $names]
    if {![llength $names]} {error "Empty DMA route endpoints"}
    set result [get_registers -no_duplicates -nowarn $names]
    if {[get_collection_size $result]!=[llength $names]} {error "Missing exact DMA route endpoints"}
    return $result
}
set dma_route_clock [get_clocks -nowarn {{ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}}]
if {[get_collection_size $dma_route_clock]!=1 || abs([get_clock_info -period $dma_route_clock]-29.5251)>.003} {error "DMA routing clock differs"}
if {![info exists pocket_cpu_hold_ns] || $pocket_cpu_hold_ns!=0.15} {error "DMA routing requires unchanged150ps reserve"}
set dma_route_sources [dma_route_regs nextAddr]
set dma_route_targets [dma_route_regs dmaSettings.D_MADR]
set dma_route_target_names {}
foreach_in_collection r $dma_route_targets {lappend dma_route_target_names [get_node_info -name $r]}
set dma_route_pairs {};set dma_route_observed_bits {};set dma_route_observed_targets {}
foreach_in_collection r $dma_route_sources {
    set name [get_node_info -name $r]
    regexp {nextAddr\[([0-9]+)\]} $name -> bit
    foreach_in_collection out [get_fanouts [dma_route_exact [list $name]]] {
        set target [get_node_info -name $out]
        if {$target in $dma_route_target_names} {
            if {![regexp {D_MADR\[([0-9]+)\]} $target -> target_bit] || $bit!=$target_bit} {error "DMA address routing crossed bit lanes $name $target"}
            lappend dma_route_pairs [list $name $target]
            lappend dma_route_observed_bits $bit
            lappend dma_route_observed_targets $target
        }
    }
}
# Physical copies can partition fanout: one copy feeds IRQ/control logic,
# another the address-bank data input. Require each logical lane and every
# destination copy, keeping ALL source copies in the routing collection.
set dma_route_expected_bits {};for {set bit 0} {$bit<24} {incr bit} {lappend dma_route_expected_bits $bit}
if {[lsort -integer -unique $dma_route_observed_bits] ne $dma_route_expected_bits} {
    error "Missing logical DMA address-bank consumer"
}
if {[lsort -unique $dma_route_observed_targets] ne [lsort -unique $dma_route_target_names]} {
    error "DMA address-bank destination has no source"
}
set dma_route_pairs [lsort -unique $dma_route_pairs]
set_net_delay -min 0.5 -from $dma_route_sources -to $dma_route_targets
puts "DMA_ADDRESS_ROUTE_MIN_NS 0.5 SOURCES_[get_collection_size $dma_route_sources] TARGETS_[get_collection_size $dma_route_targets] PAIRS_[llength $dma_route_pairs] CPU_HOLD_PS150"
