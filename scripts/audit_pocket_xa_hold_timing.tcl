# Private routed copy, original setup/hold plus independent min-net coverage.
# quartus_sta -t <script> <fresh-output> [--fault-no-bound]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh XA route audit output"}
set fault [expr {[llength $quartus(args)]==2 ? [lindex $quartus(args) 1] : ""}]
if {$fault ni {{} --fault-no-bound}} {error "Unknown XA route audit mode"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh XA route output required"}
file mkdir $output
project_open apf_top
try {
create_timing_netlist
read_sdc
if {![info exists pocket_xa_min_net_ns]} {error "Missing XA routing request"}
if {$fault eq "--fault-no-bound"} {set_net_delay -min -from $pocket_xa_sources -to $pocket_xa_targets 0}
# The project query also retains each bank's named alias. Audit the actual
# 128 physical data inputs, while leaving the project's collection unchanged.
set physical_targets [get_keepers -nowarn {*|cd_xa:icd_xa|SyncFifo:ififoADPCM_*|*~PORT_A_DATA_IN_*}]
if {[get_collection_size $physical_targets]!=128} {error "Missing physical XA RAM data inputs"}
set sources {};set targets {}
foreach_in_collection r $pocket_xa_sources {lappend sources [get_node_info -name $r]}
foreach_in_collection r $physical_targets {lappend targets [get_node_info -name $r]}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
set net_table [open [file join $output net.tsv] w]
puts $net_table "corner\tslack_ns\trequired_ns\tactual_ns\tfrom\tto"
set violations {};set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition;update_timing_netlist
    foreach kind {setup hold} {
        if {[get_collection_size [get_timing_paths -false_path -$kind -from $pocket_xa_sources -to $physical_targets -npaths 1]]!=0} {error "XA data path was hidden"}
        set paths [get_timing_paths -$kind -from $pocket_xa_sources -to $physical_targets -npaths 500]
        set reached {}
        foreach_in_collection p $paths {
            set slack [get_path_info -slack $p];set from [get_node_info -name [get_path_info -from $p]];set to [get_node_info -name [get_path_info -to $p]]
            set row [list $corner $kind $slack $from $to]
            foreach field {from_clock to_clock} {
                set name [get_clock_info -name [get_path_info -$field $p]]
                if {$name ne [get_clock_info -name $pocket_xa_clock]} {error "XA data clock changed"}
                lappend row $name
            }
            if {[lsearch -exact $sources $from]<0 || [lsearch -exact $targets $to]<0} {error "Unreviewed XA data endpoint"}
            lappend reached $to;puts $table [join $row "\t"]
            if {$slack<0} {lappend violations "$corner/$kind/$slack"}
        }
        if {[llength [lsort -unique $reached]]!=128} {error "Missing timed XA RAM data input"}
        report_timing -$kind -from $pocket_xa_sources -to $physical_targets -npaths 16 -detail full_path -file [file join $output corner${corner}-${kind}.txt]
    }
    set netfile [file join $output corner${corner}-net.txt]
    report_net_delay -nworst 1000 -file $netfile
    set f [open $netfile r];set text [read $f];close $f
    set reached {}
    foreach line [split $text "\n"] {
        if {![string match {;*} $line]} {continue}
        set cells {};foreach part [lrange [split $line ";"] 1 end-1] {lappend cells [string trim $part]}
        if {[llength $cells]!=7 || [lindex $cells 6] ne "min" || [lsearch -exact $sources [lindex $cells 4]]<0 || [lsearch -exact $targets [lindex $cells 5]]<0} {continue}
        if {abs([lindex $cells 2]-$pocket_xa_min_net_ns)>0.001} {error "XA physical min-net requirement absent"}
        lappend reached [lindex $cells 5]
        puts $net_table [join [concat [list $corner] [lrange $cells 1 5]] "\t"]
        if {[lindex $cells 1]<0} {lappend violations "$corner/net/[lindex $cells 1]"}
    }
    if {[llength [lsort -unique $reached]]!=128} {error "Missing physical XA min-net endpoint"}
    incr corner
}
close $table;close $net_table
if {$corner!=4} {error "Expected four XA route corners"}
if {[llength $violations]} {error "XA physical routing/timing still violates requirements: $violations"}
puts "PASS_XA_ROUTE corners4 inputs128 minimum_net_ns_$pocket_xa_min_net_ns original_setup_hold_retained"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
