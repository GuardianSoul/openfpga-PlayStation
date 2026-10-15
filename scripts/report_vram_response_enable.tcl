# Read-only fitted-netlist audit; run from the freshly fitted project.
package require ::quartus::project
package require ::quartus::sta
set mode post-fit
if {[llength $quartus(args)] == 2 && [lindex $quartus(args) 1] == "post-map"} {
    set mode post-map
} elseif {[llength $quartus(args)] != 1} {error "Expected fresh audit directory and optional post-map"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
set status [catch {
if {$mode == "post-map"} {create_timing_netlist -post_map} else {create_timing_netlist}
set banks [get_registers {*|vram_commands|*bank_enable*}]
set count [get_collection_size $banks]
puts "VRAM_LOCAL_ENABLE_REGISTERS $count"
if {$count < 8} {error "Preserved local enables were removed or merged"}
if {$mode == "post-fit"} {
read_sdc
update_timing_netlist
foreach {label from to} {
    advance-local {*|vram_cdc|src_state[*] *|vram_cdc|response_sync[1] *|vram_cdc|request_toggle *|vram_commands|state[*] *|vram_commands|write} {*|vram_commands|*bank_enable*}
    local-dout {*|vram_commands|*bank_enable*} {*|vram_commands|dout[*]}
    data-dout {*|vram_cdc|s_rsp_rdata[*]} {*|vram_commands|dout[*]}
    bundle-data {*|vram_cdc|response_payload[*]} {*|vram_cdc|s_rsp_rdata[*]}
    all-dout {*} {*|vram_commands|dout[*]}
} {
    set starts [get_registers $from]
    set ends [get_registers $to]
    puts "VRAM_RESPONSE_SCOPE $label [get_collection_size $starts] [get_collection_size $ends]"
    if {[get_collection_size $starts] == 0 || [get_collection_size $ends] == 0} {
        error "Missing fitted path scope: $label"
    }
    foreach kind {setup hold} {
        report_timing -$kind -from $starts -to $ends -npaths 8 -detail full_path \
            -file [file join $output ${label}-${kind}.txt]
    }
}
}
} result]
catch {delete_timing_netlist}
# Also avoid implicit exporting on any missing/failed timing database.
project_close -dont_export_assignments
if {$status} {error $result}
