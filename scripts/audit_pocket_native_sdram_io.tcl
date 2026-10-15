# Private routed copy with prepared Native SDRAM pin model.
# project_open can migrate this COPY of the QSF even when SDC rejects.
# Keep the original fit and its input/compiled database hashes separately.
# quartus_sta -t <script> <fresh-output>
#   [--fault-input|--fault-output|--fault-hold-relaxation|
#    --previous-beat-diagnostic|--expect-previous-beat].
# Fault modes add an exception to prove rejection. The explicit previous-beat
# experiment changes only the in-memory read setup relationship, keeps next-beat
# hold, and cannot run without the analysis-only model. Nothing is saved to SDC.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh Native SDRAM audit output and optional planted fault"}
set fault none
if {[llength $quartus(args)]==2} {
    set fault [lindex $quartus(args) 1]
    if {$fault ni {--fault-input --fault-output --fault-hold-relaxation --previous-beat-diagnostic --expect-previous-beat}} {error "Unknown Native pin audit option"}
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh Native SDRAM output required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
if {$fault eq "--fault-input"} {set_false_path -from [get_ports {dram_dq[0]}]}
if {$fault eq "--fault-output"} {set_false_path -to [get_ports {dram_a[0]}]}
set previous_beat_diagnostic 0
set previous_beat_expected [expr {$fault in {--previous-beat-diagnostic --expect-previous-beat --fault-hold-relaxation}}]
if {$fault eq "--previous-beat-diagnostic"} {
    if {!$native_sdram_analysis_only} {error "Previous-beat experiment requires analysis-only model"}
    set previous_beat_diagnostic 1
    set_multicycle_path -setup 2 -from $native_sdram_inputs -to $native_sdram_caps
    # No hold multicycle: the next launched beat can invalidate this sample.
    puts "NATIVE_SDRAM_PREVIOUS_BEAT_DIAGNOSTIC SETUP=2 HOLD_RELAXATION=0"
}
if {$fault eq "--fault-hold-relaxation"} {
    set_multicycle_path -setup 2 -from $native_sdram_inputs -to $native_sdram_caps
    set_multicycle_path -hold 1 -from $native_sdram_inputs -to $native_sdram_caps
}
update_timing_netlist
if {[get_collection_size [get_clocks -nowarn pocket_native_sdram_chip]]!=1} {error "Native pin clock absent"}
set cas3_tag [get_registers -nowarn {*|psx_native_memory:native_memory|psx_sdram_ctrl:controller|q_v}]
if {$native_sdram_cas==3 && [get_collection_size $cas3_tag]!=1} {
    error "Active CAS3 wrapper does not match the compiled read-tag stage"
}
set edges [open [file join $output input-edges.tsv] w]
puts $edges "corner\tkind\tfrom\tto\tsetup_end\tsetup_start\thold_end\thold_start\tlaunch_ns\tlatch_ns\trelationship_ns"
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        if {[get_collection_size [get_timing_paths -false_path -$kind -from $native_sdram_inputs -to $native_sdram_caps -npaths 1]]!=0} {
            error "Native SDRAM input path was false-pathed"
        }
        if {[get_collection_size [get_timing_paths -false_path -$kind -to $native_sdram_outputs -npaths 1]]!=0} {
            error "Native SDRAM output path was false-pathed"
        }
        foreach scope {input output global} {
            switch $scope {
                input {set filter [list -from $native_sdram_inputs -to $native_sdram_caps]}
                output {set filter [list -to $native_sdram_outputs]}
                global {set filter {}}
            }
            set paths [get_timing_paths -$kind {*}$filter -npaths 100]
            if {[get_collection_size $paths]==0} {error "Empty Native pin scope $scope/$kind"}
            set origins {};set targets {}
            foreach_in_collection path $paths {
                set row [list $corner $scope $kind [get_path_info -slack $path]]
                foreach field {from to} {
                    set name [get_node_info -name [get_path_info -$field $path]]
                    lappend row $name
                    if {$field eq "from"} {lappend origins $name} else {lappend targets $name}
                }
                foreach field {from_clock to_clock} {
                    lappend row [get_clock_info -name [get_path_info -$field $path]]
                }
                puts $table [join $row "\t"]
                if {$scope eq "input"} {
                    set edge [list $corner $kind [lindex $row 4] [lindex $row 5]]
                    foreach field {setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle launch_time latch_time clock_relationship} {
                        lappend edge [get_path_info -$field $path]
                    }
                    puts $edges [join $edge "\t"]
                    set expected_setup [expr {$previous_beat_expected ? 2 : 1}]
                    if {[lindex $edge 4]!=$expected_setup || [lindex $edge 5]!=1 ||
                        [lindex $edge 6]!=0 || [lindex $edge 7]!=0} {
                        error "Native SDRAM capture relationship altered or next-beat hold relaxed"
                    }
                    set expected_relation [expr {$native_sdram_period *
                        ($kind eq "setup" ? $expected_setup-0.5 : $expected_setup-1.5)}]
                    if {abs([lindex $edge 10]-$expected_relation)>0.002} {
                        error "Native SDRAM read launch/capture edge differs from reviewed relationship"
                    }
                    if {[lindex $row 6] ne "pocket_native_sdram_chip" ||
                        [lindex $row 7] ne [get_clock_info -name $native_sdram_master]} {
                        error "Native SDRAM input path clock differs from reviewed clock pair"
                    }
                }
            }
            if {$scope eq "input" && ([llength [lsort -unique $origins]]!=16 || [llength [lsort -unique $targets]]!=16)} {
                error "Native SDRAM inputs/captures not16/16"
            }
            # CKE is tied high; 36 other output pins must be timed, including
            # DQ data/OE paths. No empty-output report is accepted as success.
            if {$scope eq "output" && [llength [lsort -unique $targets]]!=36} {
                error "Native SDRAM output coverage not36 (CKE constant)"
            }
            report_timing -$kind {*}$filter -npaths 100 -detail full_path \
                -file [file join $output corner${corner}-${scope}-${kind}.txt]
        }
    }
    incr corner
}
close $table
close $edges
if {$corner!=4} {error "Expected four Native pin timing corners"}
report_clocks -file [file join $output clocks.txt]
report_ucp -file [file join $output unconstrained.txt]
report_exceptions -npaths 100 -file [file join $output exceptions.txt]
set model [open [file join $output model.tsv] w]
puts $model "cas\tperiod_ns\tminimum_period_ns\trate_conformant\tanalysis_only\tboard_min_ns\tboard_max_ns\tprevious_beat_diagnostic\tprevious_beat_expected\tcompiled_cas3_tag"
puts $model [join [list $native_sdram_cas $native_sdram_period $native_sdram_min_period $native_sdram_rate_ok $native_sdram_analysis_only $native_sdram_board_min $native_sdram_board_max $previous_beat_diagnostic $previous_beat_expected [get_collection_size $cas3_tag]] "\t"]
close $model
delete_timing_netlist
project_close -dont_export_assignments
