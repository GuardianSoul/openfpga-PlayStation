# Private copied routed project. Existing SDC unchanged; all launch clocks kept.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh output directory"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh GPU audit output required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set scopes {}
foreach field {reqVRAMwrap reqVRAMremain reqVRAMnext vram_BURSTCNT} {
    set ends [get_registers -nowarn [format {*|gpu:igpu|%s[*]} $field]]
    set n [get_collection_size $ends]
    set bits {};set instances {}
    foreach_in_collection node $ends {
        set name [get_node_info -name $node]
        puts "GPU_FIELD_NODE $field $name"
        if {![regexp [format {^(.*)\|%s\[([0-7])\](~DUPLICATE[0-9]*)?$} $field] $name ignored instance bit replica]} {error "Unproven GPU field replica $name"}
        lappend bits $bit;lappend instances $instance
    }
    if {[llength [lsort -unique $instances]]!=1 || [lsort -unique -integer $bits] ne {0 1 2 3 4 5 6 7}} {error "Missing/ambiguous logical GPU field $field"}
    puts "GPU_FIELD $field $n"
    lappend scopes $field $ends
}
set starts [get_registers -nowarn [list {*|psx_vram_stream:vram_memory|finished_word_sync[*]} {*|psx_vram_stream:vram_memory|posted_words[*]}]]
if {[get_collection_size $starts]<10} {error "Missing GPU credit producer"}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach {field ends} $scopes {
        foreach kind {setup hold} {
            report_timing -$kind -to $ends -npaths 20 -detail full_path -file [file join $output corner${corner}-${field}-${kind}.txt]
            report_timing -$kind -from $starts -to $ends -npaths 20 -detail full_path -file [file join $output corner${corner}-${field}-credit-${kind}.txt]
        }
    }
    incr corner
}
if {$corner!=4} {error "Expected four conditions"}
delete_timing_netlist
project_close -dont_export_assignments
