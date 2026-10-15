# Run from the component's fitted directory. This audit does not save changes.
package require ::quartus::project
package require ::quartus::sta
project_open psx_psram_burst_resource
create_timing_netlist
read_sdc
update_timing_netlist
report_clocks -file psram-clocks.txt
foreach kind {setup hold} {
    report_timing -$kind -npaths 20 -detail full_path -file psram-worst-${kind}.txt
    report_timing -$kind -from [get_ports {cram0_dq[*] cram1_dq[*] cram0_wait cram1_wait}] -to [get_registers {*|pin_dq[*] *|pin_wait[*]}] -npaths 8 -detail full_path -file psram-pad-${kind}.txt
    report_timing -$kind -from [get_registers {*|pin_dq[*] *|pin_wait[*]}] -to [get_registers {*|i_dq[*] *|i_wait[*]}] -npaths 8 -detail full_path -file psram-stage-${kind}.txt
    report_timing -$kind -to [get_ports {cram0_a[*] cram1_a[*] cram0_dq[*] cram1_dq[*] cram0_adv_n cram1_adv_n cram0_cre cram1_cre cram0_ce0_n cram1_ce0_n cram0_ce1_n cram1_ce1_n cram0_oe_n cram1_oe_n cram0_we_n cram1_we_n cram0_lb_n cram1_lb_n cram0_ub_n cram1_ub_n}] -npaths 8 -detail full_path -file psram-output-${kind}.txt
}
report_ucp -file psram-unconstrained.txt
delete_timing_netlist
project_close -dont_export_assignments
