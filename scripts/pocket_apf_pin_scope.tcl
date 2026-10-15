# Explicit diagnostic receiver/PCB budgets. No external electrical signoff.
# APF uses both edges of its quarter-phase forwarded video clock. Audio is
# checked at every rising MCLK, conservatively covering every /4 SCLK phase.
foreach name {video_setup video_hold audio_setup audio_hold board_min board_max} {
    set value [set apf_pin_$name]
    if {$value<0 || $value>100} {error "Invalid APF pin requirement $name"}
}
if {$apf_pin_board_min>$apf_pin_board_max || $apf_pin_board_max>2 || $apf_pin_falling_audio ni {0 1}} {error "Invalid APF pin model"}
if {[get_collection_size [get_clocks -nowarn {pocket_apf_video_pin pocket_apf_audio_pin}]]!=0} {error "Duplicate APF pin clocks"}
set apf_pin_video_master [get_clocks {*video_pll*general[0]*OUTPUT_COUNTER*divclk}]
set apf_pin_video_quarter [get_clocks {*video_pll*general[1]*OUTPUT_COUNTER*divclk}]
set apf_pin_audio_master [get_clocks {*audio_pll*general[0]*OUTPUT_COUNTER*divclk}]
foreach name {video_master video_quarter audio_master} {
    set clocks [set apf_pin_$name]
    if {[get_collection_size $clocks]!=1} {error "Ambiguous APF clock $name"}
    foreach_in_collection c $clocks {
        set apf_pin_${name}_period [get_clock_info -period $c]
        set apf_pin_${name}_wave [get_clock_info -waveform $c]
        set apf_pin_${name}_source [get_clock_info -targets $c]
    }
    if {[get_collection_size [set apf_pin_${name}_source]]!=1} {error "Ambiguous APF clock source"}
}
if {$apf_pin_video_master_period<18.5 || $apf_pin_video_master_period>19.0 ||
    abs($apf_pin_video_master_period-$apf_pin_video_quarter_period)>0.002 ||
    abs([lindex $apf_pin_video_quarter_wave 0]-[lindex $apf_pin_video_master_wave 0]-$apf_pin_video_master_period/4)>0.002 ||
    abs([lindex $apf_pin_video_quarter_wave 1]-[lindex $apf_pin_video_master_wave 1]-$apf_pin_video_master_period/4)>0.002 ||
    $apf_pin_audio_master_period<81.2 || $apf_pin_audio_master_period>81.6} {error "APF clock phase/rate outside reviewed geometry"}
set apf_pin_video_outputs [get_ports {scal_vid[*] scal_vs scal_hs scal_de scal_skip}]
set apf_pin_audio_outputs [get_ports {scal_auddac scal_audlrck}]
set apf_pin_video_clock_port [get_ports scal_clk]
set apf_pin_audio_clock_port [get_ports scal_audmclk]
if {[get_collection_size $apf_pin_video_outputs]!=16 || [get_collection_size $apf_pin_audio_outputs]!=2 ||
    [get_collection_size $apf_pin_video_clock_port]!=1 || [get_collection_size $apf_pin_audio_clock_port]!=1} {error "Missing APF output pin geometry"}
create_generated_clock -name pocket_apf_video_pin -source $apf_pin_video_quarter_source -divide_by 1 $apf_pin_video_clock_port
create_generated_clock -name pocket_apf_audio_pin -source $apf_pin_audio_master_source -divide_by 1 $apf_pin_audio_clock_port
derive_clock_uncertainty
set apf_pin_skew [expr {$apf_pin_board_max-$apf_pin_board_min}]
foreach {scope ports} [list video $apf_pin_video_outputs audio $apf_pin_audio_outputs] {
    set clock [get_clocks pocket_apf_${scope}_pin]
    set maximum [expr {[set apf_pin_${scope}_setup]+$apf_pin_skew}]
    set minimum [expr {-[set apf_pin_${scope}_hold]-$apf_pin_skew}]
    set_output_delay -clock $clock -max $maximum $ports
    set_output_delay -clock $clock -min $minimum $ports
    if {$scope eq "video"} {
        set_output_delay -clock $clock -clock_fall -max $maximum -add_delay $ports
        set_output_delay -clock $clock -clock_fall -min $minimum -add_delay $ports
    }
}
puts "APF_PIN_MODEL DIAGNOSTIC_ONLY VIDEO_PINS16_BOTH_EDGES AUDIO_PINS2_EVERY_MCLK FALLING_AUDIO=$apf_pin_falling_audio"
