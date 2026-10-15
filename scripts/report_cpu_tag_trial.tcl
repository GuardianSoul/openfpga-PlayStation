# Read-only audit, no added timing exceptions. Run from the fitted CPU directory.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 1} {error "Expected audit output directory"}
set output_dir [file normalize [lindex $quartus(args) 0]]
file mkdir $output_dir
project_open psx_mister_cpu_resource
try {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    set tags [get_registers {*itagram*}]
    set count [get_collection_size $tags]
    if {$count == 0} {error "No fitted CPU tag RAM registers found"}
    puts "CPU_TAG_REGISTER_COUNT $count"
    foreach_in_collection node $tags { puts "CPU_TAG_REGISTER [get_register_info -name $node]" }
    set corner 0
    foreach_in_collection op [get_available_operating_conditions] {
        set_operating_conditions $op
        update_timing_netlist
        puts "CPU_TAG_CORNER $corner [get_operating_conditions_info $op -model]"
        foreach kind {setup hold} {
            report_timing -$kind -from $tags -npaths 12 -detail full_path -file $output_dir/corner${corner}-from-${kind}.txt
            report_timing -$kind -to $tags -npaths 12 -detail full_path -file $output_dir/corner${corner}-to-${kind}.txt
        }
        incr corner
    }
    delete_timing_netlist
} finally {
    project_close -dont_export_assignments
}
