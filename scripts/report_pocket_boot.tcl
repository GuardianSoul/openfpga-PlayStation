# Read-only connected release boot audit. No new timing exceptions.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 1} {error "Expected audit directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
set status [catch {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    foreach {label pattern expected} {
        freeze_meta {*|*boot_gate*|freeze_meta} 1
        freeze_sync {*|*boot_gate*|freeze_sync} 1
        ack_cpu {*|*boot_gate*|ack_cpu} 1
        ack_meta {*|*boot_gate*|ack_meta} 1
        ack_sync {*|*boot_gate*|ack_sync} 1
        lock_meta {*|*boot_gate*|lock_meta} 1
        lock_sync {*|*boot_gate*|lock_sync} 1
        reset_pipe {*|*boot_gate*|reset_pipe[*]*} 3
        reset_seen {*|*boot_gate*|reset_seen[*]} 3
        owner_meta {*|bios_ready_meta} 1
        owner_sync {*|bios_ready_cpu*} 1
    } {
        set nodes($label) [get_registers $pattern]
        set count [get_collection_size $nodes($label)]
        puts "BOOT_SCOPE $label $count"
        set logical {}
        set instances {}
        foreach_in_collection node $nodes($label) {
            set name [get_node_info -name $node]
            puts "BOOT_NODE $label $name"
            if {$label in {reset_pipe reset_seen}} {
                set expression [format {^(.*)\|%s\[([0-2])\](~DUPLICATE[0-9]*)?$} $label]
                if {![regexp $expression $name ignored instance bit replica]} {
                    error "Unrecognized reset stage node: $name"
                }
                lappend instances $instance
                lappend logical $bit
            } else {
                set leaf $label
                if {$label eq "owner_sync"} {set leaf bios_ready_cpu}
                if {$label eq "owner_meta"} {set leaf bios_ready_meta}
                set expression [format {^(.*)\|%s(~DUPLICATE[0-9]*)?$} $leaf]
                if {![regexp $expression $name ignored instance replica]} {
                    error "Unrecognized boot node: $name"
                }
                lappend instances $instance
                lappend logical 0
            }
        }
        set wanted [expr {$expected == 3 ? "0 1 2" : "0"}]
        if {[lsort -integer -unique $logical] ne $wanted || [llength [lsort -unique $instances]] != 1} {
            error "Missing/ambiguous logical $label stages"
        }
        if {$label in {freeze_meta ack_meta lock_meta owner_meta} && $count != 1} {
            error "Duplicated first-stage boot synchronizer $label"
        }
    }
    set nodes(loader) [get_registers {*|*bios_loader*|*}]
    set nodes(gate) [get_registers {*|*boot_gate*|*}]
    if {[get_collection_size $nodes(loader)] == 0} {error "Missing release BIOS loader"}
    puts "BOOT_LOADER_REGISTERS [get_collection_size $nodes(loader)]"
    set corner 0
    foreach_in_collection op [get_available_operating_conditions] {
        set_operating_conditions $op
        update_timing_netlist
        puts "BOOT_CORNER $corner [get_operating_conditions_info $op -model]"
        foreach kind {setup hold recovery removal} {
            report_timing -$kind -from $nodes(gate) -to $nodes(gate) -npaths 16 -detail full_path \
                -file $output/corner${corner}-gate-local-${kind}.txt
            report_timing -$kind -from $nodes(loader) -to $nodes(loader) -npaths 12 -detail full_path \
                -file $output/corner${corner}-loader-local-${kind}.txt
            report_timing -$kind -from $nodes(reset_pipe) -npaths 16 -detail full_path \
                -file $output/corner${corner}-reset-consumers-${kind}.txt
        }
        foreach kind {setup hold} {
            foreach {from to} {freeze_meta freeze_sync ack_meta ack_sync lock_meta lock_sync owner_meta owner_sync} {
                report_timing -$kind -from $nodes($from) -to $nodes($to) -npaths 4 -detail full_path \
                    -file $output/corner${corner}-${from}-${kind}.txt
            }
            report_timing -$kind -from $nodes(owner_sync) -npaths 12 -detail full_path \
                -file $output/corner${corner}-memory-owner-${kind}.txt
            report_timing -$kind -npaths 24 -detail full_path \
                -file $output/corner${corner}-global-${kind}.txt
        }
        incr corner
    }
} result]
catch {delete_timing_netlist}
project_close -dont_export_assignments
if {$status} {error $result}
