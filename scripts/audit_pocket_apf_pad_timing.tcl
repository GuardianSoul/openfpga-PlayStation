# Private fitted copy only. This audits the synchronizer, not external PAD timing.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh PAD audit output"}
set fault [expr {[llength $quartus(args)]==2 ? [lindex $quartus(args) 1] : ""}]
if {$fault ni {{} --fault-hidden-chain}} {error "Unknown PAD audit fault"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh PAD audit output required"}
file mkdir $output
project_open apf_top
try {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    set clock [get_clocks clk_74a]
    if {[get_collection_size $clock]!=1} {error "Missing/ambiguous APF PAD clock"}
    foreach_in_collection c $clock {
        if {abs([get_clock_info -period $c]-13.468)>0.002} {error "APF PAD clock period changed"}
    }
    set base {io_pad_controller:ipm|synch_3:s01|}
    set expected {}
    foreach tail {{stage_1[0]} {stage_2[0]} {o[0]} {stage_3[0]}} {lappend expected ${base}${tail}}
    set nodes [get_keepers -nowarn {*ipm*s01*}]
    set names {}
    foreach_in_collection n $nodes {lappend names [get_node_info -name $n]}
    if {[lsort $names] ne [lsort $expected]} {error "PAD physical synchronizer geometry changed: $names"}
    set table [open [file join $output input-fanout.txt] w]
    set fanout {}
    foreach_in_collection n [get_fanouts [get_ports bridge_1wire]] {lappend fanout [get_node_info -name $n]}
    puts $table [join $fanout "\n"];close $table
    if {$fanout ne [list [lindex $expected 0]]} {error "PAD input has unexpected physical consumers: $fanout"}
    set ordered {}
    foreach name $expected {
        # Quartus node IDs returned by foreach_in_collection are not query
        # collections. Resolve an exact singleton before passing it to APIs.
        set found [get_keepers -nowarn [list $name]]
        if {[get_collection_size $found]!=1} {error "Missing/ambiguous PAD physical stage"}
        foreach_in_collection node $found {
            if {[get_node_info -name $node] ne $name} {error "PAD singleton query changed endpoint"}
        }
        lappend ordered $found
    }
    foreach pair {{0 1} {1 2}} {
        lassign $pair a b
        set next {}
        foreach_in_collection n [get_fanouts [lindex $ordered $a]] {lappend next [get_node_info -name $n]}
        if {$next ne [list [lindex $expected $b]]} {error "PAD serial stage has unexpected consumers: $next"}
    }
    if {$fault eq "--fault-hidden-chain"} {set_false_path -from [lindex $ordered 1] -to [lindex $ordered 2]}
    set table [open [file join $output paths.tsv] w]
    puts $table "corner\tlink\tkind\tfrom\tto\tslack_ns\tfrom_clock\tto_clock\trelationship_ns\tsetup_end\tsetup_start\thold_end\thold_start"
    set violations {};set corner 0
    foreach_in_collection condition [get_available_operating_conditions] {
        set_operating_conditions $condition
        update_timing_netlist
        for {set link 0} {$link<3} {incr link} {
            set from [lindex $ordered $link];set to [lindex $ordered [expr {$link+1}]]
            foreach kind {setup hold} {
                if {[get_collection_size [get_timing_paths -false_path -$kind -from $from -to $to -npaths 1]]} {error "PAD chain path was hidden"}
                set paths [get_timing_paths -$kind -from $from -to $to -npaths 1]
                if {[get_collection_size $paths]!=1} {error "Missing ordinary PAD interstage timing path"}
                foreach_in_collection p $paths {
                    set names [list [get_node_info -name [get_path_info -from $p]] [get_node_info -name [get_path_info -to $p]]]
                    if {$names ne [list [lindex $expected $link] [lindex $expected [expr {$link+1}]]]} {error "PAD path endpoints changed"}
                    set clocks {}
                    foreach field {from_clock to_clock} {lappend clocks [get_clock_info -name [get_path_info -$field $p]]}
                    if {$clocks ne {clk_74a clk_74a}} {error "PAD synchronizer clock changed: $clocks"}
                    set edge {}
                    foreach field {clock_relationship setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle} {lappend edge [get_path_info -$field $p]}
                    if {[lrange $edge 1 4] ne {1 1 0 0}} {error "PAD ordinary interstage relationship relaxed"}
                    set relationship [expr {$kind eq "setup" ? 13.468 : 0.0}]
                    if {abs([lindex $edge 0]-$relationship)>0.002} {error "PAD interstage edge relationship changed"}
                    set slack [get_path_info -slack $p]
                    if {$slack<0} {lappend violations "$corner/$link/$kind/$slack"}
                    puts $table [join [concat [list $corner $link $kind] $names [list $slack] $clocks $edge] "\t"]
                }
                report_timing -$kind -from $from -to $to -npaths 1 -detail full_path \
                    -file [file join $output corner${corner}-link${link}-${kind}.txt]
            }
        }
        incr corner
    }
    close $table
    if {$corner!=4} {error "Expected four PAD timing corners"}
    if {[llength $violations]} {error "PAD interstage timing negative: $violations"}
    puts "PASS_PAD_CHAIN corners4 links3 ordinary_checks INPUT_PIN_AND_MTBF_NOT_QUALIFIED"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
