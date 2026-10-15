# Private routed database. Never modify saved constraints or fitted inputs.
# quartus_sta -t <this> <fresh-output> [--fault-output]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected report RAM audit output and optional fault"}
set fault 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--fault-output"} {error "Unknown report RAM fault"}
    set fault 1
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh report RAM output required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set prefix {*|gpu_videoout_async:igpu_videoout_async|psx_video_reports_cdc:video_reports|dual_clock_fifo:snapshots|}
set data [get_keepers -nowarn [format {%srd_data_o[*]} $prefix]]
set bits {};set instances {}
foreach_in_collection node $data {
    set name [get_node_info -name $node]
    if {![regexp {^(.*\|dual_clock_fifo:snapshots)\|rd_data_o\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $name -> instance bit replica]} {error "Unknown report RAM output $name"}
    lappend instances $instance;lappend bits $bit
}
# Returned vsync/interlacedDisplayField are unused in this connected build.
# All eight live bits (including tick and unpause) must have timed consumers.
if {[llength [lsort -unique $instances]]!=1 || [lsort -integer -unique $bits] ne {0 2 3 4 5 6 8 9}} {error "Unexpected report RAM live-bit geometry: $bits"}
set valid [reports_regs read_valid 1]
set cpu [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $cpu]!=1} {error "Unknown report RAM read clock"}
foreach_in_collection c $cpu {set cpu_name [get_clock_info -name $c]}
set core [get_registers {*|psx_mister_psx_top_resource:psx|*}]
if {[get_collection_size $core]<1000} {error "Missing connected report consumers"}
if {$fault} {set_false_path -from $data -to $core}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach scope {ram valid} {
        if {$scope eq "ram"} {set origins $data} else {set origins $valid}
        foreach_in_collection origin $origins {
            set origin_name [get_node_info -name $origin]
            foreach kind {setup hold} {
                if {[get_collection_size [get_timing_paths -false_path -$kind -from $origin -to $core -npaths 1]]!=0} {error "Report RAM output was hidden"}
                set paths [get_timing_paths -$kind -from $origin -to $core -npaths 1]
                if {[get_collection_size $paths]!=1} {error "Missing timed report output $origin_name/$kind"}
                foreach_in_collection path $paths {
                    if {[get_clock_info -name [get_path_info -from_clock $path]] ne $cpu_name} {error "Report RAM output has unexpected source clock"}
                    set row [list $corner $scope $kind [get_path_info -slack $path] $origin_name [get_node_info -name [get_path_info -to $path]]]
                    foreach field {from_clock to_clock} {lappend row [get_clock_info -name [get_path_info -$field $path]]}
                    puts $table [join $row "\t"]
                }
            }
        }
        foreach kind {setup hold} {
            report_timing -$kind -from $origins -to $core -npaths 100 -detail full_path -file [file join $output corner${corner}-${scope}-${kind}.txt]
        }
    }
    incr corner
}
if {$corner!=4} {error "Expected four report RAM corners"}
close $table
delete_timing_netlist
project_close -dont_export_assignments
