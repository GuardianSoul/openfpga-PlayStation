# Run ONLY in a PRIVATE COPY of a completed routed synchronous-main project.
# quartus_sta -t <this script> <fresh report directory>
# No SDC edits. Reports are evidence, not a hardware/whole-top timing verdict.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh output directory"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh output directory required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist

set cpu [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $cpu]!=4} {error "Expected CPU 1x/2x/3x plus forwarded PSRAM clock"}
set one {}; set three {}
foreach_in_collection clk $cpu {
    set name [get_clock_info -name $clk]
    puts "MAIN_SYNC_CPU_CLOCK $name"
    if {[string first {general[0].} $name]>=0} {set one $clk}
    if {[string first {general[2].} $name]>=0} {set three $clk}
}
if {$one eq "" || $three eq ""} {error "Missing exact CPU clock counters"}
set main [get_registers {*|main_commands|*}]
set clients [get_registers {*|psram_clients|*}]
if {[get_collection_size $main]<100 || [get_collection_size $clients]<100} {
    error "Missing main/PSRAM register hierarchy"
}
foreach {label pattern} {
    request-token {*|main_commands|request_toggle*}
    request-data {*|main_commands|request_payload[*]}
    request-ack {*|main_commands|request_ack}
    response-token {*|main_commands|response_toggle*}
    response-data {*|main_commands|response_payload[*] *|main_commands|read_data[*]}
    response-ack {*|main_commands|response_ack}
} {
    set regs($label) [get_registers $pattern]
    if {[get_collection_size $regs($label)]<1} {error "Missing live $label bank"}
    foreach_in_collection reg $regs($label) {puts "MAIN_SYNC_BANK $label [get_node_info -name $reg]"}
}
set summary [open [file join $output paths.tsv] w]
puts $summary "corner\tboundary\tkind\tslack_ns\trelationship_ns\tskew_ns\tsetup_start_multicycle\tsetup_end_multicycle\thold_start_multicycle\thold_end_multicycle\tfrom\tto\tfrom_clock\tto_clock"
set geometry [open [file join $output geometry.tsv] w]
puts $geometry "boundary\tlaunch_registers"
foreach label [array names regs] {puts $geometry "$label\t[get_collection_size $regs($label)]"}
puts $geometry "main_all\t[get_collection_size $main]"
puts $geometry "clients_all\t[get_collection_size $clients]"
close $geometry

proc boundary {label args} {
    global output summary corner
    # On a direct related-clock boundary a false path must not silently
    # turn an absent timed report into a passing result.
    foreach kind {setup hold} {
        set cut [get_timing_paths -false_path -$kind -npaths 1 {*}$args]
        if {[get_collection_size $cut]!=0} {error "False-pathed direct boundary: $label ($kind)"}
        set paths [get_timing_paths -$kind -npaths 20 {*}$args]
        if {[get_collection_size $paths]<1} {error "No timed $kind path: $label"}
        report_timing -$kind -npaths 20 -detail full_path {*}$args \
            -file [file join $output corner${corner}-${label}-${kind}.txt]
        foreach_in_collection path $paths {
            set row [list $corner $label $kind]
            foreach field {slack clock_relationship clock_skew setup_start_multicycle setup_end_multicycle hold_start_multicycle hold_end_multicycle} {
                lappend row [get_path_info -$field $path]
            }
            foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $path]]}
            foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $path]]}
            puts $summary [join $row "\t"]
        }
    }
}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach {label clock} [list request-token $three request-data $three request-ack $one response-token $one response-data $one response-ack $three] {
        boundary $label -from $regs($label) -to_clock $clock
    }
    boundary packet-request -from $main -from_clock $three -to $clients -to_clock $three
    boundary packet-response -from $clients -from_clock $three -to $main -to_clock $three
    foreach kind {setup hold} {
        report_timing -$kind -from_clock $cpu -to_clock $cpu -npaths 20 -detail full_path \
            -file [file join $output corner${corner}-cpu-family-${kind}.txt]
    }
    incr corner
}
if {$corner!=4} {error "Expected four operating conditions"}
close $summary
report_clocks -file [file join $output clocks.txt]
report_clock_transfers -file [file join $output clock-transfers.txt]
report_ucp -file [file join $output unconstrained.txt]
report_exceptions -from $main -to_clock $cpu -npaths 20 -file [file join $output main-exceptions.txt]
delete_timing_netlist
project_close -dont_export_assignments
