# Run only in an analysis COPY of a completed reset-release full-top fit.
# Checks ordinary local shift and downstream paths at every device corner.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {
    error "Expected output directory and optional --fault-local-cut"
}
set output [file normalize [lindex $quartus(args) 0]]
set fault 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--fault-local-cut"} {error "Unknown reset audit option"}
    set fault 1
}
file mkdir $output
project_open apf_top
try {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    if {![llength [info procs reset_release_regs]]} {error "Reset release scope missing"}
    set pipelines {
        core_top:ic|psx_mem_cdc:loader_cdc src_reset_pipe
        core_top:ic|psx_mem_cdc:loader_cdc dst_reset_pipe
        core_top:ic|psx_boot_gate:boot_gate cpu_transport_reset_pipe
        core_top:ic|psx_boot_gate:boot_gate host_transport_reset_pipe
    }
    if {$fault} {
        # Must reject a hidden LOCAL shift, not merely an empty raw-reset path.
        set bank [reset_release_regs {core_top:ic|psx_mem_cdc:loader_cdc} dst_reset_pipe 3]
        set_false_path -from $bank -to $bank
        update_timing_netlist
    }
    set table [open [file join $output paths.tsv] w]
    puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
    set corner 0
    set violations {}
    foreach_in_collection condition [get_available_operating_conditions] {
        set_operating_conditions $condition
        update_timing_netlist
        foreach {instance stem} $pipelines {
            set bank [reset_release_regs $instance $stem 3]
            # Every physical nonzero bit must retain an ordinary setup/hold
            # path from the previous local stage, with no multicycle/false cut.
            foreach_in_collection node $bank {
                set name [get_node_info -name $node]
                if {![regexp {\[([0-2])\](~DUPLICATE[0-9]*)?$} $name ignored bit replica]} {
                    error "Unknown reset pipe physical bit $name"
                }
                if {$bit==0} {continue}
                set prior [expr {$bit-1}]
                set from [get_registers -nowarn [list [format {%s|%s[%d]} $instance $stem $prior] [format {%s|%s[%d]~DUPLICATE*} $instance $stem $prior]]]
                set to [get_registers -no_duplicates -nowarn [list $name]]
                if {[get_collection_size $to]!=1 || [get_collection_size $from]<1} {error "Missing reset pipe stage $name"}
                foreach kind {setup hold} {
                    set paths [get_timing_paths -$kind -from $from -to $to -npaths 100]
                    if {[get_collection_size $paths]<1} {error "Hidden local reset shift $name $kind"}
                    foreach_in_collection p $paths {
                        set launch [get_node_info -name [get_path_info -from $p]]
                        set capture [get_node_info -name [get_path_info -to $p]]
                        set from_clock [get_clock_info -name [get_path_info -from_clock $p]]
                        set to_clock [get_clock_info -name [get_path_info -to_clock $p]]
                        set lc [get_path_info -from_clock $p]
                        set period [get_clock_info -period $lc]
                        set relation [get_path_info -clock_relationship $p]
                        set multicycles {}
                        foreach field {setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle} {
                            lappend multicycles [get_path_info -$field $p]
                        }
                        set expected [expr {$kind eq "setup" ? $period : 0.0}]
                        if {$from_clock ne $to_clock || $multicycles ne {1 1 0 0} || abs($relation-$expected)>.002} {
                            error "Relaxed/nonlocal reset shift $launch -> $capture $kind"
                        }
                        set slack [get_path_info -slack $p]
                        if {$slack<=0} {lappend violations "$corner/$name/$kind/$slack"}
                        puts $table [join [list $corner shift $kind $slack $launch $capture $from_clock $to_clock] "\t"]
                    }
                }
            }
            # All timed downstream controls are reported (CD reset is
            # synchronous; mailbox/gate reset uses recovery/removal checks).
            set terminal [get_registers -nowarn [list [format {%s|%s[2]} $instance $stem] [format {%s|%s[2]~DUPLICATE*} $instance $stem]]]
            foreach kind {setup hold recovery removal} {
                set paths [get_timing_paths -$kind -from $terminal -npaths 200]
                if {$kind eq "setup" && [string match *_reset_pipe $stem] && ![string match *_transport_* $stem] && [get_collection_size $paths]<1} {
                    error "Missing shared synchronous CD reset consumers $stem"
                }
                if {$kind eq "recovery" && [get_collection_size $paths]<1} {
                    error "Missing local reset recovery consumers $stem"
                }
                foreach_in_collection p $paths {
                    set slack [get_path_info -slack $p]
                    set row [list $corner "$instance/$stem" $kind $slack]
                    if {$slack<=0} {lappend violations "$corner/$instance/$stem/$kind/$slack"}
                    foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $p]]}
                    foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $p]]}
                    puts $table [join $row "\t"]
                }
                report_timing -$kind -from $terminal -npaths 100 -detail full_path \
                    -file [file join $output "corner${corner}-${stem}-${kind}.txt"]
            }
        }
        # Former broad-cut source is now allowed only at explicit boundaries.
        # Its same-host-clock consumers must still have ordinary timed paths.
        set power [reset_release_regs core_top:ic power_reset_n 1]
        foreach kind {setup hold recovery removal} {
            set paths [get_timing_paths -$kind -from $power -npaths 200]
            if {$kind in {setup hold} && [get_collection_size $paths]<1} {error "Power origin still broadly cut"}
            foreach_in_collection p $paths {
                set slack [get_path_info -slack $p]
                set row [list $corner power $kind $slack]
                if {$slack<=0} {lappend violations "$corner/power/$kind/$slack"}
                foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $p]]}
                foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $p]]}
                puts $table [join $row "\t"]
            }
        }
        incr corner
    }
    close $table
    if {$corner!=4} {error "Expected four reset timing corners"}
    if {[llength $violations]} {error "Reset timing is not strictly positive: $violations"}
    if {$fault} {error "Injected local reset cut escaped audit"}
    puts "PASS_RESET_RELEASE_ALL_CORNERS"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
