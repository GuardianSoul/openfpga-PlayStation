# Private routed copy. Independently reconstruct paths from timing points;
# compare physical pin bounds, ordinary clock checks and every FF replica.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh Native hold audit output"}
set fault [lindex $quartus(args) 1]
if {$fault ni {{} --fault-no-bound --fault-tight --fault-hidden-path --fault-missing-copy}} {error "Unknown Native hold fault"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh Native hold audit output required"}
file mkdir $output
project_open apf_top
try {
create_timing_netlist
read_sdc
if {![info exists native_hold_pairs] || [dict size $native_hold_pairs]<3} {error "Missing Native hold requests"}
if {$fault in {--fault-no-bound --fault-tight}} {
    dict for {key values} $native_hold_pairs {
        lassign $key from to
        set value [expr {$fault eq "--fault-tight" ? 10 : 0}]
        set_net_delay -min -from [native_hold_exact_pin $from] -to [native_hold_exact_pin $to] $value
        if {$fault eq "--fault-tight"} {dict set native_hold_pairs $key [lreplace $values 1 1 $value]}
    }
}
if {$fault eq "--fault-hidden-path"} {
    dict for {key values} $native_hold_pairs {
        if {[lindex $values 0] eq "dma"} {
            set_false_path -from [get_registers -no_duplicates [list [lindex $values 2]]] \
                -to [get_registers -no_duplicates [list [lindex [lindex $values 4] 0]]]
            break
        }
    }
}
if {$fault eq "--fault-missing-copy"} {
    set changed 0
    dict for {key values} $native_hold_pairs {
        if {[llength [lindex $values 4]]>1} {
            dict set native_hold_pairs $key [lreplace $values 4 4 [lrange [lindex $values 4] 1 end]]
            set changed 1;break
        }
    }
    if {!$changed} {error "Missing physical-copy fault fixture"}
}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tlabel\tkind\tslack_ns\tuncertainty_ns\tfrom\tto\tq\tlut_output"
set net_table [open [file join $output nets.tsv] w]
puts $net_table "corner\tlabel\tslack_ns\trequired_ns\tactual_ns\tq\tlut_output"
set failures {};set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    set_clock_uncertainty -add -hold -enable_same_physical_edge \
        -from $pocket_cpu_hold_clocks -to $pocket_cpu_hold_clocks $pocket_cpu_hold_ns
    update_timing_netlist
    set seen_keys [dict create]
    foreach label {dma sdr} {
        set selected_names {}
        foreach_in_collection s $native_hold_sources($label) {
            set name [get_node_info -name $s];regexp {\[([0-9]+)\](?:~.*)?$} $name -> bit
            if {[lsearch -exact $native_hold_selected($label) $bit]>=0} {lappend selected_names $name}
        }
        set seen_sources {};set target_count 0
        foreach_in_collection target $native_hold_targets($label) {
            set tn [get_node_info -name $target];regexp {\[([0-9]+)\](?:~.*)?$} $tn -> target_bit
            set wanted 0
            foreach bit $native_hold_selected($label) {if {$target_bit==$bit%$native_hold_modulo($label)} {set wanted 1}}
            if {!$wanted} {continue}
            incr target_count
            set dst [get_registers -no_duplicates -nowarn [list $tn]]
            if {[get_collection_size $dst]!=1} {error "Missing exact Native FF target"}
            set incoming {}
            foreach_in_collection s [get_fanins -synch $dst] {
                set sn [get_node_info -name $s]
                if {[lsearch -exact $selected_names $sn]>=0} {lappend incoming $sn}
            }
            if {![llength $incoming]} {error "Missing independent Native target fanin"}
            foreach sn [lsort -unique $incoming] {
                set src [get_registers -no_duplicates -nowarn [list $sn]]
                lappend seen_sources $sn
                foreach kind {setup hold} {
                    if {[get_collection_size [get_timing_paths -false_path -$kind -from $src -to $dst -npaths 1]]} {
                        error "Hidden Native clock path"
                    }
                    set paths [get_timing_paths -$kind -from $src -to $dst -npaths 16]
                    if {![get_collection_size $paths]} {error "Missing Native clock path"}
                    foreach_in_collection path $paths {
                        if {[get_node_info -name [get_path_info -from $path]] ne $sn ||
                            [get_node_info -name [get_path_info -to $path]] ne $tn} {error "Native FF pair changed"}
                        foreach field {from_clock to_clock} {
                            if {[get_clock_info -name [get_path_info -$field $path]] ne
                                [get_clock_info -name $native_hold_clock($label)]} {error "Native hold clock changed"}
                        }
                        set started 0;set q {};set lut {};set comb_count 0
                        foreach_in_collection point [get_path_info -arrival_points $path] {
                            if {[get_point_info -type $point] eq "utco"} {set started 1}
                            if {!$started || [get_point_info -type $point] ne "cell"} {continue}
                            set node [get_point_info -node $point]
                            if {$node eq {} || [get_node_info -type $node] ne "pin"} {continue}
                            set name [get_node_info -name $node];set port [lindex [split $name |] end]
                            if {$port in {q regout}} {set q $name}
                            if {$port eq "combout"} {set lut $name;incr comb_count}
                        }
                        set key [list $q $lut]
                        if {$q eq {} || $comb_count!=1 || ![dict exists $native_hold_pairs $key]} {
                            error "Native timing path has no complete physical bound"
                        }
                        set values [dict get $native_hold_pairs $key]
                        if {[lindex $values 0] ne $label || [lindex $values 2] ne $sn ||
                            [lsearch -exact [lindex $values 4] $tn]<0} {error "Native physical target coverage incomplete"}
                        dict set seen_keys $key 1
                        set uncertainty {};set uncertainty_rows 0
                        foreach_in_collection point [get_path_info -required_points $path] {
                            if {[get_point_info -type $point] eq "unc"} {
                                set uncertainty [get_point_info -incremental_delay $point];incr uncertainty_rows
                            }
                        }
                        if {$uncertainty_rows!=1 || ($kind eq "hold" && $uncertainty<.149)} {error "Native CPU uncertainty was relaxed"}
                        set slack [get_path_info -slack $path]
                        puts $table [join [concat [list $corner $label $kind $slack $uncertainty $sn $tn] $key] "\t"]
                        if {$slack<=0} {lappend failures "$corner/$label/$kind/$slack"}
                    }
                }
            }
        }
        if {$target_count==0 || [lsort -unique $seen_sources] ne [lsort -unique $selected_names]} {
            error "Native ordinary clock coverage incomplete"
        }
    }
    if {[lsort [dict keys $seen_keys]] ne [lsort [dict keys $native_hold_pairs]]} {error "Native physical net coverage incomplete"}
    set file [file join $output corner${corner}-nets.txt]
    report_net_delay -nworst 1000 -file $file
    set f [open $file r];set text [read $f];close $f
    set seen [dict create]
    foreach line [split $text "\n"] {
        if {![string match {;*} $line]} {continue}
        set cells {};foreach part [lrange [split $line ";"] 1 end-1] {lappend cells [string trim $part]}
        if {[llength $cells]!=7 || [lindex $cells 6] ne "min"} {continue}
        set key [lrange $cells 4 5]
        if {![dict exists $native_hold_pairs $key]} {continue}
        set values [dict get $native_hold_pairs $key]
        if {![string is double -strict [lindex $cells 2]] ||
            abs([lindex $cells 2]-[lindex $values 1])>.001} {error "Native physical minimum absent"}
        dict set seen $key 1
        puts $net_table [join [concat [list $corner [lindex $values 0]] [lrange $cells 1 5]] "\t"]
        if {[lindex $cells 1]<=0} {lappend failures "$corner/net/[lindex $cells 1]"}
    }
    if {[lsort [dict keys $seen]] ne [lsort [dict keys $native_hold_pairs]]} {error "Missing Native net-delay rows"}
    incr corner
}
close $table;close $net_table
if {$corner!=4} {error "Missing Native route corners"}
if {[llength $failures]} {error "Native physical routing/timing violates requirements: $failures"}
puts "PASS_NATIVE_HOLD_ROUTE corners4 original_clock_checks CPUreserve150ps all_physical_pairs"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
