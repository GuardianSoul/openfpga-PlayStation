# Private copied routed project only. Expected scoped video.sdc already saved.
# quartus_sta -t <this script> <fresh output directory> [--faults]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected output directory and optional --faults"}
set faults 0
if {[llength $quartus(args)]==2} {
    if {[lindex $quartus(args) 1] ne "--faults"} {error "Unknown video packet audit option"}
    set faults 1
}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh video audit directory required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set scope {*|gpu_videoout_async:igpu_videoout_async|}
set fifo_scope ${scope}psx_video_config_cdc:video_config|dual_clock_fifo:snapshots|
set fifo_keepers [get_keepers -nowarn ${fifo_scope}*]
set video_regs [get_registers -nowarn ${scope}*]
if {[get_collection_size $fifo_keepers]<20 || [get_collection_size $video_regs]<100} {error "Missing video FIFO/consumer geometry"}
set first {};set second {}
foreach {a b} {wr_addr_gray_rd wr_addr_gray_rd_r rd_addr_gray_wr rd_addr_gray_wr_r} {
    # Validate logical coverage and named physical copies in the same scope
    # routine that applies the bounds; include every copy in timing reports.
    video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $a] 4
    video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $b] 4
    lappend first [format {%s%s[*]} $fifo_scope $a]
    lappend second [format {%s%s[*]} $fifo_scope $b]
}
set first [get_registers $first];set second [get_registers $second]
if {[get_collection_size $first]<8 || [get_collection_size $second]<8} {error "Missing physical video Gray stages"}
set status_first [get_registers [list ${scope}psx_video_config_cdc:video_config|source_online_meta ${scope}psx_video_config_cdc:video_config|destination_online_meta]]
set status_second [get_registers [list ${scope}psx_video_config_cdc:video_config|source_online_sync ${scope}psx_video_config_cdc:video_config|destination_online_sync]]
if {[get_collection_size $status_first]!=2 || [get_collection_size $status_second]!=2} {error "Video online stages not2/2"}
set fault_meta [get_registers ${scope}video_config_fault_meta]
set fault_sync [get_registers ${scope}video_config_fault_sync]
set reset_meta [get_registers ${scope}reset_s2]
set reset_sync [get_registers ${scope}reset_s1]
set reset_final [get_registers ${scope}reset_sampled]
set warm0 [get_registers ${scope}video_config_warm0]
set warm1 [get_registers ${scope}video_config_warm1]
foreach collection [list $fault_meta $fault_sync $reset_meta $reset_sync $reset_final $warm0 $warm1] {
    if {[get_collection_size $collection]!=1} {error "Missing video level/reset/warm-up stage"}
}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach {gray binary first_stage} {wr_addr_gray wr_addr wr_addr_gray_rd rd_addr_gray rd_addr rd_addr_gray_wr} {
        set source [video_cfg_gray $gray $binary]
        set target [video_cfg_regs [format {dual_clock_fifo:snapshots|%s[*]} $first_stage] 4]
        foreach kind {setup hold} {
            set cuts [get_timing_paths -false_path -$kind -from $source -to $target -npaths 1]
            if {[get_collection_size $cuts]!=0} {error "Video configuration Gray bus was false-pathed ($kind)"}
            set timed [get_timing_paths -$kind -from $source -to $target -npaths 1]
            if {[get_collection_size $timed]!=1} {error "Missing timed video Gray bus ($kind)"}
        }
    }
    foreach kind {setup hold} {
        report_timing -$kind -to $first -npaths 100 -detail summary -file [file join $output corner${corner}-gray-first-${kind}.txt]
        report_timing -$kind -from $first -to $second -npaths 100 -detail summary -file [file join $output corner${corner}-gray-second-${kind}.txt]
        report_timing -$kind -to $status_first -npaths 100 -detail summary -file [file join $output corner${corner}-online-first-${kind}.txt]
        report_timing -$kind -from $status_first -to $status_second -npaths 100 -detail summary -file [file join $output corner${corner}-online-second-${kind}.txt]
        report_timing -$kind -to $fault_meta -npaths 20 -detail full_path -file [file join $output corner${corner}-fault-first-${kind}.txt]
        report_timing -$kind -from $fault_meta -to $fault_sync -npaths 20 -detail full_path -file [file join $output corner${corner}-fault-second-${kind}.txt]
        report_timing -$kind -to $reset_meta -npaths 20 -detail full_path -file [file join $output corner${corner}-reset-first-${kind}.txt]
        report_timing -$kind -from $reset_meta -to $reset_sync -npaths 20 -detail full_path -file [file join $output corner${corner}-reset-second-${kind}.txt]
        report_timing -$kind -from $reset_sync -to $reset_final -npaths 20 -detail full_path -file [file join $output corner${corner}-reset-third-${kind}.txt]
        report_timing -$kind -to $warm0 -npaths 20 -detail full_path -file [file join $output corner${corner}-warm-first-${kind}.txt]
        report_timing -$kind -from $warm0 -to $warm1 -npaths 20 -detail full_path -file [file join $output corner${corner}-warm-second-${kind}.txt]
        # Includes RAM keepers as well as FFs, and ALL launch clocks: do not
        # hide an unsafe upstream host control feeding a FIFO write port.
        report_timing -$kind -to $fifo_keepers -npaths 40 -detail full_path -file [file join $output corner${corner}-fifo-all-input-${kind}.txt]
        report_timing -$kind -from $fifo_keepers -to $video_regs -to_clock $video_cfg_dst_clock -npaths 40 -detail full_path -file [file join $output corner${corner}-consumer-${kind}.txt]
        report_timing -$kind -npaths 20 -detail full_path -file [file join $output corner${corner}-global-${kind}.txt]
    }
    foreach pipe {src_reset_pipe dst_reset_pipe} {
        set p [video_cfg_regs [format {%s[2]} $pipe] 1]
        foreach kind {recovery removal} {
            report_timing -$kind -from $p -npaths 100 -detail full_path -file [file join $output corner${corner}-${pipe}-${kind}.txt]
        }
        foreach kind {setup hold} {
            report_timing -$kind -from [video_cfg_regs [format {%s[*]} $pipe] 3] -to [video_cfg_regs [format {%s[*]} $pipe] 3] -npaths 20 -detail full_path -file [file join $output corner${corner}-${pipe}-shift-${kind}.txt]
        }
    }
    report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output corner${corner}-skew.txt]
    incr corner
}
if {$corner!=4} {error "Expected four video audit conditions"}
if {$faults} {
    set from [video_cfg_gray wr_addr_gray wr_addr]
    set to [video_cfg_regs {dual_clock_fifo:snapshots|wr_addr_gray_rd[*]} 4]
    set_net_delay -max -from $from -to $to 0.001
    set_max_skew -from $from -to $to 0.001
    update_timing_netlist
    report_net_delay -nworst 500 -file [file join $output planted-net.txt]
    report_max_skew -npaths 500 -detail full_path -file [file join $output planted-skew.txt]
}
delete_timing_netlist
project_close -dont_export_assignments
