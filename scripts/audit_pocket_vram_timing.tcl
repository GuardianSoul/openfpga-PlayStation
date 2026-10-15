# Run only in a PRIVATE COPY of a routed SDRAM-only project: project_open
# can migrate the QSF. The project's SDC must include pocket_vram_scope.tcl.
# quartus_sta -t <this script> <output-directory> [--faults]
# --faults adds deliberately impossible bounds AFTER the four-corner audit.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {
    error "Expected output directory and optional --faults"
}
set faults 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--faults"} {error "Unknown audit option"}
    set faults 1
}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
if {[llength [info commands pocket_vram_gray_source]]!=1} {
    error "Project SDC is missing scoped VRAM constraints"
}
set scope {*|psx_vram_stream:vram_memory|}
set first_patterns {}; set second_patterns {}
foreach {suffix meta sync width} {
    {dual_clock_fifo:commands|} rd_addr_gray_wr rd_addr_gray_wr_r 4
    {dual_clock_fifo:commands|} wr_addr_gray_rd wr_addr_gray_rd_r 4
    {dual_clock_fifo:responses|} rd_addr_gray_wr rd_addr_gray_wr_r 4
    {dual_clock_fifo:responses|} wr_addr_gray_rd wr_addr_gray_rd_r 4
    {} retired_command_meta retired_command_sync 5
    {} finished_word_meta finished_word_sync 11
    {} consumed_meta consumed_sync 5
} {
    set first_pattern [format {%s%s%s[*]} $scope $suffix $meta]
    set second_pattern [format {%s%s%s[*]} $scope $suffix $sync]
    set first [get_registers -nowarn $first_pattern]
    set second [get_registers -nowarn $second_pattern]
    if {[get_collection_size $first]==0 && [get_collection_size $second]==0 &&
        $suffix eq "dual_clock_fifo:responses|" && $meta eq "rd_addr_gray_wr"} {continue}
    if {[get_collection_size $first]!=$width || [get_collection_size $second]!=$width} {
        error "Missing/ambiguous VRAM synchronizer pair $meta/$sync"
    }
    lappend first_patterns $first_pattern
    lappend second_patterns $second_pattern
}
set first [get_registers $first_patterns]
set second [get_registers $second_patterns]
foreach kind {setup hold} {
    # A hold-only false path also suppresses set_max_skew in Lite 25.1.
    # Require every first-stage Gray path to retain both kinds of analysis.
    set cut [get_timing_paths -false_path -$kind -to $first -npaths 1]
    if {[get_collection_size $cut]!=0} {error "False-pathed VRAM Gray first stage ($kind); skew checks are not qualified"}
}
puts "VRAM_AUDIT_GRAY_STAGES [get_collection_size $first] [get_collection_size $second]"
set status_first_patterns {}; set status_second_patterns {}
foreach pointer {initialized_sync backend_fault_sync source_online_sync} {
    set meta_pattern [format {%s%s[0]} $scope $pointer]
    set sync_pattern [format {%s%s[1]} $scope $pointer]
    set meta [get_registers -nowarn $meta_pattern]
    set sync [get_registers -nowarn $sync_pattern]
    if {$pointer eq "backend_fault_sync" && [get_collection_size $meta]==0 && [get_collection_size $sync]==0} {continue}
    if {[get_collection_size $meta]!=1 || [get_collection_size $sync]!=1} {
        error "Missing/ambiguous VRAM status pair $pointer"
    }
    lappend status_first_patterns $meta_pattern
    lappend status_second_patterns $sync_pattern
}
set status_first [get_registers $status_first_patterns]
set status_second [get_registers $status_second_patterns]
puts "VRAM_AUDIT_STATUS_STAGES [get_collection_size $status_first] [get_collection_size $status_second]"
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        report_timing -$kind -to $first -npaths 100 -detail summary \
            -file [file join $output corner${corner}-gray-first-${kind}.txt]
        report_timing -$kind -from $first -to $second -npaths 100 -detail summary \
            -file [file join $output corner${corner}-gray-second-${kind}.txt]
        report_timing -$kind -to $status_first -npaths 20 -detail summary \
            -file [file join $output corner${corner}-status-first-${kind}.txt]
        report_timing -$kind -from $status_first -to $status_second -npaths 20 -detail summary \
            -file [file join $output corner${corner}-status-second-${kind}.txt]
    }
    report_net_delay -nworst 100 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 100 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four operating conditions, found $corner"}
if {$faults} {
    set prefix "${scope}dual_clock_fifo:commands|"
    set from [pocket_vram_gray_source $prefix rd_addr_gray rd_addr 4]
    set to [get_registers [format {%srd_addr_gray_wr[*]} $prefix]]
    set_net_delay -max -from $from -to $to 0.001
    set_max_skew -from $from -to $to 0.001
    update_timing_netlist
    report_net_delay -nworst 100 -file [file join $output planted-net.txt]
    report_max_skew -npaths 100 -detail full_path -file [file join $output planted-skew.txt]
}
delete_timing_netlist
project_close -dont_export_assignments
