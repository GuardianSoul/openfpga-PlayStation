# Private routed copy; verify actual margin DELTA against an in-memory
# no-additional-margin control. Device-derived uncertainty stays present.
# quartus_sta -t <script> <fresh-output> [--fault-no-margin]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh CPU hold audit output"}
set fault [expr {[llength $quartus(args)]==2 ? [lindex $quartus(args) 1] : ""}]
if {$fault ni {{} --fault-no-margin}} {error "Unknown CPU hold fault"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh CPU hold output required"}
file mkdir $output
project_open apf_top
try {
create_timing_netlist
read_sdc
if {![info exists pocket_cpu_hold_clocks] || [get_collection_size $pocket_cpu_hold_clocks]!=3} {error "Missing related CPU hold request"}
proc cpu_hold_request {value} {
    global pocket_cpu_hold_clocks
    set_clock_uncertainty -add -hold -enable_same_physical_edge -from $pocket_cpu_hold_clocks -to $pocket_cpu_hold_clocks $value
}
proc cpu_hold_row {path} {
    set uncertainty 0;set rows 0
    foreach_in_collection p [get_path_info -required_points $path] {
        if {[get_point_info -type $p] eq "unc"} {set uncertainty [get_point_info -incremental_delay $p];incr rows}
    }
    if {$rows!=1} {error "Expected one actual hold-uncertainty requirement"}
    set key [list [get_node_info -name [get_path_info -from $path]] [get_node_info -name [get_path_info -to $path]]]
    return [list $key [get_path_info -slack $path] $uncertainty]
}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tfrom_clock\tto_clock\tkind\tslack_ns\tuncertainty_ns\tfrom\tto"
set delta [open [file join $output margin-delta.tsv] w]
puts $delta "corner\tfrom_clock\tto_clock\tfrom\tto\tuncertainty_delta_ns\tslack_delta_ns"
set coverage [open [file join $output coverage.tsv] w]
puts $coverage "corner\tfrom_clock\tto_clock\tkind\tpaths"
set violations {};set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    cpu_hold_request [expr {$fault eq "--fault-no-margin" ? 0 : $pocket_cpu_hold_ns}]
    update_timing_netlist
    set active [dict create]
    foreach_in_collection from $pocket_cpu_hold_clocks {
        foreach_in_collection to $pocket_cpu_hold_clocks {
            set pair [list [get_clock_info -name $from] [get_clock_info -name $to]]
            foreach kind {setup hold} {
                set paths [get_timing_paths -$kind -from_clock $from -to_clock $to -npaths 16]
                puts $coverage [join [concat [list $corner] $pair [list $kind [get_collection_size $paths]]] "\t"]
                if {$from eq $to && [get_collection_size $paths]!=16} {error "Missing same-clock CPU paths"}
                foreach_in_collection p $paths {
                    set slack [get_path_info -slack $p]
                    if {$slack<0} {lappend violations "$corner/$pair/$kind/$slack"}
                    set values [cpu_hold_row $p]
                    puts $table [join [concat [list $corner] $pair [list $kind $slack [lindex $values 2]] [lindex $values 0]] "\t"]
                    # Rise/fall data paths can share physical endpoints. Keep
                    # their minimum slack, not the last (less critical) row.
                    if {$kind eq "hold" && (![dict exists $active $pair [lindex $values 0]] ||
                        $slack<[lindex [dict get $active $pair [lindex $values 0]] 0])} {
                        dict set active $pair [lindex $values 0] [lrange $values 1 2]
                    }
                }
                report_timing -$kind -from_clock $from -to_clock $to -npaths 8 -detail full_path \
                    -file [file join $output corner${corner}-pair[llength [dict keys $active]]-${kind}-[get_clock_info -period $from]-[get_clock_info -period $to].txt]
            }
        }
    }
    cpu_hold_request 0
    update_timing_netlist
    foreach_in_collection from $pocket_cpu_hold_clocks {
        foreach_in_collection to $pocket_cpu_hold_clocks {
            set pair [list [get_clock_info -name $from] [get_clock_info -name $to]]
            if {![dict exists $active $pair]} {continue}
            set baseline [dict create]
            foreach_in_collection p [get_timing_paths -hold -from_clock $from -to_clock $to -npaths 64] {
                set row [cpu_hold_row $p]
                if {![dict exists $baseline [lindex $row 0]] || [lindex $row 1]<[lindex [dict get $baseline [lindex $row 0]] 0]} {
                    dict set baseline [lindex $row 0] [lrange $row 1 2]
                }
            }
            dict for {key value} [dict get $active $pair] {
                if {![dict exists $baseline $key]} {
                    # Other endpoints can fill the global top-64 after the
                    # requirement changes. Query this exact physical pair;
                    # never substitute another path or waive its delta.
                    set exact_from [get_keepers -nowarn [list [lindex $key 0]]]
                    set exact_to [get_keepers -nowarn [list [lindex $key 1]]]
                    if {[get_collection_size $exact_from]==0 || [get_collection_size $exact_to]==0} {error "Missing exact no-margin keeper"}
                    foreach_in_collection p [get_timing_paths -hold -from_clock $from -to_clock $to \
                        -from $exact_from -to $exact_to -npaths 16] {
                        set row [cpu_hold_row $p]
                        if {[lindex $row 0] eq $key && (![dict exists $baseline $key] ||
                            [lindex $row 1]<[lindex [dict get $baseline $key] 0])} {
                            dict set baseline $key [lrange $row 1 2]
                        }
                    }
                }
                if {![dict exists $baseline $key]} {error "Missing paired no-margin physical path"}
                set base [dict get $baseline $key]
                set ud [expr {[lindex $value 1]-[lindex $base 1]}]
                set sd [expr {[lindex $base 0]-[lindex $value 0]}]
                # The direct requirement increment is authoritative. Slack
                # compares selected data arcs and remains diagnostic; sharing
                # endpoints does not make those selected paths identical.
                if {abs($ud-$pocket_cpu_hold_ns)>0.002} {error "Added CPU hold margin absent from actual timing requirement: unc_delta=$ud slack_delta=$sd active=$value base=$base pair=$pair"}
                puts $delta [join [concat [list $corner] $pair $key [list $ud $sd]] "\t"]
            }
        }
    }
    cpu_hold_request $pocket_cpu_hold_ns
    update_timing_netlist
    incr corner
}
close $table;close $delta;close $coverage
if {$corner!=4} {error "Expected four CPU hold corners"}
if {[llength $violations]} {error "Related CPU timing remains negative: $violations"}
puts "PASS_CPU_HOLD_MARGIN all9_pairs corners4 actual_requirement_delta_$pocket_cpu_hold_ns"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
