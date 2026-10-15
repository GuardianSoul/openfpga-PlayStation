# Private routed copy only; requires the explicit diagnostic pin model.
# quartus_sta -t <script> <fresh-output> [--require-nonnegative|
#   --fault-hidden-video|--fault-hidden-audio|--fault-missing-edge]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh APF pin audit output and optional mode"}
set mode none
if {[llength $quartus(args)]==2} {
    set mode [lindex $quartus(args) 1]
    if {$mode ni {--require-nonnegative --fault-hidden-video --fault-hidden-audio --fault-missing-edge}} {error "Unknown APF audit mode"}
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh APF pin audit output required"}
file mkdir $output
project_open apf_top
try {
create_timing_netlist
read_sdc
if {![info exists apf_pin_video_outputs]} {error "APF pin timing not prepared"}
if {$mode eq "--fault-hidden-video"} {set_false_path -to [get_ports {scal_vid[0]}]}
if {$mode eq "--fault-hidden-audio"} {set_false_path -to [get_ports scal_audlrck]}
if {$mode eq "--fault-missing-edge"} {
    # Quartus removes all reference-edge delays together. Reapply only rising
    # so the independent falling-edge coverage check must reject the omission.
    remove_output_delay $apf_pin_video_outputs
    set_output_delay -clock pocket_apf_video_pin -max [expr {$apf_pin_video_setup+$apf_pin_skew}] $apf_pin_video_outputs
    set_output_delay -clock pocket_apf_video_pin -min [expr {-$apf_pin_video_hold-$apf_pin_skew}] $apf_pin_video_outputs
}
update_timing_netlist
foreach scope {video audio} {
    set clock [get_clocks pocket_apf_${scope}_pin]
    set expected_master [set apf_pin_[expr {$scope eq "video" ? "video_quarter" : "audio_master"}]]
    foreach_in_collection c $clock {
        if {[get_clock_info -master_clock $c] ne [get_clock_info -name $expected_master]} {error "APF pin clock master changed"}
    }
}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tedge\tkind\tpin\tslack_ns\tfrom\tfrom_clock\tto_clock\tlaunch_ns\tlatch_ns\trelationship_ns\tsetup_end\tsetup_start\thold_end\thold_start"
set minimum 1e9
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach scope {video audio} {
        set ports [set apf_pin_${scope}_outputs]
        set clock [get_clocks pocket_apf_${scope}_pin]
        if {$scope eq "video"} {set edges {rise fall};set master $apf_pin_video_master;set period $apf_pin_video_master_period} else {
            set edges rise;set master $apf_pin_audio_master;set period $apf_pin_audio_master_period
        }
        foreach edge $edges {
            foreach kind {setup hold} {
                if {[get_collection_size [get_timing_paths -false_path -$kind -${edge}_to_clock $clock -to $ports -npaths 1]]!=0} {error "APF output path was hidden"}
                foreach_in_collection pin $ports {
                    set paths [get_timing_paths -$kind -${edge}_to_clock $clock -to $pin -npaths 1]
                    if {[get_collection_size $paths]!=1} {error "Missing APF output edge/requirement"}
                    foreach_in_collection path $paths {
                        set slack [get_path_info -slack $path]
                        set minimum [expr {min($minimum,$slack)}]
                        set row [list $corner $scope $edge $kind [get_node_info -name $pin] $slack [get_node_info -name [get_path_info -from $path]]]
                        foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $path]]}
                        if {[lindex $row 7] ne [get_clock_info -name $master] || [lindex $row 8] ne [get_clock_info -name $clock]} {error "APF output clock pair changed"}
                        foreach field {launch_time latch_time clock_relationship setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle} {lappend row [get_path_info -$field $path]}
                        if {[lrange $row 12 15] ne {1 1 0 0}} {error "APF output edge relaxed by multicycle"}
                        if {$scope eq "video"} {
                            set expected [expr {$period/4*($kind eq "setup" ? 1 : -1)}]
                        } elseif {$apf_pin_falling_audio} {
                            set expected [expr {$period/2*($kind eq "setup" ? 1 : -1)}]
                        } else {set expected [expr {$kind eq "setup" ? $period : 0}]}
                        if {abs([lindex $row 11]-$expected)>0.002} {error "Unexpected APF launch/capture relationship"}
                        puts $table [join $row "\t"]
                    }
                }
                report_timing -$kind -${edge}_to_clock $clock -to $ports -npaths 40 -detail full_path \
                    -file [file join $output corner${corner}-${scope}-${edge}-${kind}.txt]
            }
        }
    }
    incr corner
}
close $table
if {$corner!=4} {error "Expected four APF pin corners"}
report_clocks -file [file join $output clocks.txt]
report_ucp -file [file join $output unconstrained.txt]
report_exceptions -npaths 100 -file [file join $output exceptions.txt]
set model [open [file join $output assumptions.tsv] w]
puts $model "video_setup\tvideo_hold\taudio_setup\taudio_hold\tboard_min\tboard_max\tfalling_audio\tminimum_slack\tanalysis_only\texternal_qualified"
puts $model [join [list $apf_pin_video_setup $apf_pin_video_hold $apf_pin_audio_setup $apf_pin_audio_hold $apf_pin_board_min $apf_pin_board_max $apf_pin_falling_audio $minimum 1 0] "\t"]
close $model
if {$mode eq "--require-nonnegative" && $minimum<0} {error "APF diagnostic pin timing violates the assumed requirements"}
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
