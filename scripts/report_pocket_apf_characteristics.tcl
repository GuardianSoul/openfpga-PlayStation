# Private fitted copy. Characterize paths and datasheet values, not I/O slack.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh APF characteristics output"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh APF characteristics output required"}
file mkdir $output
project_open apf_top
try {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    set clocks [open [file join $output clocks.tsv] w]
    puts $clocks "clock\tperiod_ns"
    foreach name {clk_74a bridge_spiclk} {
        set clock [get_clocks $name]
        if {[get_collection_size $clock]!=1} {error "Missing/ambiguous APF clock $name"}
        foreach_in_collection c $clock {
            set period [get_clock_info -period $c]
            if {abs($period-13.468)>0.002} {error "Unreviewed APF clock period"}
            puts $clocks "$name\t$period"
        }
    }
    close $clocks
    set rx {}
    foreach tail {rx_byte_done rx_latch_idx.state_bit_0 rx_latch_idx.state_bit_1} {lappend rx io_bridge_peripheral:ibs|$tail}
    for {set bit 0} {$bit<8} {incr bit} {lappend rx [format {io_bridge_peripheral:ibs|rx_byte[%d]} $bit]}
    for {set bit 2} {$bit<8} {incr bit} {lappend rx [format {io_bridge_peripheral:ibs|rx_dat[%d]} $bit]}
    set input_map [dict create \
        bridge_1wire {{io_pad_controller:ipm|synch_3:s01|stage_1[0]}} \
        bridge_spiclk $rx \
        bridge_spiss [concat $rx {{io_bridge_peripheral:ibs|synch_3:s02|stage_1[0]}}] \
        bridge_spimosi {{io_bridge_peripheral:ibs|rx_byte[1]} {io_bridge_peripheral:ibs|rx_dat[3]} {io_bridge_peripheral:ibs|rx_dat[5]} {io_bridge_peripheral:ibs|rx_dat[7]}} \
        bridge_spimiso {{io_bridge_peripheral:ibs|rx_byte[0]} {io_bridge_peripheral:ibs|rx_dat[2]} {io_bridge_peripheral:ibs|rx_dat[4]} {io_bridge_peripheral:ibs|rx_dat[6]}}]
    set output_map [dict create \
        bridge_1wire {io_pad_controller:ipm|pad_1wire~reg0 io_pad_controller:ipm|pad_1wire~en} \
        bridge_spiclk {io_bridge_peripheral:ibs|phy_spiclk~reg0 io_bridge_peripheral:ibs|phy_spiclk~en} \
        bridge_spimiso {io_bridge_peripheral:ibs|phy_spimiso~reg0 io_bridge_peripheral:ibs|phy_spimiso~en} \
        bridge_spimosi {io_bridge_peripheral:ibs|phy_spimosi~reg0 io_bridge_peripheral:ibs|phy_spimiso~en}]
    set geometry [open [file join $output geometry.tsv] w]
    puts $geometry "direction\tport\tregister"
    set arcs {}
    foreach direction {input output} {
        set mapping [set ${direction}_map]
        dict for {port expected} $mapping {
            set pin [get_ports $port]
            if {[get_collection_size $pin]!=1} {error "Missing APF physical port $port"}
            if {$direction eq "input"} {set actual [get_fanouts $pin]} else {set actual [get_fanins $pin]}
            set names {}
            foreach_in_collection n $actual {lappend names [get_node_info -name $n]}
            if {[lsort $names] ne [lsort $expected]} {error "APF $direction geometry changed for $port: $names"}
            foreach name [lsort $expected] {
                puts $geometry "$direction\t$port\t$name"
                # A clock port's clock-tree fanout is inventoried, not treated
                # as a data path. SS's direct enable/reset consumers remain.
                if {$port eq "bridge_spiclk" && $direction eq "input"} {continue}
                set reg [get_keepers -nowarn [list $name]]
                if {[get_collection_size $reg]!=1} {error "Missing/ambiguous APF endpoint $name"}
                foreach_in_collection n $reg {if {[get_node_info -name $n] ne $name} {error "APF singleton query changed endpoint"}}
                if {$direction eq "input"} {set from $pin;set to $reg} else {set from $reg;set to $pin}
                lappend arcs [list $direction $port $name $from $to]
            }
        }
    }
    close $geometry
    if {[llength $arcs]!=35} {error "Missing APF data/control arcs"}
    set table [open [file join $output transit.tsv] w]
    puts $table "corner\tarc\tdirection\tport\tregister\tminimum\tfrom\tto\tdelay_ns"
    set corner 0
    foreach_in_collection condition [get_available_operating_conditions] {
        set_operating_conditions $condition
        update_timing_netlist
        report_datasheet -expand_bus -file [file join $output corner${corner}-datasheet.txt]
        set index 0
        foreach arc $arcs {
            lassign $arc direction port name from to
            foreach minimum {0 1} {
                set flags {}
                if {$minimum} {lappend flags -min_path}
                set paths [get_path -from $from -to $to -npaths 1 {*}$flags]
                if {[get_collection_size $paths]!=1} {error "Missing APF $direction physical path $port -> $name"}
                foreach_in_collection p $paths {
                    set a [get_node_info -name [get_path_info -from $p]]
                    set b [get_node_info -name [get_path_info -to $p]]
                    if {$direction eq "input"} {set expected [list $port $name]} else {set expected [list $name $port]}
                    if {[list $a $b] ne $expected} {error "APF raw path endpoint changed"}
                    # get_path has delay but no meaningful clock/slack data.
                    puts $table [join [list $corner $index $direction $port $name $minimum $a $b [get_path_info -arrival_time $p]] "\t"]
                }
                report_path -from $from -to $to -npaths 1 {*}$flags -show_routing \
                    -file [file join $output corner${corner}-arc${index}-min${minimum}.txt]
            }
            incr index
        }
        incr corner
    }
    close $table
    if {$corner!=4} {error "Expected four APF characterization corners"}
    puts "PASS_APF_CHARACTERISTICS arcs35 corners4 NO_REQUIREMENT_OR_SLACK_CLAIM"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
