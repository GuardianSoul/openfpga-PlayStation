# Read-only protected-copy audit. Identity LUTs must be present in every
# selected physical FF path; ordinary clocks and150ps reserve remain active.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh identity-LUT audit output"}
set fault [lindex $quartus(args) 1]
if {$fault ni {{} --fault-hidden-path --fault-missing-cell}} {error "Unknown identity-LUT fault"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh identity-LUT audit output required"}
file mkdir $output

proc identity_bank {pattern expression count} {
    set regs [get_registers -nowarn $pattern]
    set bits {};set instances {};set physical [dict create];set names [dict create]
    foreach_in_collection r $regs {
        set name [get_node_info -name $r]
        if {![regexp $expression $name -> instance bit replica] || $bit>=$count} {
            error "Unknown identity-LUT register $name"
        }
        set cell [get_node_info -cell $r]
        if {[dict exists $physical $cell]} {error "Ambiguous identity-LUT register alias"}
        dict set physical $cell 1
        dict set names $name $bit
        lappend bits $bit;lappend instances $instance
    }
    set expected {};for {set i 0} {$i<$count} {incr i} {lappend expected $i}
    if {[lsort -integer -unique $bits] ne $expected ||
        [llength [lsort -unique $instances]]!=1 || [get_collection_size $regs]>4*$count} {
        error "Incomplete identity-LUT register geometry"
    }
    return [list $regs $names [lindex $instances 0]]
}

proc identity_exact_register {name} {
    set r [get_registers -no_duplicates -nowarn [list $name]]
    if {[get_collection_size $r]!=1} {error "Missing exact identity-LUT FF $name"}
    return $r
}

project_open apf_top
try {
create_timing_netlist
read_sdc
if {![info exists pocket_cpu_hold_ns] || abs($pocket_cpu_hold_ns-.15)>.0001} {
    error "Identity LUTs require retained150ps CPU reserve"
}
set leaves [dict create]
foreach_in_collection c [get_cells -compatibility_mode -nowarn {*|pocket_hold_*|identity_lut}] {
    if {[get_cell_info -wysiwyg_type $c] ne "cyclonev_lcell_comb"} {error "Unexpected identity-LUT atom type"}
    dict set leaves [get_cell_info -name $c] $c
}
if {$fault eq "--fault-missing-cell" && [dict size $leaves]} {
    dict unset leaves [lindex [dict keys $leaves] 0]
}
if {[dict size $leaves]!=3} {error "Native identity cell census incomplete"}
set pairs {};set used_leaves [dict create]
foreach {label sp sr sc tp tr tc selected modulo clock_index} [list \
    dma {*|dma:idma|nextAddr[*]*} {^(.*\|dma:idma)\|nextAddr\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 24 \
        {*|dma:idma|dmaSettings.D_MADR[*]*} {^(.*\|dma:idma)\|dmaSettings\.D_MADR\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 24 {11} 24 0 \
    sdr {*|psx_sdram_ctrl:controller|wdata[*]*} {^(.*\|psx_sdram_ctrl:controller)\|wdata\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 32 \
        {*|psx_sdram_ctrl:controller|dq_out[*]*} {^(.*\|psx_sdram_ctrl:controller)\|dq_out\[([0-9]+)\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} 16 {4 26} 16 2] {
    lassign [identity_bank $sp $sr $sc] sources source_names source_instance
    lassign [identity_bank $tp $tr $tc] targets target_names target_instance
    if {$source_instance ne $target_instance} {error "Identity-LUT register instances differ"}
    set clock [get_clocks "*cpu_pll*general\[$clock_index\]*OUTPUT_COUNTER*divclk"]
    if {[get_collection_size $clock]!=1} {error "Missing identity-LUT clock"}
    foreach_in_collection c $clock {
        if {abs([get_clock_info -period $c]-[lindex {29.5251 14.76255 9.8417} $clock_index])>.003} {
            error "Identity-LUT clock period changed"
        }
    }
    set selected_names [dict create]
    foreach_in_collection source $sources {
        set name [get_node_info -name $source];set bit [dict get $source_names $name]
        if {[lsearch -exact $selected $bit]<0} {continue}
        set parent [join [lrange [split [get_cell_info -name [get_node_info -cell $source]] |] 0 end-1] |]
        set child [expr {$label eq "dma" ? "pocket_hold_dma11" : "pocket_hold_sdr${bit}_cell"}]
        set leaf "$parent|$child|identity_lut"
        if {![dict exists $leaves $leaf]} {error "Missing owned identity LUT $leaf"}
        dict set used_leaves $leaf 1
        dict set selected_names $name [list $bit $leaf]
    }
    set seen_sources [dict create]
    foreach_in_collection target $targets {
        set tn [get_node_info -name $target];set bit [dict get $target_names $tn]
        set wanted 0
        foreach b $selected {if {$bit==$b%$modulo} {set wanted 1}}
        if {!$wanted} {continue}
        set incoming 0
        foreach_in_collection source [get_fanins -synch [identity_exact_register $tn]] {
            set sn [get_node_info -name $source]
            if {![dict exists $selected_names $sn]} {continue}
            lassign [dict get $selected_names $sn] b leaf
            if {$bit!=$b%$modulo} {error "Identity-LUT data bit changed"}
            lappend pairs [list $label $sn $tn $leaf $clock]
            dict set seen_sources $sn 1;incr incoming
        }
        if {$incoming==0} {error "Missing independent identity-LUT FF fanin"}
    }
    if {[lsort [dict keys $seen_sources]] ne [lsort [dict keys $selected_names]]} {
        error "Incomplete identity-LUT physical source coverage"
    }
}
if {[lsort [dict keys $used_leaves]] ne [lsort [dict keys $leaves]]} {error "Unaccounted identity-LUT atom"}
if {$fault eq "--fault-hidden-path"} {
    lassign [lindex $pairs 0] label sn tn
    set_false_path -from [identity_exact_register $sn] -to [identity_exact_register $tn]
}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tlabel\tkind\tslack_ns\tuncertainty_ns\tfrom\tto\tidentity_lut"
set corner 0;set violations {}
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    set_clock_uncertainty -add -hold -enable_same_physical_edge \
        -from $pocket_cpu_hold_clocks -to $pocket_cpu_hold_clocks $pocket_cpu_hold_ns
    update_timing_netlist
    foreach pair $pairs {
        lassign $pair label sn tn leaf clock
        set src [identity_exact_register $sn];set dst [identity_exact_register $tn]
        foreach kind {setup hold} {
            if {[get_collection_size [get_timing_paths -false_path -$kind -from $src -to $dst -npaths 1]]} {
                error "Hidden Native identity-LUT clock path"
            }
            set paths [get_timing_paths -$kind -from $src -to $dst -npaths 16]
            if {![get_collection_size $paths]} {error "Missing Native identity-LUT clock path"}
            foreach_in_collection path $paths {
                foreach field {from_clock to_clock} {
                    if {[get_clock_info -name [get_path_info -$field $path]] ne [get_clock_info -name $clock]} {
                        error "Native identity-LUT clock changed"
                    }
                }
                set seen 0
                foreach_in_collection point [get_path_info -arrival_points $path] {
                    if {[get_point_info -type $point] ne "cell"} {continue}
                    set node [get_point_info -node $point]
                    if {$node eq {} || [get_node_info -type $node] ne "pin"} {continue}
                    if {[lindex [split [get_node_info -name $node] |] end] ne "combout"} {continue}
                    if {[get_cell_info -name [get_node_info -cell $node]] eq $leaf} {incr seen}
                }
                if {$seen!=1} {error "Native identity LUT missing from physical FF path"}
                set uncertainty {};set count 0
                foreach_in_collection point [get_path_info -required_points $path] {
                    if {[get_point_info -type $point] eq "unc"} {
                        set uncertainty [get_point_info -incremental_delay $point];incr count
                    }
                }
                if {$count!=1 || ($kind eq "hold" && $uncertainty<.149)} {error "Identity-LUT CPU reserve relaxed"}
                set slack [get_path_info -slack $path]
                puts $table [join [list $corner $label $kind $slack $uncertainty $sn $tn $leaf] "\t"]
                if {$slack<=0} {lappend violations "$corner/$label/$kind/$slack"}
            }
        }
    }
    incr corner
}
close $table
if {$corner!=4} {error "Missing identity-LUT timing corners"}
if {[llength $violations]} {error "Native identity-LUT timing remains negative: $violations"}
puts "PASS_NATIVE_HOLD_CELLS corners4 all3atoms allphysicalFFpairs reserve150ps"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
