# Only this exact stable-level snapshot geometry is supported by these bounds.
# A published payload is held until the destination commits and the ACK makes
# two source synchronizer hops. Capture occurs after two destination request
# synchronizer hops, then a local edge: >=2 destination periods after launch.
proc config_regs {name width} {
    set scope {*|psx_config_cdc:host_config|}
    if {$width==1} {
        set patterns [list "${scope}${name}" "${scope}${name}~DUPLICATE*"]
    } else {
        set patterns [list [format {%s%s[*]} $scope $name] [format {%s%s[*]~DUPLICATE*} $scope $name]]
    }
    set nodes [get_registers -nowarn $patterns]
    set bits {}; set instances {}
    if {$width==1} {
        set rx [format {^(.*)\|%s(~DUPLICATE[0-9]*)?$} $name]
    } else {
        set rx [format {^(.*)\|%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $name]
    }
    foreach_in_collection node $nodes {
        set physical [get_node_info -name $node]
        if {$width==1} {
            if {![regexp $rx $physical ignored instance replica]} {error "Unexpected config scalar $physical"}
            set bit 0
        } else {
            if {![regexp $rx $physical ignored instance bit replica]} {error "Unexpected config bus $physical"}
        }
        lappend bits $bit; lappend instances $instance
    }
    set expected {}; for {set i 0} {$i<$width} {incr i} {lappend expected $i}
    if {[lsort -unique $instances] ne {core_top:ic|psx_config_cdc:host_config} || [lsort -integer -unique $bits] ne $expected} {
        error "Missing/ambiguous config geometry $name: $bits $instances"
    }
    return $nodes
}
set config_clock [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $config_clock]!=1} {error "Missing/ambiguous config destination clock"}
foreach_in_collection c $config_clock {
    if {[get_clock_info -period $c]<25.0} {error "Config destination clock too fast for 20ns payload bound"}
}
set config_source_clock [get_clocks clk_74a]
if {[get_collection_size $config_source_clock]!=1} {error "Missing config source clock"}
foreach_in_collection c $config_source_clock {
    if {[get_clock_info -period $c]<10.0} {error "Config source clock too fast for payload hold proof"}
}
foreach {launch first second} {request request_meta request_sync acknowledge acknowledge_meta acknowledge_sync} {
    set from [config_regs $launch 1]; set to [config_regs $first 1]
    config_regs $second 1
    set_false_path -from $from -to $to
    puts "CONFIG_FIRST_STAGE $launch $first"
}
set config_payload [config_regs payload 4]
set config_dest [config_regs dst_data 4]
set_max_delay -from $config_payload -to $config_dest 20.0
set_min_delay -from $config_payload -to $config_dest -20.0
set_net_delay -max -from $config_payload -to $config_dest 20.0
set_net_delay -min -from $config_payload -to $config_dest 0.0
set_max_skew -from $config_payload -to $config_dest 20.0
set config_reset [get_registers -nowarn {*|config_reset}]
if {[get_collection_size $config_reset]!=1} {error "Missing registered config reset origin"}
foreach pipe {src_reset_pipe dst_reset_pipe} {
    set to [config_regs $pipe 3]
    set_false_path -from $config_reset -to $to
    puts "CONFIG_ASYNC_RESET $pipe [get_collection_size $to]"
}
# Local reset shift/recovery/removal, second stages, host writers and CPU
# consumers are all timed. No raw payload or whole domain is false-pathed.
