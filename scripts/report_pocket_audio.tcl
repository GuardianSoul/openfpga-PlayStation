# Read-only audit of the freshly fitted standalone audio project.
package require ::quartus::project
package require ::quartus::sta
source [file join [file dirname [info script]] pocket_audio_scope.tcl]
if {[llength $quartus(args)] != 1} {error "Expected audit output directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open psx_audio_bridge_resource
set status [catch {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    foreach {source destination} {wr_gray wr_gray_meta rd_gray rd_gray_meta} {
        set from [pocket_audio_gray_bus {*audio_stream*|} [string range $source 0 1]]
        set to [get_registers "*audio_stream*|${destination}\[*\]"]
        puts "AUDIO_GRAY_SCOPE $source [get_collection_size $from] [get_collection_size $to]"
        if {[get_collection_size $from] < 7 || [get_collection_size $to] != 7} {
            error "Audio Gray scope mismatch"
        }
        foreach kind {setup hold} {
            report_timing -$kind -from $from -to $to -npaths 7 -detail full_path \
                -file [file join $output ${source}-${kind}.txt]
        }
    }
    report_net_delay -nworst 20 -file [file join $output net-delay.txt]
    report_max_skew -npaths 10 -detail full_path -file [file join $output max-skew.txt]
    report_timing -recovery -npaths 20 -file [file join $output recovery.txt]
    report_timing -removal -npaths 20 -file [file join $output removal.txt]
} result]
catch {delete_timing_netlist}
project_close -dont_export_assignments
if {$status} {error $result}
