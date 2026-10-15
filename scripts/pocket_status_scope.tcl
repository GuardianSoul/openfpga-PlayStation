# Only single-bit request/ACK first stages are asynchronous. Data is stored
# in a dual-clock M10K, read after two destination sync stages plus one edge,
# and its bank is not rewritten until that read ACK crosses two source stages.
# Local RAM address/enables/outputs, later synchronizer stages and consumers
# remain timed. No payload or complete clock domain is false-pathed.
set status_root {core_top:ic|psx_status_cdc:status_readback|}
proc status_reg {root name width} {
    if {$width==1} {
        set patterns [list "${root}${name}" "${root}${name}~DUPLICATE*"]
        set rx [format {^(.*)\|%s(~DUPLICATE[0-9]*)?$} $name]
    } else {
        set patterns [list [format {%s%s[*]} $root $name] [format {%s%s[*]~DUPLICATE*} $root $name]]
        set rx [format {^(.*)\|%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $name]
    }
    set r [get_registers -nowarn $patterns]
    set bits {};set instances {}
    foreach_in_collection n $r {
        set physical [get_node_info -name $n]
        if {$width==1} {
            if {![regexp $rx $physical ignored instance replica]} {error "Unexpected status register $physical"}
            set bit 0
        } else {
            if {![regexp $rx $physical ignored instance bit replica]} {error "Unexpected status register $physical"}
        }
        lappend bits $bit;lappend instances $instance
    }
    set expected {};for {set i 0} {$i<$width} {incr i} {lappend expected $i}
    if {[lsort -integer -unique $bits] ne $expected ||
        [lsort -unique $instances] ne [list [string trimright $root |]]} {
        error "Missing/ambiguous status logical geometry $name: $bits $instances"
    }
    puts "STATUS_GEOMETRY $name logical_$width physical_[get_collection_size $r]"
    return $r
}
set status_source_clock [get_clocks {*cpu_pll*general[0]*OUTPUT_COUNTER*divclk}]
set status_host_clock [get_clocks clk_74a]
if {[get_collection_size $status_source_clock]!=1 || [get_collection_size $status_host_clock]!=1} {
    error {Status channel requires the reviewed source/host clocks}
}
foreach_in_collection c $status_source_clock {
    if {abs([get_clock_info -period $c]-29.5251)>0.003} {error {Status source clock changed}}
}
foreach_in_collection c $status_host_clock {
    if {abs([get_clock_info -period $c]-13.468)>0.003} {error {Status host clock changed}}
}
foreach {launch first second} {request request_meta request_sync acknowledge acknowledge_meta acknowledge_sync} {
    set from [status_reg $status_root $launch 1]
    set to [status_reg $status_root $first 1]
    status_reg $status_root $second 1
    set_false_path -from $from -to $to
    puts "STATUS_FIRST_STAGE $launch $first"
}
set status_reset [get_registers -nowarn {*|config_reset}]
if {[get_collection_size $status_reset]!=1} {error {Missing registered status reset origin}}
foreach pipe {src_reset_pipe dst_reset_pipe} {
    set to [status_reg $status_root $pipe 3]
    set_false_path -from $status_reset -to $to
    puts "STATUS_ASYNC_RESET $pipe"
}
