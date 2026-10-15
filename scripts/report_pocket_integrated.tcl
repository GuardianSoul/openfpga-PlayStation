# Read-only, multi-corner audit of the connected CPU/audio/VRAM experiments.
# No exceptions added. Scope failures are diagnostic failures, never closure.
package require ::quartus::project
package require ::quartus::sta
source [file join [file dirname [info script]] pocket_audio_scope.tcl]
if {[llength $quartus(args)] != 1} {error "Expected audit directory"}
set output [file normalize [lindex $quartus(args) 0]]
file mkdir $output
project_open apf_top
set status [catch {
    create_timing_netlist
    read_sdc
    update_timing_netlist
    set tags [get_registers {*|itagram|* *|dpram:itagram|*}]
    set tag_count [get_collection_size $tags]
    # VHDL hierarchy may retain either the dpram entity prefix or the label.
    if {$tag_count == 0} {set tags [get_registers {*itagram*}]; set tag_count [get_collection_size $tags]}
    if {$tag_count == 0} {error "Missing connected CPU tag RAM"}
    puts "POCKET_TAG_SCOPE $tag_count"
    set banks [get_registers {*|vram_commands|*bank_enable*}]
    if {[get_collection_size $banks] != 8} {error "Missing eight local VRAM enables"}
    puts "POCKET_VRAM_SCOPE [get_collection_size $banks]"
    foreach {source destination} {wr_gray wr_gray_meta rd_gray rd_gray_meta} {
        set bus($source) [pocket_audio_gray_bus {*audio_stream*|} [string range $source 0 1]]
        set bus($destination) [get_registers "*audio_stream*|${destination}\[*\]"]
        puts "POCKET_AUDIO_SCOPE $source [get_collection_size $bus($source)] [get_collection_size $bus($destination)]"
        if {[get_collection_size $bus($destination)] != 7} {error "Audio receiver scope changed"}
    }
    set clocks [get_clocks {*cpu_pll*divclk}]
    if {[get_collection_size $clocks] != 3} {error "Missing CPU PLL clocks"}
    set corner 0
    foreach_in_collection op [get_available_operating_conditions] {
        set_operating_conditions $op
        update_timing_netlist
        puts "POCKET_CORNER $corner [get_operating_conditions_info $op -model]"
        foreach kind {setup hold} {
            report_timing -$kind -from $tags -npaths 12 -detail full_path -file $output/corner${corner}-tag-from-${kind}.txt
            report_timing -$kind -to $tags -npaths 12 -detail full_path -file $output/corner${corner}-tag-to-${kind}.txt
            foreach {source destination} {wr_gray wr_gray_meta rd_gray rd_gray_meta} {
                report_timing -$kind -from $bus($source) -to $bus($destination) -npaths 7 -detail full_path \
                    -file $output/corner${corner}-${source}-${kind}.txt
            }
            report_timing -$kind -npaths 24 -detail full_path -file $output/corner${corner}-global-${kind}.txt
            report_timing -$kind -from_clock $clocks -to_clock $clocks -npaths 24 -detail full_path \
                -file $output/corner${corner}-cpu-related-${kind}.txt
            set index 0
            foreach_in_collection clock $clocks {
                puts "POCKET_INTRA_CLOCK $index [get_clock_info -name $clock]"
                report_timing -$kind -from_clock $clock -to_clock $clock -npaths 12 -detail full_path \
                    -file $output/corner${corner}-cpu${index}-intra-${kind}.txt
                incr index
            }
            report_timing -$kind -from [get_registers {*|vram_cdc|src_state[*] *|vram_cdc|response_sync[1] *|vram_cdc|request_toggle *|vram_commands|state[*] *|vram_commands|write}] \
                -to $banks -npaths 12 -detail full_path -file $output/corner${corner}-vram-enable-${kind}.txt
            report_timing -$kind -from $banks -to [get_registers {*|vram_commands|dout[*]}] \
                -npaths 12 -detail full_path -file $output/corner${corner}-vram-dout-${kind}.txt
            report_timing -$kind -from [get_registers {*|vram_cdc|s_rsp_rdata[*]}] \
                -to [get_registers {*|vram_commands|dout[*]}] -npaths 12 -detail full_path \
                -file $output/corner${corner}-vram-data-${kind}.txt
            report_timing -$kind -from [get_registers {*|vram_cdc|response_payload[*]}] \
                -to [get_registers {*|vram_cdc|s_rsp_rdata[*] *|vram_cdc|s_rsp_error}] \
                -npaths 12 -detail full_path -file $output/corner${corner}-vram-response-bundle-${kind}.txt
            report_timing -$kind -from [get_registers {*|vram_cdc|request_payload[*]}] \
                -to [get_registers {*|vram_cdc|d_addr[*] *|vram_cdc|d_write_q *|vram_cdc|d_halfwords[*] *|vram_cdc|d_wdata[*] *|vram_cdc|d_byte_en[*]}] \
                -npaths 12 -detail full_path -file $output/corner${corner}-vram-request-bundle-${kind}.txt
        }
        report_net_delay -nworst 20 -file $output/corner${corner}-audio-net-delay.txt
        report_max_skew -npaths 10 -detail full_path -file $output/corner${corner}-audio-max-skew.txt
        report_timing -recovery -npaths 16 -file $output/corner${corner}-recovery.txt
        report_timing -removal -npaths 16 -file $output/corner${corner}-removal.txt
        incr corner
    }
} result]
catch {delete_timing_netlist}
project_close -dont_export_assignments
if {$status} {error $result}
