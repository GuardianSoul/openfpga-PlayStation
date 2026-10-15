# Run only from a private copy of a fitted project containing host_config.
# quartus_sta -t <this script> <fresh output directory> [--faults]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected output directory and optional --faults"}
set faults 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--faults"} {error "Unknown config audit option"}
    set faults 1
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Config audit requires fresh output directory"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
# Geometry and scope are checked by the project's pocket_config_scope.tcl.
set width 4
if {[info exists config_width]} {set width $config_width}
if {$width ni {4 5 6}} {error "Unknown host configuration payload width"}
set payload [config_regs payload $width]; set destination [config_regs dst_data $width]
set host_patterns [list {*|biosregion_host[*]} {*|region_host[*]}]
if {$width>=5} {lappend host_patterns {core_top:ic|pause_host} {core_top:ic|pause_host~DUPLICATE*}}
if {$width==6} {lappend host_patterns {core_top:ic|isPal_host} {core_top:ic|isPal_host~DUPLICATE*}}
set host [get_registers $host_patterns]
# Physical synthesis can replicate a host bit. Keep every replica in the raw
# crossing report, while still requiring all four original logical bits.
set host_bits [dict create]
foreach_in_collection r $host {
    set name [get_node_info -name $r]
    if {![regexp {\|(biosregion_host|region_host)\[([01])\](~DUPLICATE[0-9]*)?$} $name -> field bit replica]} {
        if {$width>=5 && [regexp {^core_top:ic\|pause_host(~DUPLICATE[0-9]*)?$} $name -> replica]} {
            set field pause_host;set bit 0
        } elseif {$width==6 && [regexp {^core_top:ic\|isPal_host(~DUPLICATE[0-9]*)?$} $name -> replica]} {
            set field isPal_host;set bit 0
        } else {
            error "Unexpected config host source register $name"
        }
    }
    if {$replica eq ""} {dict set host_bits "$field/$bit" 1}
}
if {$width>=5 && ![dict exists $host_bits pause_host/0]} {error "Missing coherent host pause source"}
if {$width==6 && ![dict exists $host_bits isPal_host/0]} {error "Missing coherent host PAL source"}
foreach field {biosregion_host region_host} {
    foreach bit {0 1} {
        if {![dict exists $host_bits "$field/$bit"]} {error "Missing config host source $field/$bit"}
    }
}
puts "CONFIG_HOST_SOURCES logical=[dict size $host_bits] physical=[get_collection_size $host]"
set cpu [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
set core [get_registers {*|psx_mister_psx_top_resource:psx|*}]
if {[get_collection_size $core]<1000} {error "Missing connected PSX consumer geometry"}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        set cuts [get_timing_paths -false_path -$kind -from $payload -to $destination -npaths 1]
        if {[get_collection_size $cuts]!=0} {error "Host configuration payload was false-pathed ($kind)"}
        set timed [get_timing_paths -$kind -from $payload -to $destination -npaths 1]
        if {[get_collection_size $timed]!=1} {error "Missing timed host payload ($kind)"}
    }
    foreach {launch first second} {request request_meta request_sync acknowledge acknowledge_meta acknowledge_sync} {
        foreach kind {setup hold} {
            report_timing -$kind -from [config_regs $launch 1] -to [config_regs $first 1] -npaths 20 -detail full_path \
                -file [file join $output corner${corner}-${launch}-first-${kind}.txt]
            report_timing -$kind -from [config_regs $first 1] -to [config_regs $second 1] -npaths 20 -detail full_path \
                -file [file join $output corner${corner}-${launch}-second-${kind}.txt]
        }
    }
    foreach pipe {src_reset_pipe dst_reset_pipe} {
        set reset_bit [get_registers [format {*|psx_config_cdc:host_config|%s[2]} $pipe]]
        if {[get_collection_size $reset_bit]!=1} {error "Missing config local reset endpoint"}
        foreach kind {setup hold} {
            report_timing -$kind -from [config_regs $pipe 3] -to [config_regs $pipe 3] -npaths 20 -detail full_path \
                -file [file join $output corner${corner}-${pipe}-shift-${kind}.txt]
        }
        foreach kind {recovery removal} {
            report_timing -$kind -from $reset_bit -npaths 100 -detail full_path \
                -file [file join $output corner${corner}-${pipe}-${kind}.txt]
        }
    }
    foreach kind {setup hold} {
        report_timing -$kind -npaths 20 -detail full_path \
            -file [file join $output corner${corner}-global-${kind}.txt]
        report_timing -$kind -from $payload -to $destination -npaths 100 -detail full_path \
            -file [file join $output corner${corner}-payload-${kind}.txt]
        report_timing -$kind -from $destination -to $core -npaths 20 -detail full_path \
            -file [file join $output corner${corner}-consumer-${kind}.txt]
        report_timing -$kind -from $host -to_clock $cpu -npaths 20 -detail full_path \
            -file [file join $output corner${corner}-raw-host-${kind}.txt]
    }
    report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four config operating conditions"}
if {$faults} {
    set_net_delay -max -from $payload -to $destination 0.001
    set_max_skew -from $payload -to $destination 0.001
    update_timing_netlist
    report_net_delay -nworst 500 -file [file join $output planted-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output planted-skew.txt]
}
delete_timing_netlist
project_close -dont_export_assignments
