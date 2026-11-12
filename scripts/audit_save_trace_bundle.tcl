# Trace-only frozen metadata: two read periods before a word is sampled.
# Run exclusively through Jenkins, against its private fitted copy.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 1} {error "Expected fresh report directory"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh trace audit directory required"}
file mkdir $output
proc trace_exact {name} {
    set regs [get_registers -no_duplicates -nowarn [list $name]]
    if {[get_collection_size $regs]!=1} {error "Missing exact trace register $name"}
    foreach_in_collection r $regs {
        if {[get_node_info -name $r] ne $name} {error "Trace register alias changed $name"}
    }
    return $regs
}
project_open apf_top
try {
    create_timing_netlist
    read_sdc
    set patterns {}
    foreach name {triggered entered returned resumed frozen export_stop trigger_cycle stop_cycle pre_count pre_next first_count late_count late_next entry_epc trigger_keys trigger_sticks} {
        if {$name in {triggered entered returned resumed frozen export_stop}} {
            lappend patterns "*|save_trace|$name" "*|save_trace|$name~DUPLICATE*"
        } else {lappend patterns [format {*|save_trace|%s[*]} $name] [format {*|save_trace|%s[*]~DUPLICATE*} $name]}
    }
    set starts [get_registers $patterns]
    set ends [get_registers {*|save_trace|header_q[*]*}]
    set selected {};set logical {}
    foreach_in_collection r $starts {
        set name [get_node_info -name $r]
        lappend selected $name
        lappend logical [regsub {~DUPLICATE.*$} $name {}]
    }
    set logical [lsort -unique $logical]
    set sources [llength $logical]
    if {$sources < 150 || $sources > 208 || [get_collection_size $ends] < 32} {
        error "Unexpected trace metadata geometry $sources/[get_collection_size $ends]"
    }
    set f [open [file join $output paths.tsv] w]
    puts $f "corner\tdata_ns\tslack_ns\tfrom\tto"
    set corners 0; set paths 0; set worst 0
    foreach_in_collection cond [get_available_operating_conditions] {
        set_operating_conditions $cond
        update_timing_netlist
        set covered {}
        foreach_in_collection target $ends {
          set tn [get_node_info -name $target]
          set to [trace_exact $tn]
          foreach_in_collection source [get_fanins -synch $to] {
            set sn [get_node_info -name $source]
            if {$sn ni $selected} {continue}
            lappend covered [regsub {~DUPLICATE.*$} $sn {}]
            # Convert each physical name to a singleton collection. Iterated
            # reg_* node handles are not accepted by get_fanins/get_timing_paths.
            set from [trace_exact $sn]
            if {[get_collection_size [get_timing_paths -false_path -setup -from $from -to $to -npaths 1]]} {
                error "Hidden/unconstrained trace physical pair $sn -> $tn"
            }
            set found 0
            foreach_in_collection p [get_timing_paths -setup -from $from -to $to -npaths 1] {
                incr found; incr paths
                if {[get_node_info -name [get_path_info -from $p]] ne $sn ||
                    [get_node_info -name [get_path_info -to $p]] ne $tn} {
                    error "Trace physical pair changed"
                }
                set delay [get_path_info -data_delay $p]
                set launch [get_clock_info -name [get_path_info -from_clock $p]]
                set latch [get_clock_info -name [get_path_info -to_clock $p]]
                set period [get_clock_info -period [get_path_info -to_clock $p]]
                if {$launch ne {ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk} || $latch ne "clk_74a"} {
                    error "Unexpected trace bundle clocks $launch -> $latch"
                }
                if {$delay < 0 || $delay > 20 || 20 >= 2*$period} {
                    error "Trace bundle exceeds frozen window: $delay ns, read period $period"
                }
                if {$delay > $worst} {set worst $delay}
                puts $f [join [list $corners $delay [get_path_info -slack $p] $sn $tn] "\t"]
            }
            if {$found != 1} {error "Hidden/unconstrained trace physical pair $sn -> $tn"}
          }
        }
        if {[lsort -unique $covered] ne $logical} {
            set missing {};foreach bit $logical {if {$bit ni $covered} {lappend missing $bit}}
            error "Trace logical metadata fanin coverage incomplete: $missing"
        }
        incr corners
    }
    close $f
    if {$corners != 4} {error "Expected four trace corners, found $corners"}
    puts "PASS_SAVE_TRACE_BUNDLE corners4 sources$sources paths$paths worst_ns=$worst bound_ns=20"
    delete_timing_netlist
} finally {project_close -dont_export_assignments}
