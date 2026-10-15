# CPU boot-reset level -> APF blanking. Only the first video-domain data
# synchronizer input is asynchronous. Later stages and RGB consumers are timed.
proc apf_blank_reg {name} {
    set r [get_registers -nowarn "core_top:ic|psx_video_blank_cdc:apf_blank_cdc|$name"]
    if {[get_collection_size $r]!=1} {error "Missing/ambiguous APF blank register $name"}
    return $r
}
set apf_blank_source [get_registers -nowarn {core_top:ic|psx_boot_gate:boot_gate|reset_pipe[2]}]
if {[get_collection_size $apf_blank_source]!=1} {error "Missing/ambiguous CPU boot reset origin"}
set apf_blank_cpu_clock [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
set apf_blank_video_clock [get_clocks {*video_pll*general[0]*OUTPUT_COUNTER*divclk}]
if {[get_collection_size $apf_blank_cpu_clock]!=1 || [get_collection_size $apf_blank_video_clock]!=1} {
    error "Missing/ambiguous APF blank clocks"
}
foreach_in_collection c $apf_blank_cpu_clock {set apf_blank_cpu_period [get_clock_info -period $c]}
foreach_in_collection c $apf_blank_video_clock {set apf_blank_video_period [get_clock_info -period $c]}
if {$apf_blank_cpu_period<25 || $apf_blank_cpu_period>35 || $apf_blank_video_period<15 || $apf_blank_video_period>20 || 2*$apf_blank_cpu_period<2*$apf_blank_video_period} {
    error "APF blank reset-pulse geometry outside tested range"
}
set apf_blank_first [apf_blank_reg blank_meta]
apf_blank_reg blank_sync
apf_blank_reg blank_video
set_false_path -from $apf_blank_source -to $apf_blank_first
puts "APF_BLANK_FIRST_STAGE 1"
