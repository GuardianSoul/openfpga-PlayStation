# Run with quartus_sta -t from a fitted standalone SPU resource directory.
# All clocks/constraints come from that project's SDC; no exceptions are added.
project_open psx_mister_spu_resource
create_timing_netlist
read_sdc
update_timing_netlist
set contexts [get_registers {*context_table* *iEnvCounterRam*}]
set count [get_collection_size $contexts]
if {$count == 0} {error "No fitted SPU context registers found"}
puts "SPU_CONTEXT_REGISTER_COUNT $count"
report_timing -setup -from $contexts -npaths 12 -detail full_path -file context-from-setup.rpt
report_timing -setup -to $contexts -npaths 12 -detail full_path -file context-to-setup.rpt
report_timing -hold -from $contexts -npaths 12 -detail full_path -file context-from-hold.rpt
report_timing -hold -to $contexts -npaths 12 -detail full_path -file context-to-hold.rpt
delete_timing_netlist
project_close
