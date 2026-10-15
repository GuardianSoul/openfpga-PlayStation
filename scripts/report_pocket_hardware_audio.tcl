# Audit a COPY of the actual fitted hardware project; no added exceptions.
# The async Gray first-stage hold reports are retained separately from local
# audio timing. Net-delay/skew constraints must still bound every physical bit.
package require ::quartus::project
package require ::quartus::sta
source [file join [file dirname [info script]] pocket_audio_scope.tcl]
if {[llength $quartus(args)] != 1} {error "Expected fresh audit directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
try {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    set audio [get_clocks {*audio_pll*OUTPUT_COUNTER*divclk}]
    if {[get_collection_size $audio] != 1} {error "Expected exactly one audio MCLK"}
    foreach pointer {wr rd} {
        set launch($pointer) [pocket_audio_gray_bus {*audio_stream*|} $pointer]
        set receiver($pointer) [get_registers [format {*audio_stream*|%s_gray_meta[*]} $pointer]]
        if {[get_collection_size $receiver($pointer)] != 7} {error "Missing Gray receivers"}
        puts "HARDWARE_AUDIO_GRAY $pointer [get_collection_size $launch($pointer)] 7"
    }
    report_clocks -file $output/clocks.txt
    report_ucp -file $output/unconstrained.txt
    set corner 0
    foreach_in_collection op [get_available_operating_conditions] {
        set_operating_conditions $op
        update_timing_netlist
        puts "HARDWARE_AUDIO_CORNER $corner [get_operating_conditions_info $op -model]"
        foreach kind {setup hold} {
            report_timing -$kind -from_clock $audio -to_clock $audio -npaths 12 -detail full_path \
                -file $output/corner${corner}-local-${kind}.txt
            foreach pointer {wr rd} {
                report_timing -$kind -from $launch($pointer) -to $receiver($pointer) -npaths 16 -detail full_path \
                    -file $output/corner${corner}-${pointer}-gray-${kind}.txt
            }
        }
        foreach kind {recovery removal} {
            report_timing -$kind -to_clock $audio -npaths 12 -detail full_path \
                -file $output/corner${corner}-${kind}.txt
        }
        report_net_delay -nworst 64 -file $output/corner${corner}-net-delay.txt
        report_max_skew -npaths 16 -detail full_path -file $output/corner${corner}-max-skew.txt
        incr corner
    }
    delete_timing_netlist
} finally {
    project_close -dont_export_assignments
}
