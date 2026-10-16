# Raw power reset reaches only explicitly identified asynchronous reset-release
# pipelines. Their local shifts and all downstream reset recovery/removal and
# synchronous reset/CE/data paths retain ordinary timing analysis.
proc reset_release_regs {instance name width} {
    if {$width==1} {
        set patterns [list "${instance}|${name}" "${instance}|${name}~DUPLICATE*"]
        set rx [format {^(.*)\|%s(~DUPLICATE[0-9]*)?$} $name]
    } else {
        set patterns [list [format {%s|%s[*]} $instance $name] [format {%s|%s[*]~DUPLICATE*} $instance $name]]
        set rx [format {^(.*)\|%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $name]
    }
    set nodes [get_registers -nowarn $patterns]
    set bits {}; set owners {}
    foreach_in_collection node $nodes {
        set physical [get_node_info -name $node]
        if {$width==1} {
            if {![regexp $rx $physical ignored owner replica]} {error "Unexpected reset scalar $physical"}
            set bit 0
        } else {
            if {![regexp $rx $physical ignored owner bit replica]} {error "Unexpected reset bus $physical"}
        }
        lappend owners $owner; lappend bits $bit
    }
    set expected {}; for {set i 0} {$i<$width} {incr i} {lappend expected $i}
    if {[lsort -unique $owners] ne [list $instance] || [lsort -integer -unique $bits] ne $expected} {
        error "Missing/ambiguous reset geometry $instance $name: $owners $bits"
    }
    return $nodes
}

set reset_power_source [reset_release_regs core_top:ic power_reset_n 1]
foreach {instance name} {
    core_top:ic|psx_mem_cdc:loader_cdc src_reset_pipe
    core_top:ic|psx_mem_cdc:loader_cdc dst_reset_pipe
    core_top:ic|psx_boot_gate:boot_gate cpu_transport_reset_pipe
    core_top:ic|psx_boot_gate:boot_gate host_transport_reset_pipe
} {
    set reset_boundary [reset_release_regs $instance $name 3]
    set_false_path -from $reset_power_source -to $reset_boundary
    puts "RESET_RELEASE_BOUNDARY $instance $name [get_collection_size $reset_boundary]"
}
