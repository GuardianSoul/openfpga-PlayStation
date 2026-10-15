# Run on a private COPY of a completed routed full-top project.
# quartus_sta -t <this script> <fresh output directory>
# Report tag inputs and ALL tag consumers at all four conditions. No SDC
# exception/clock changes. Empty per-launch-clock reports are retained so
# the actual source-clock scope can be reviewed; these reports alone do
# not assert global timing or hardware qualification.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh output directory"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh tag audit directory required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set scope {*|gpu_pixelpipeline:igpu_pixelpipeline|}
set tags [get_keepers -nowarn ${scope}*itagram*]
set states [get_registers -nowarn ${scope}state*]
if {[get_collection_size $tags]<1 || [get_collection_size $states]<3} {error "Missing tag RAM/state geometry"}
foreach_in_collection node $tags {puts "TAG_KEEPER [get_node_info -name $node]"}
foreach_in_collection node $states {puts "TAG_STATE [get_node_info -name $node]"}
set clocks [get_clocks *]
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        report_timing -$kind -to $tags -npaths 100 -detail full_path -file [file join $output corner${corner}-inputs-${kind}.txt]
        report_timing -$kind -from $tags -npaths 100 -detail full_path -file [file join $output corner${corner}-ram-all-${kind}.txt]
        set ci 0
        foreach_in_collection clk $clocks {
            puts "TAG_LAUNCH_CLOCK $ci [get_clock_info -name $clk]"
            report_timing -$kind -from_clock $clk -to $tags -npaths 10 -detail full_path -file [file join $output corner${corner}-launch${ci}-${kind}.txt]
            incr ci
        }
        report_timing -$kind -from $tags -to $states -npaths 40 -detail full_path -file [file join $output corner${corner}-ram-state-${kind}.txt]
        report_timing -$kind -to $states -npaths 40 -detail full_path -file [file join $output corner${corner}-all-state-${kind}.txt]
    }
    incr corner
}
if {$corner!=4} {error "Expected4conditions"}
report_clocks -file [file join $output clocks.txt]
delete_timing_netlist
project_close -dont_export_assignments
