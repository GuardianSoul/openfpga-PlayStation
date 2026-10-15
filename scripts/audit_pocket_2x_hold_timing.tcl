# Run only on a PRIVATE COPY of a completed fit; never rewrites saved SDC.
# quartus_sta -t <script> <fresh output> [--fault-no-margin]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error {Expected fresh output and optional fault mode}}
set fault [expr {[llength $quartus(args)]==2 ? [lindex $quartus(args) 1] : ""}]
if {$fault ni {{} --fault-no-margin}} {error {Unknown 2x hold audit option}}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error {Fresh 2x hold audit output required}}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
if {![info exists pocket_2x_hold_ns] || $pocket_2x_hold_ns<0.05 || $pocket_2x_hold_ns>0.5} {
    error {Saved additional 2x hold margin absent}
}
set c [get_clocks {*cpu_pll*general[1]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $c]!=1} {error {Missing unique 2x clock}}
if {$fault eq "--fault-no-margin"} {
    set_clock_uncertainty -add -hold -enable_same_physical_edge -from $c -to $c 0
}
update_timing_netlist
set table [open [file join $output paths.tsv] w]
puts $table "corner\tkind\tslack_ns\tuncertainty_ns\tfrom\tto"
set corner 0
set violations {}
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        set paths [get_timing_paths -$kind -from_clock $c -to_clock $c -npaths 64]
        if {[get_collection_size $paths]!=64} {error {Missing 2x timing paths}}
        foreach_in_collection path $paths {
            set uncertainty 0;set unc_rows 0
            foreach_in_collection point [get_path_info -required_points $path] {
                if {[get_point_info -type $point] eq "unc"} {
                    set uncertainty [get_point_info -incremental_delay $point]
                    incr unc_rows
                }
            }
            set slack [get_path_info -slack $path]
            puts $table [join [list $corner $kind $slack $uncertainty \
                [get_node_info -name [get_path_info -from $path]] \
                [get_node_info -name [get_path_info -to $path]]] "\t"]
            if {$kind eq "hold" && ($unc_rows!=1 || $uncertainty<$pocket_2x_hold_ns-0.001)} {
                error {Added hold margin absent from actual timing requirement}
            }
            if {$slack<0} {lappend violations "corner${corner}_${kind}_$slack"}
        }
        report_timing -$kind -from_clock $c -to_clock $c -npaths 8 -detail full_path \
            -file [file join $output "corner${corner}-${kind}.txt"]
    }
    incr corner
}
close $table
if {$corner!=4} {error {Expected four 2x timing corners}}
delete_timing_netlist
project_close -dont_export_assignments
if {[llength $violations]} {error "2x timing remains negative: $violations"}
puts "PASS_2X_HOLD_MARGIN corners_4 paths_512 added_ns_$pocket_2x_hold_ns"
