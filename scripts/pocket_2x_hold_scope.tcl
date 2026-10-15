# Additional physical implementation margin; no path is hidden or relaxed.
# -add preserves the device-derived uncertainty. Same-edge hold checks need
# -enable_same_physical_edge explicitly (Quartus 25.1 SDC command contract).
if {![info exists pocket_2x_hold_ns] || $pocket_2x_hold_ns<0.05 || $pocket_2x_hold_ns>0.5} {
    error {Expected a reviewed 0.05..0.5ns additional same-clock hold margin}
}
set pocket_2x_hold_clock [get_clocks {*cpu_pll*general[1]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $pocket_2x_hold_clock]!=1} {
    error {Missing or ambiguous CPU 2x hold-margin clock}
}
foreach_in_collection c $pocket_2x_hold_clock {
    if {abs([get_clock_info -period $c]-14.76255)>0.003} {
        error {Hold margin requires the reviewed 67.7376MHz CPU 2x clock}
    }
}
set_clock_uncertainty -add -hold -enable_same_physical_edge \
    -from $pocket_2x_hold_clock -to $pocket_2x_hold_clock $pocket_2x_hold_ns
puts "POCKET_2X_HOLD_MARGIN $pocket_2x_hold_ns add_derived same_physical_edge"
