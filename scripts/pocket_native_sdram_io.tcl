# AS4C32M16MSA-6BIN, vendor Rev1.0 p16; Figure2 p9 identifies beat edges.
# The board flight bounds are per leg, unqualified, explicitly saved by caller.
if {[get_collection_size [get_clocks -nowarn pocket_native_sdram_chip]]!=0} {error "Duplicate Native SDRAM pin clock"}
set native_sdram_master [get_clocks {*cpu_pll*general[2]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $native_sdram_master]!=1} {error "Missing unique Native clk3x"}
foreach_in_collection c $native_sdram_master {
    set native_sdram_period [get_clock_info -period $c]
    set native_sdram_source [get_clock_info -targets $c]
}
if {[get_collection_size $native_sdram_source]!=1 || $native_sdram_period<6 || $native_sdram_period>16} {
    error "Unsupported Native SDRAM clock geometry"
}
if {$native_sdram_cas==2} {
    set native_sdram_min_period 12.0;set native_sdram_tac 6.0
} elseif {$native_sdram_cas==3} {
    set native_sdram_min_period 6.0;set native_sdram_tac 5.5
} else {error "Unsupported SDRAM CAS latency"}
set native_sdram_rate_ok [expr {$native_sdram_period >= $native_sdram_min_period}]
puts "NATIVE_SDRAM_RATE CAS=$native_sdram_cas PERIOD=$native_sdram_period MIN=$native_sdram_min_period CONFORMANT=$native_sdram_rate_ok"
if {!$native_sdram_rate_ok && !$native_sdram_analysis_only} {
    error "Native SDRAM CAS/clock violates vendor specification; analysis-only required for diagnostics"
}
if {$native_sdram_board_min<0 || $native_sdram_board_max<$native_sdram_board_min || $native_sdram_board_max>2} {
    error "Invalid Native SDRAM board-flight bounds"
}
set native_sdram_caps [get_registers {*|psx_native_memory:native_memory|psx_sdram_ctrl:controller|dq_in[*]}]
set native_sdram_inputs [get_ports {dram_dq[*]}]
set native_sdram_outputs [get_ports {dram_a[*] dram_ba[*] dram_dqm[*] dram_dq[*] dram_we_n dram_ras_n dram_cas_n dram_cke}]
set native_sdram_pin [get_ports dram_clk]
if {[get_collection_size $native_sdram_caps]!=16 || [get_collection_size $native_sdram_inputs]!=16 ||
    [get_collection_size $native_sdram_outputs]!=37 || [get_collection_size $native_sdram_pin]!=1} {
    error "Missing Native SDRAM capture/pin geometry"
}
# Source collection is the actual clk3x target, not a guessed hierarchy filter.
# DDIO forwards inverted clk3x; propagated clock delay includes its output pad.
create_generated_clock -name pocket_native_sdram_chip -source $native_sdram_source \
    -divide_by 1 -invert $native_sdram_pin
derive_clock_uncertainty
# Read flight includes outgoing clock and returning DQ PCB legs. Write timing
# uses their worst differential skew. No numeric/data path is exempted.
set_input_delay -clock pocket_native_sdram_chip -max [expr {$native_sdram_tac+2*$native_sdram_board_max}] $native_sdram_inputs
set_input_delay -clock pocket_native_sdram_chip -min [expr {2.5+2*$native_sdram_board_min}] $native_sdram_inputs
set_output_delay -clock pocket_native_sdram_chip -max [expr {2.0+$native_sdram_board_max-$native_sdram_board_min}] $native_sdram_outputs
set_output_delay -clock pocket_native_sdram_chip -min [expr {-1.0+$native_sdram_board_min-$native_sdram_board_max}] $native_sdram_outputs
puts "NATIVE_SDRAM_PINS INPUT=16 OUTPUT=37 CAPTURE=16 ANALYSIS_ONLY=$native_sdram_analysis_only"
