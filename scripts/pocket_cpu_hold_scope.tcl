# Strengthen all three related CPU-clock pairs; retain device uncertainty.
# The observed APF candidate hold miss is 2x -> 1x, so same-clock-only margin
# cannot cover it. No false path, multicycle, rate or phase change.
if {![info exists pocket_cpu_hold_ns] || $pocket_cpu_hold_ns<0.05 || $pocket_cpu_hold_ns>0.5} {error "Expected .05..0.5ns additional CPU hold margin"}
set pocket_cpu_hold_clocks [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $pocket_cpu_hold_clocks]!=3} {error "Expected three related CPU clocks"}
set pocket_cpu_hold_indices {};set pocket_cpu_hold_families {};set pocket_cpu_hold_masters {}
foreach_in_collection c $pocket_cpu_hold_clocks {
    set name [get_clock_info -name $c]
    if {![regexp {^(.*cpu_pll)\|general\[([0-2])\]\.gpll~PLL_OUTPUT_COUNTER\|divclk$} $name -> family index]} {error "Unknown CPU hold clock $name"}
    set expected [lindex {29.5251 14.76255 9.8417} $index]
    if {abs([get_clock_info -period $c]-$expected)>0.003} {error "CPU hold clock period changed"}
    lappend pocket_cpu_hold_indices $index;lappend pocket_cpu_hold_families $family;lappend pocket_cpu_hold_masters [get_clock_info -master_clock $c]
}
if {[lsort -integer $pocket_cpu_hold_indices] ne {0 1 2} || [llength [lsort -unique $pocket_cpu_hold_families]]!=1 || [llength [lsort -unique $pocket_cpu_hold_masters]]!=1} {error "CPU hold clocks do not share one reviewed PLL family"}
set_clock_uncertainty -add -hold -enable_same_physical_edge \
    -from $pocket_cpu_hold_clocks -to $pocket_cpu_hold_clocks $pocket_cpu_hold_ns
puts "POCKET_CPU_HOLD_MARGIN $pocket_cpu_hold_ns add_derived all9_related_pairs same_physical_edge"
