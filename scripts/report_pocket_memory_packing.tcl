# Run in a PRIVATE COPY of a freshly routed Native project. Reports only;
# no exceptions or constraints are added. Successful execution is not closure.
# quartus_sta -t scripts/report_pocket_memory_packing.tcl <fresh-output>
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 1} {error "Expected output directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set cpu [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
set audio [get_clocks {*audio_pll*OUTPUT_COUNTER*divclk}]
set converter [get_keepers {*|audio_stream|converter|*}]
if {[get_collection_size $cpu]!=3 || [get_collection_size $audio]!=1 || [get_collection_size $converter]<20} {
    error "Missing selected Native/audio geometry"
}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
proc scoped {name args} {
    global table corner output
    foreach kind {setup hold} {
        set paths [get_timing_paths -$kind {*}$args -npaths 20]
        report_timing -$kind {*}$args -npaths 20 -detail full_path \
            -file [file join $output corner${corner}-${name}-${kind}.txt]
        if {[get_collection_size $paths]<1} {error "Empty timing scope $name/$kind"}
        foreach_in_collection p $paths {
            set row [list $corner $name $kind [get_path_info -slack $p]]
            foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $p]]}
            foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $p]]}
            puts $table [join $row "\t"]
        }
    }
}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    scoped global
    scoped cpu-family -from_clock $cpu -to_clock $cpu
    scoped audio -from_clock $audio -to_clock $audio
    scoped converter -from $converter -to $converter
    set index 0
    foreach_in_collection clock $cpu {
        scoped intra${index} -from_clock $clock -to_clock $clock
        incr index
    }
    incr corner
}
if {$corner!=4} {error "Expected four corners"}
close $table
report_clocks -file [file join $output clocks.txt]
report_exceptions -npaths 20 -file [file join $output exceptions.txt]
delete_timing_netlist
project_close -dont_export_assignments
