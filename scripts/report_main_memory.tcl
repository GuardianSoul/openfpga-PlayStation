# Read-only, all-corner audit of native packet/ownership + real PSRAM pins.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 1} {error "Expected audit directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open psx_main_memory_resource
set status [catch {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    foreach {label pattern} {
        packet {*|*packet*|*}
        arbiter {*|*arb*|*}
        owner {*|boot_owned}
        pin_sample {*|pin_dq[*] *|pin_wait[*]}
        pin_stage {*|i_dq[*] *|i_wait[*]}
    } {
        set nodes($label) [get_registers $pattern]
        if {[get_collection_size $nodes($label)] == 0} {error "Missing main-memory $label scope"}
        puts "MAIN_SCOPE $label [get_collection_size $nodes($label)]"
    }
    report_clocks -file $output/clocks.txt
    set corner 0
    foreach_in_collection op [get_available_operating_conditions] {
        set_operating_conditions $op
        update_timing_netlist
        puts "MAIN_CORNER $corner [get_operating_conditions_info $op -model]"
        foreach kind {setup hold} {
            report_timing -$kind -from $nodes(packet) -to $nodes(packet) -npaths 16 -detail full_path \
                -file $output/corner${corner}-packet-${kind}.txt
            report_timing -$kind -from $nodes(arbiter) -to $nodes(arbiter) -npaths 16 -detail full_path \
                -file $output/corner${corner}-arbiter-${kind}.txt
            report_timing -$kind -to $nodes(owner) -npaths 12 -detail full_path \
                -file $output/corner${corner}-owner-${kind}.txt
            report_timing -$kind -from [get_ports {cram0_dq[*] cram1_dq[*] cram0_wait cram1_wait}] \
                -to $nodes(pin_sample) -npaths 8 -detail full_path -file $output/corner${corner}-pad-${kind}.txt
            report_timing -$kind -from $nodes(pin_sample) -to $nodes(pin_stage) -npaths 8 -detail full_path \
                -file $output/corner${corner}-sample-stage-${kind}.txt
            report_timing -$kind -npaths 24 -detail full_path -file $output/corner${corner}-global-${kind}.txt
        }
        report_ucp -file $output/corner${corner}-unconstrained.txt
        incr corner
    }
} result]
catch {delete_timing_netlist}
project_close -dont_export_assignments
if {$status} {error $result}
