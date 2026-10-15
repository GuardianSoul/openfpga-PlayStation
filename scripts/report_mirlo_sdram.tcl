# Read-only audit of the ordinary probe's three-output memory PLL.
# Run from the fitted project: quartus_sta -t <script> <new output directory>.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 1} {error "Expected one audit output directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set source [get_pins {ic|memory|memory_clocks|pll|general[2].gpll~PLL_OUTPUT_COUNTER|divclk}]
if {[get_collection_size $source] != 1} {error "Expected the shifted PLL output"}
set forwarded 0
foreach_in_collection node [get_fanouts $source] {
    if {[get_node_info -name $node] eq "dram_clk"} {set forwarded 1}
}
if {!$forwarded} {error "SDRAM generated-clock source does not drive dram_clk"}
puts "MIRLO_FORWARDED_CLOCK_PARENT [get_pin_info -name $source]"
report_clocks -file [file join $output clocks.txt]
report_ucp -file [file join $output unconstrained.txt]
set inputs [get_ports {dram_dq[*]}]
set outputs [get_ports {dram_a[*] dram_ba[*] dram_dqm[*] dram_dq[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]
set samples [get_registers {*|in2[*]}]
puts "MIRLO_SCOPE inputs [get_collection_size $inputs] samples [get_collection_size $samples] outputs [get_collection_size $outputs]"
if {[get_collection_size $inputs] != 16 || [get_collection_size $samples] < 16} {
    error "Missing SDRAM input/sample path"
}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    puts "MIRLO_CORNER $corner [get_operating_conditions_info -display_name $condition]"
    foreach kind {setup hold} {
        report_timing -$kind -npaths 12 -detail full_path -file [file join $output corner${corner}-global-${kind}.txt]
        report_timing -$kind -from $inputs -to $samples -npaths 8 -detail full_path -file [file join $output corner${corner}-input-${kind}.txt]
        report_timing -$kind -to $outputs -npaths 8 -detail full_path -file [file join $output corner${corner}-output-${kind}.txt]
    }
    incr corner
}
if {$corner != 4} {error "Expected four available operating corners"}
delete_timing_netlist
project_close -dont_export_assignments
