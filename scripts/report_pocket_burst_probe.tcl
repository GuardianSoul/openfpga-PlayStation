# Read-only physical probe timing audit, from its fitted directory.
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
report_clocks -file probe-clocks.txt
foreach kind {setup hold} {
    report_timing -$kind -npaths 20 -detail full_path -file probe-worst-${kind}.txt
    report_timing -$kind -from [get_ports {cram0_dq[*] cram1_dq[*] cram0_wait cram1_wait}] -to [get_registers {*|pin_dq[*] *|pin_wait[*]}] -npaths 8 -detail full_path -file probe-psram-pad-${kind}.txt
    report_timing -$kind -from [get_ports {dram_dq[*]}] -npaths 8 -detail full_path -file probe-sdram-pad-${kind}.txt
    report_timing -$kind -to [get_clocks {ic|memory|memory_clocks|pll|general[3].gpll~PLL_OUTPUT_COUNTER|divclk}] -npaths 8 -detail full_path -file probe-sample-${kind}.txt
}
report_ucp -file probe-unconstrained.txt
delete_timing_netlist
project_close -dont_export_assignments
