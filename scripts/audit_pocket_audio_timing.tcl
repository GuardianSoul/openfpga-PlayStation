# Run in a PRIVATE COPY of a routed project with the saved audio SDC.
# quartus_sta -t <script> <output-directory> [--faults]
# Reports preserve all second-stage checks; successful Tcl is not closure.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected output directory and optional --faults"}
set faults 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--faults"} {error "Unknown option"}
    set faults 1
}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set first_patterns {}; set second_patterns {}
foreach pointer {wr rd} {
    set first_pattern [format {*audio_stream*|%s_gray_meta[*]} $pointer]
    set second_pattern [format {*audio_stream*|%s_gray_sync[*]} $pointer]
    if {[get_collection_size [get_registers $first_pattern]]!=7 ||
        [get_collection_size [get_registers $second_pattern]]!=7} {error "Missing audio synchronizer pair"}
    lappend first_patterns $first_pattern
    lappend second_patterns $second_pattern
}
set first [get_registers $first_patterns]
set second [get_registers $second_patterns]
puts "AUDIO_AUDIT_STAGES [get_collection_size $first] [get_collection_size $second]"
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        report_timing -$kind -to $first -npaths 100 -detail summary \
            -file [file join $output corner${corner}-first-${kind}.txt]
        report_timing -$kind -from $first -to $second -npaths 100 -detail summary \
            -file [file join $output corner${corner}-second-${kind}.txt]
    }
    report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four timing conditions"}
if {$faults} {
    set from [pocket_audio_gray_bus {*audio_stream*|} wr]
    set to [get_registers {*audio_stream*|wr_gray_meta[*]}]
    set_net_delay -max -from $from -to $to 0.001
    set_max_skew -from $from -to $to 0.001
    update_timing_netlist
    report_net_delay -nworst 500 -file [file join $output planted-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output planted-skew.txt]
}
delete_timing_netlist
project_close -dont_export_assignments
