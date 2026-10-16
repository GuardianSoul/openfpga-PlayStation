# Audit a PRIVATE COPY of a completed full-top database. project_open may
# migrate the QSF even with project_close -dont_export_assignments.
# quartus_sta -t <script> <fresh-own-output-directory> [--related-corners|--native-corners]
# cwd = copied fitted project. The optional SDRAM-only audit retains all
# related CPU/memory clocks and reports their paths at all four corners.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {
    error "Expected audit output directory and optional --related-corners"
}
set related_corners 0
set native_corners 0
if {[llength $quartus(args)]==2} {
    switch -- [lindex $quartus(args) 1] {
        --related-corners {set related_corners 1}
        --native-corners {set native_corners 1}
        default {error "Unknown audit option"}
    }
}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
try {
create_timing_netlist
read_sdc
update_timing_netlist
report_clocks -file [file join $output clocks.txt]
report_clock_transfers -file [file join $output clock-transfers.txt]
report_ucp -file [file join $output unconstrained.txt]
report_exceptions -npaths 200 -file [file join $output exceptions.txt]
foreach kind {setup hold recovery removal} {
    report_timing -$kind -npaths 20 -detail full_path -file [file join $output worst-${kind}.txt]
}
set index 0
foreach_in_collection clock [get_clocks *] {
    set name [get_clock_info -name $clock]
    puts "FULL_CLOCK $index $name"
    foreach kind {setup hold} {
        report_timing -$kind -from_clock $clock -to_clock $clock -npaths 8 -detail full_path \
            -file [file join $output intra-${index}-${kind}.txt]
    }
    incr index
}
foreach {label from to} {
    vram-return {*|vram_cdc|s_rsp_rdata[*]} {*|vram_commands|dout[*]}
    vram-bundle {*|vram_cdc|response_payload[*]} {*|vram_cdc|s_rsp_rdata[*]}
    sdram-return {*|main_memory|dq_reg[*]} {*|main_memory|ch1_dout[*] *|main_memory|ch1_dout32[*]}
    video-pad {*|video_r[*] *|video_g[*] *|video_b[*]} {*|outdata_high_r[*] *|outdata_low_r[*]}
} {
    set starts [get_registers $from]
    set ends [get_registers $to]
    puts "FULL_PATH_SCOPE $label [get_collection_size $starts] [get_collection_size $ends]"
    if {[get_collection_size $starts] > 0 && [get_collection_size $ends] > 0} {
        foreach kind {setup hold} {
            report_timing -$kind -from $starts -to $ends -npaths 8 -detail full_path \
                -file [file join $output ${label}-${kind}.txt]
        }
    }
}
if {$related_corners} {
    set cpu [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
    set memory [get_clocks {*memory_clocks*OUTPUT_COUNTER*divclk}]
    if {[get_collection_size $cpu]!=3 || [get_collection_size $memory]!=3} {
        error "Unexpected SDRAM-only PLL clock geometry"
    }
    # These use the project's saved pin delays. Reports do not establish
    # that the board/skew/data-sheet assumptions themselves are qualified.
    foreach kind {setup hold} {
        report_timing -$kind -from [get_ports {dram_dq[*]}] -npaths 4 -detail full_path \
            -file [file join $output pin-input-${kind}.txt]
        report_timing -$kind -to [get_ports {dram_a[*] dram_ba[*] dram_dqm[*] dram_dq[*] dram_ras_n dram_cas_n dram_we_n dram_cke}] \
            -npaths 4 -detail full_path -file [file join $output pin-output-${kind}.txt]
    }
    set corner 0
    foreach_in_collection condition [get_available_operating_conditions] {
        set_operating_conditions $condition
        update_timing_netlist
        foreach {label family} [list cpu $cpu memory $memory] {
            foreach kind {setup hold} {
                report_timing -$kind -from_clock $family -to_clock $family -npaths 8 -detail full_path \
                    -file [file join $output corner${corner}-${label}-${kind}.txt]
            }
        }
        incr corner
    }
    if {$corner!=4} {error "Expected four operating conditions, found $corner"}
}
if {$native_corners} {
    set cpu [get_clocks {*cpu_pll*OUTPUT_COUNTER*divclk}]
    set video [get_clocks {*video_pll*OUTPUT_COUNTER*divclk}]
    set audio [get_clocks {*audio_pll*OUTPUT_COUNTER*divclk}]
    set native [get_keepers {*|psx_native_memory:native_memory|*}]
    if {[get_collection_size $cpu]!=3 || [get_collection_size $video]!=2 ||
        [get_collection_size $audio]!=1 || [get_collection_size $native]<50} {
        error "Unexpected Native integrated clock/memory geometry"
    }
    set input_pins [get_ports -nowarn {dram_dq[*] cram0_dq[*] cram1_dq[*] cram0_wait cram1_wait}]
    set output_pins [get_ports -nowarn {dram_a[*] dram_ba[*] dram_dqm[*] dram_dq[*] dram_we_n dram_ras_n dram_cas_n dram_cke cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n cram1_a[*] cram1_dq[*] cram1_adv_n cram1_cre cram1_ce0_n cram1_ce1_n cram1_oe_n cram1_we_n cram1_ub_n cram1_lb_n}]
    if {[get_collection_size $input_pins]<48 || [get_collection_size $output_pins]<70} {
        error "Native memory pin geometry missing"
    }
    set table [open [file join $output native-paths.tsv] w]
    puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
    set coverage [open [file join $output native-coverage.tsv] w]
    puts $coverage "corner\tscope\tkind\tpaths"
    proc native_paths {name args} {
        global table coverage corner output
        # Reset checks must participate in the machine-readable whole-core
        # gate too; text-only reports can conceal a recovery/removal failure
        # from an aggregator that checks only the setup/hold table.
        set kinds {setup hold}
        if {$name eq "global"} {set kinds {setup hold recovery removal}}
        foreach kind $kinds {
            set paths [get_timing_paths -$kind {*}$args -npaths 100]
            puts $coverage [join [list $corner $name $kind [get_collection_size $paths]] "\t"]
            report_timing -$kind {*}$args -npaths 100 -detail full_path \
                -file [file join $output corner${corner}-native-${name}-${kind}.txt]
            foreach_in_collection p $paths {
                set row [list $corner $name $kind [get_path_info -slack $p]]
                foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $p]]}
                foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $p]]}
                puts $table [join $row "\t"]
            }
        }
    }
    set clock_table [open [file join $output native-clocks.tsv] w]
    puts $clock_table "index\tclock"
    set index 0
    foreach_in_collection clock [get_clocks *] {
        puts $clock_table "$index\t[get_clock_info -name $clock]"
        incr index
    }
    close $clock_table
    set corner 0
    foreach_in_collection condition [get_available_operating_conditions] {
        set_operating_conditions $condition
        update_timing_netlist
        native_paths global
        native_paths cpu-family -from_clock $cpu -to_clock $cpu
        set index 0
        foreach_in_collection clock [get_clocks *] {
            native_paths intra${index} -from_clock $clock -to_clock $clock
            native_paths receiver${index} -to_clock $clock
            incr index
        }
        # Empty pin timing is explicitly recorded in coverage. It means
        # absent timing coverage, never zero slack or a passing interface.
        native_paths memory-input -from $input_pins
        native_paths memory-output -to $output_pins
        foreach kind {recovery removal} {
            report_timing -$kind -npaths 100 -detail full_path \
                -file [file join $output corner${corner}-native-${kind}.txt]
        }
        incr corner
    }
    close $table;close $coverage
    if {$corner!=4} {error "Expected four Native operating conditions"}
}
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
