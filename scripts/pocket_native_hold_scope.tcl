# Strengthening only: keep all clock setup/hold checks and CPU uncertainty.
# A register-to-register net query misses these one-LUT paths. Bind the
# source Q to the connected LUT OUTPUT, not its unsupported input pin.
foreach {name lower upper} {native_hold_dma_ns 0.4 1.0 native_hold_sdr_ns 0.2 1.0} {
    if {![info exists $name] || ![string is double -strict [set $name]] ||
        [set $name]<$lower || [set $name]>$upper} {error "Invalid Native minimum delay $name"}
}
if {![info exists pocket_cpu_hold_ns] || abs($pocket_cpu_hold_ns-.15)>.0001} {
    error "Native routing requires retained150ps CPU reserve"
}

proc native_hold_exact_pin {name} {
    set pins [get_pins -no_duplicates -nowarn [list $name]]
    if {[get_collection_size $pins]!=1} {error "Missing exact Native data pin $name"}
    foreach_in_collection pin $pins {
        if {[get_node_info -name $pin] ne $name} {error "Native data pin alias changed"}
    }
    return $pins
}

proc native_hold_registers {pattern expression count} {
    set regs [get_registers -nowarn $pattern]
    set bits {};set instances {};set cells [dict create]
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {![regexp $expression $name -> instance bit replica] || $bit>=$count} {
            error "Unknown Native data register $name"
        }
        set cell [get_node_info -cell $r]
        if {[dict exists $cells $cell]} {error "Ambiguous Native physical register alias"}
        dict set cells $cell [list $name $bit]
        lappend bits $bit;lappend instances $instance
    }
    set expected {};for {set i 0} {$i<$count} {incr i} {lappend expected $i}
    if {[lsort -integer -unique $bits] ne $expected ||
        [llength [lsort -unique $instances]]!=1 || [get_collection_size $regs]>4*$count} {
        error "Missing Native register geometry $pattern"
    }
    return [list $regs $cells [lindex $instances 0]]
}

proc native_hold_collect {sources target_cells selected modulo} {
    set result [dict create];set reached [dict create]
    foreach_in_collection r $sources {
        set name [get_node_info -name $r]
        regexp {\[([0-9]+)\](?:~.*)?$} $name -> bit
        if {[lsearch -exact $selected $bit]<0} {continue}
        set found 0
        foreach_in_collection q [get_cell_info -out_pins [get_node_info -cell $r]] {
            if {[lindex [split [get_node_info -name $q] |] end] ni {q regout}} {continue}
            foreach edge [get_node_info -fanout_edges $q] {
                if {[string tolower [get_edge_info -delay_type $edge]] ne "ic"} {continue}
                set input [get_edge_info -dst $edge];set cell [get_node_info -cell $input]
                if {$cell eq {}} {continue}
                foreach_in_collection output [get_cell_info -out_pins $cell] {
                    if {[lindex [split [get_node_info -name $output] |] end] ne "combout"} {continue}
                    set targets {}
                    foreach tail [get_node_info -fanout_edges $output] {
                        if {[string tolower [get_edge_info -delay_type $tail]] ne "ic"} {continue}
                        set d [get_edge_info -dst $tail];set target_cell [get_node_info -cell $d]
                        if {![dict exists $target_cells $target_cell] ||
                            [lindex [split [get_node_info -name $d] |] end] ne "d"} {continue}
                        lassign [dict get $target_cells $target_cell] target target_bit
                        if {$target_bit!=$bit%$modulo} {error "Cross-bit Native hold connection"}
                        lappend targets $target
                        dict set reached $target $name 1
                    }
                    if {![llength $targets]} {continue}
                    set key [list [get_node_info -name $q] [get_node_info -name $output]]
                    dict set result $key [list $name $bit [lsort -unique $targets]]
                    incr found
                }
            }
        }
        if {$found!=1} {error "Expected one connected LUT for Native source $name, got $found"}
    }
    dict for {cell entry} $target_cells {
        lassign $entry target bit
        foreach selected_bit $selected {
            if {$bit==$selected_bit%$modulo && ![dict exists $reached $target]} {
                error "Missing physical Native target connection $target"
            }
        }
    }
    if {[dict size $result]<[llength $selected]} {error "Missing Native selected source connection"}
    return $result
}

set native_hold_sdr_bits {};for {set bit 0} {$bit<32} {incr bit} {lappend native_hold_sdr_bits $bit}
set native_hold_pairs [dict create]
foreach {label sp sr sc tp tr tc selected modulo minimum clock_index} [list \
    dma {*|dma:idma|nextAddr[*]*} {^(.*\|dma:idma)\|nextAddr\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 24 \
        {*|dma:idma|dmaSettings.D_MADR[*]*} {^(.*\|dma:idma)\|dmaSettings\.D_MADR\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 24 {11} 24 $native_hold_dma_ns 0 \
    sdr {*|psx_sdram_ctrl:controller|wdata[*]*} {^(.*\|psx_sdram_ctrl:controller)\|wdata\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 32 \
        {*|psx_sdram_ctrl:controller|dq_out[*]*} {^(.*\|psx_sdram_ctrl:controller)\|dq_out\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 16 $native_hold_sdr_bits 16 $native_hold_sdr_ns 2] {
    lassign [native_hold_registers $sp $sr $sc] sources source_cells instance
    lassign [native_hold_registers $tp $tr $tc] targets target_cells target_instance
    if {$instance ne $target_instance} {error "Native hold instances differ"}
    set clock [get_clocks "*cpu_pll*general\[$clock_index\]*OUTPUT_COUNTER*divclk"]
    if {[get_collection_size $clock]!=1} {error "Missing Native hold clock"}
    foreach_in_collection c $clock {
        if {abs([get_clock_info -period $c]-[lindex {29.5251 14.76255 9.8417} $clock_index])>.003} {
            error "Native hold clock period changed"
        }
    }
    set native_hold_sources($label) $sources
    set native_hold_targets($label) $targets
    set native_hold_clock($label) $clock
    set native_hold_selected($label) $selected
    set native_hold_modulo($label) $modulo
    set native_hold_minimum($label) $minimum
    set pairs [native_hold_collect $sources $target_cells $selected $modulo]
    dict for {key values} $pairs {
        lassign $key from to
        set_net_delay -min -from [native_hold_exact_pin $from] -to [native_hold_exact_pin $to] $minimum
        dict set native_hold_pairs $key [concat [list $label $minimum] $values]
    }
    puts "NATIVE_HOLD_ROUTE $label NETS_[dict size $pairs] MIN_$minimum SELECTED_$selected ORIGINAL_CLOCK_CHECKS_RETAINED"
}
