# Private routed DB only: quartus_sta -t <script> <fresh output directory>.
# Keep first-stage asynchronous failures visible; no exception is selected here.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected audit directory and optional --request-fifo"}
set request_fifo 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--request-fifo"} {error "Unknown reverse audit option"}
    set request_fifo 1
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh reverse-video audit output required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set prefix {*|gpu_videoout_async:igpu_videoout_async|}
set report_keys {hblank_tmr dotclock inVsync irq_VBLANK activeLineLSB GPUSTAT_InterlaceField GPUSTAT_DrawingOddline}
set request_keys {fetch}
for {set i 0} {$i<10} {incr i} {lappend request_keys [format {fetchsize[%d]} $i]}
for {set i 0} {$i<9} {incr i} {lappend request_keys [format {lineInNext[%d]} $i]}
proc return_regs {stem keys} {
    global prefix
    if {$keys eq {scalar}} {set pattern "${prefix}${stem}*"} else {set pattern "${prefix}${stem}.*"}
    set nodes [get_registers -nowarn $pattern];set actual {};set instances {}
    foreach_in_collection node $nodes {
        set name [get_node_info -name $node]
        if {$keys eq {scalar}} {
            if {![regexp [format {^(.*)\|%s(~DUPLICATE[0-9]*)?$} $stem] $name ignored inst replica]} {
                error "Unexpected scalar return stage $name"
            }
            lappend actual scalar
        } else {
            if {![regexp [format {^(.*)\|%s\.([A-Za-z0-9_]+(\[[0-9]+\])?)(~DUPLICATE[0-9]*)?$} $stem] $name ignored inst key index replica]} {
                error "Reverse stage is not a plain FF record: $name"
            }
            lappend actual $key
        }
        lappend instances $inst
    }
    if {[llength [lsort -unique $instances]]!=1 || [lsort -unique $actual] ne [lsort $keys]} {
        error "Missing reverse FF stage $stem: $actual (expected $keys)"
    }
    puts "RETURN_FF $stem logical=[llength $keys] physical=[get_collection_size $nodes]"
    return $nodes
}
set clocks [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $clocks]!=3} {error "Expected related CPU clock family"}
foreach {name pattern} {cpu1 {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk} cpu2 {*cpu_pll*general[1]*OUTPUT_COUNTER*divclk} video {*video_pll*general[0]*OUTPUT_COUNTER*divclk}} {
    set c [get_clocks $pattern]
    if {[get_collection_size $c]!=1} {error "Ambiguous reverse-video clock $name"}
    foreach_in_collection clock $c {set clock_name($name) [get_clock_info -name $clock]}
}
set reports [list [return_regs videoout_reports_s1 $report_keys] [return_regs videoout_reports_s2 $report_keys] [return_regs videoout_reports_1x $report_keys]]
if {$request_fifo} {
    if {[llength [info commands request_regs]]!=1} {error "Request FIFO constraints not loaded"}
    request_regs {dual_clock_fifo:snapshots|wr_addr_gray_rd[*]} 4
    request_regs {dual_clock_fifo:snapshots|rd_addr_gray_wr[*]} 4
    if {[get_collection_size [get_registers -nowarn [list ${prefix}videoout_request_s1* ${prefix}videoout_request_s2*]]]!=0} {
        error "Old numeric request samplers remain"
    }
} else {
    set requests [list [return_regs videoout_request_s1 $request_keys] [return_regs videoout_request_s2 $request_keys] [return_regs videoout_request_2x $request_keys]]
}
set unpause [list [return_regs allowunpause_s1 scalar] [return_regs allowunpause_s2 scalar] [return_regs allowunpause1x scalar]]
set ram [get_registers -nowarn "${prefix}*altshift_taps:*videoout_request*"]
if {[get_collection_size $ram]!=0} {error "Reverse request sampler still contains shift RAM"}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
proc return_paths {label args} {
    global output table corner
    foreach kind {setup hold} {
        if {[get_collection_size [get_timing_paths -false_path -$kind {*}$args -npaths 1]]!=0} {
            error "Reverse-video path hidden by an exception ($label $kind)"
        }
        set paths [get_timing_paths -$kind {*}$args -npaths 100]
        if {[get_collection_size $paths]==0} {error "No timed reverse-video paths $label $kind"}
        foreach_in_collection p $paths {
            set row [list $corner $label $kind [get_path_info -slack $p]]
            foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $p]]}
            foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $p]]}
            puts $table [join $row "\t"]
        }
        report_timing -$kind {*}$args -npaths 100 -detail full_path -file [file join $output corner${corner}-${label}-${kind}.txt]
    }
}
set corner 0
set groups [list reports $reports cpu1 unpause $unpause cpu1]
if {!$request_fifo} {lappend groups request $requests cpu2}
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach {label stages domain} $groups {
        lassign $stages first second third
        return_paths ${label}-first -to $first
        # All inputs to these two stages must come from their predecessor.
        foreach {stage source target} [list second $first $second third $second $third] {
            foreach kind {setup hold} {
                set paths [get_timing_paths -$kind -to $target -npaths 100]
                foreach_in_collection p $paths {
                    set origin [get_node_info -name [get_path_info -from $p]]
                    set found 0
                    foreach_in_collection n $source {if {[get_node_info -name $n] eq $origin} {set found 1}}
                    if {!$found} {error "Unexpected reverse-stage fan-in $label $stage: $origin"}
                    foreach field {from_clock to_clock} {
                        if {[get_clock_info -name [get_path_info -$field $p]] ne $clock_name($domain)} {
                            error "Wrong local reverse-stage clock $label $stage"
                        }
                    }
                }
            }
            return_paths ${label}-${stage} -to $target
        }
        return_paths ${label}-consumers -from $third -to_clock $clocks
    }
    report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four reverse-video conditions"}
close $table
delete_timing_netlist
project_close -dont_export_assignments
