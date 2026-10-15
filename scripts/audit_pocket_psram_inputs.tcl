# Paired PSRAM input audit on a PRIVATE COPY of a completed routed project.
# quartus_sta -t <this script> <fresh output directory>
# Captures17pins/17first FFs perchip at4corners. Does not change constraints
# or assert board timing/clock-edge alignment. Reported slack depends on the
# saved I/O model and exceptions; qualify those separately.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh output path"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh output required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
foreach {c chip} {cram0 chip0 cram1 chip1} {
 set pins [get_ports [format {%s_dq[*] %s_wait} $c $c]]
 set caps [get_registers [format {*|pair|%s|cram_dq_r[*] *|pair|%s|cram_wait_r} $chip $chip]]
 puts "PSRAM_CAPTURE_SCOPE $c [get_collection_size $pins] [get_collection_size $caps]"
 if {[get_collection_size $pins]!=17 || [get_collection_size $caps]!=17} {error "Missing input/capture geometry"}
 foreach_in_collection node $caps {puts "PSRAM_CAPTURE_REG [get_node_info -name $node]"}
}
report_clocks -file [file join $output clocks.txt]
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
 set_operating_conditions $condition
 update_timing_netlist
 foreach {c chip} {cram0 chip0 cram1 chip1} {
  set pins [get_ports [format {%s_dq[*] %s_wait} $c $c]]
  set caps [get_registers [format {*|pair|%s|cram_dq_r[*] *|pair|%s|cram_wait_r} $chip $chip]]
  foreach kind {setup hold} {
   report_timing -$kind -from $pins -to $caps -npaths 34 -detail full_path -file [file join $output corner${corner}-${c}-${kind}.txt]
  }
 }
 incr corner
}
if {$corner!=4} {error "Expected4corners"}
delete_timing_netlist
project_close -dont_export_assignments
