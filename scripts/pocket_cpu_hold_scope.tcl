# Strengthen all three related CPU-clock pairs; retain device uncertainty.
# The observed APF candidate hold miss is 2x -> 1x, so same-clock-only margin
# cannot cover it. No false path, multicycle, rate or phase change.
# A fourth output (general[3]) at the clk2x period (--vram-full-rate) or the
# clk3x period (--vram-x3) is the PSRAM chip clock; it only clocks the
# chip-clock DDIO, so it stays out.
if {![info exists pocket_cpu_hold_ns] || $pocket_cpu_hold_ns<0.05 || $pocket_cpu_hold_ns>0.5} {error "Expected .05..0.5ns additional CPU hold margin"}
set pocket_cpu_hold_all [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
set pocket_cpu_hold_indices {};set pocket_cpu_hold_families {};set pocket_cpu_hold_masters {};set pocket_cpu_hold_names {};set pocket_cpu_hold_extra 0
foreach_in_collection c $pocket_cpu_hold_all {
    set name [get_clock_info -name $c]
    if {![regexp {^(.*cpu_pll)\|general\[([0-3])\]\.gpll~PLL_OUTPUT_COUNTER\|divclk$} $name -> family index]} {error "Unknown CPU hold clock $name"}
    set period [get_clock_info -period $c]
    lappend pocket_cpu_hold_families $family;lappend pocket_cpu_hold_masters [get_clock_info -master_clock $c]
    if {$index==3} {incr pocket_cpu_hold_extra;set pocket_cpu_hold_extra_period $period;continue}
    set expected [lindex {29.5251 14.76255 9.8417} $index]
    if {abs($period-$expected)>0.003} {error "CPU hold clock period changed: $name $period"}
    if {$index==1} {set pocket_cpu_hold_2x_period $period}
    if {$index==2} {set pocket_cpu_hold_3x_period $period}
    lappend pocket_cpu_hold_indices $index;lappend pocket_cpu_hold_names $name
}
if {[llength $pocket_cpu_hold_indices]!=3 || $pocket_cpu_hold_extra>1} {error "Expected three related CPU clocks"}
if {$pocket_cpu_hold_extra && abs($pocket_cpu_hold_extra_period-$pocket_cpu_hold_2x_period)>0.003 && abs($pocket_cpu_hold_extra_period-$pocket_cpu_hold_3x_period)>0.003} {error "VRAM chip clock period is neither clk2x nor clk3x: $pocket_cpu_hold_extra_period"}
if {[lsort -integer $pocket_cpu_hold_indices] ne {0 1 2} || [llength [lsort -unique $pocket_cpu_hold_families]]!=1 || [llength [lsort -unique $pocket_cpu_hold_masters]]!=1} {error "CPU hold clocks do not share one reviewed PLL family"}
foreach from $pocket_cpu_hold_names {
    foreach to $pocket_cpu_hold_names {
        set_clock_uncertainty -add -hold -enable_same_physical_edge \
            -from [get_clocks $from] -to [get_clocks $to] $pocket_cpu_hold_ns
    }
}
puts "POCKET_CPU_HOLD_MARGIN $pocket_cpu_hold_ns add_derived all9_related_pairs same_physical_edge"
