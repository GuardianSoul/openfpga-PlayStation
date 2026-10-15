# quartus_sta -t <absolute-script-path>, from the fitted fabric directory.
# Use the project's unchanged SDC and default Slow 1100mV 85C corner.
project_open psx_memory_fabric_resource
create_timing_netlist
read_sdc
update_timing_netlist
foreach {label pattern} {capture {*|in2[*]} stage {*|f_rddata[*]} delay {*|rddata_d[*]} client {*|rddata[*]}} {
    set registers [get_registers $pattern]
    set count [get_collection_size $registers]
    puts "PIPELINE_REGISTERS $label $count"
    if {$count == 0} {error "Missing pipeline stage $label"}
    set stages($label) $registers
}
foreach {from to} {capture stage stage delay stage client} {
    report_timing -setup -from $stages($from) -to $stages($to) -npaths 16 -detail full_path -file $from-$to-setup.rpt
    report_timing -hold -from $stages($from) -to $stages($to) -npaths 16 -detail full_path -file $from-$to-hold.rpt
}
delete_timing_netlist
# Standalone audit must not add LAST_QUARTUS_VERSION to the compiled QSF.
project_close -dont_export_assignments
