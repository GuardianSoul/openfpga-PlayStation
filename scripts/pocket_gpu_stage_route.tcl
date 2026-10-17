# Route the complete reviewed GPU stall-buffer handoff with more minimum
# wire delay. Ordinary timing and the required150ps reserve remain active.
proc gpu_stage_profile {} {
    # read_sdc can evaluate the file in a private namespace. Do not couple
    # procedure globals to variables created in that caller's scope.
    return [list {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|gpu:igpu|gpu_pixelpipeline:igpu_pixelpipeline} \
                 {valid 1 texture 1 transparent 1 rawTexture 1 dithering 1 x 10 y 9 cr 8 cg 8 cb 8 u 8 v 8 oldPixel 15}]
}
proc gpu_stage_key {name stage} {
    lassign [gpu_stage_profile] gpu_stage_owner gpu_stage_fields
    set prefix [format {%s|} $gpu_stage_owner]
    if {[string first $prefix $name]!=0} {error "Wrong GPU handoff owner $name"}
    set suffix [string range $name [string length $prefix] end]
    # This separate cache-address bank is not a stageS handoff consumer.
    if {$stage eq "stage0" && [regexp {^stage0_textaddr_1\[0\]\[([1-9]|1[0-9])\](~DUPLICATE[0-9]*)?$} $suffix]} {
        return {}
    }
    foreach {field width} $gpu_stage_fields {
        if {$width==1} {
            if {[regexp [format {^%s_%s(~DUPLICATE[0-9]*)?$} $stage $field] $suffix]} {
                return [list $field 0]
            }
        } elseif {[regexp [format {^%s_%s\[([0-9]+)\](~DUPLICATE[0-9]*)?$} $stage $field] $suffix -> bit replica]} {
            if {$bit>=$width} {error "GPU handoff bit outside reviewed width $name"}
            return [list $field $bit]
        }
    }
    error "Unknown GPU handoff register $name"
}
proc gpu_stage_exact {names} {
    set names [lsort -unique $names]
    if {![llength $names]} {error "Empty GPU handoff collection"}
    set result [get_registers -no_duplicates -nowarn $names]
    if {[get_collection_size $result]!=[llength $names]} {error "Missing exact GPU handoff register"}
    return $result
}
proc gpu_stage_bank {stage} {
    lassign [gpu_stage_profile] gpu_stage_owner gpu_stage_fields
    set names {};set keys {}
    foreach_in_collection r [get_registers -nowarn [list [format {%s|%s_*} $gpu_stage_owner $stage]]] {
        set name [get_node_info -name $r]
        set key [gpu_stage_key $name $stage]
        if {![llength $key]} {continue}
        lappend names $name;lappend keys $key
    }
    set expected {}
    foreach {field width} $gpu_stage_fields {
        for {set bit 0} {$bit<$width} {incr bit} {lappend expected [list $field $bit]}
    }
    if {[lsort -unique $keys] ne [lsort -unique $expected]} {error "Incomplete GPU handoff bank $stage"}
    return [gpu_stage_exact $names]
}
set gpu_stage_clock [get_clocks -nowarn {{ic|cpu_pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}}]
if {[get_collection_size $gpu_stage_clock]!=1 || abs([get_clock_info -period $gpu_stage_clock]-14.76255)>.003} {
    error "GPU handoff2x clock differs"
}
if {![info exists pocket_cpu_hold_ns] || $pocket_cpu_hold_ns!=0.15} {
    error "GPU handoff routing requires unchanged150ps reserve"
}
set gpu_stage_sources [gpu_stage_bank stageS]
set gpu_stage_targets [gpu_stage_bank stage0]
set gpu_stage_target_names {}
foreach_in_collection r $gpu_stage_targets {lappend gpu_stage_target_names [get_node_info -name $r]}
set gpu_stage_pairs {};set gpu_stage_observed_targets {}
foreach_in_collection r $gpu_stage_sources {
    set name [get_node_info -name $r]
    set key [gpu_stage_key $name stageS]
    set found 0
    foreach_in_collection out [get_fanouts [gpu_stage_exact [list $name]]] {
        set target [get_node_info -name $out]
        if {$target in $gpu_stage_target_names} {
            if {[gpu_stage_key $target stage0] ne $key} {error "GPU handoff crossed field/bit lanes $name $target"}
            lappend gpu_stage_pairs [list $name $target]
            lappend gpu_stage_observed_targets $target;incr found
        }
    }
    if {!$found} {error "Missing GPU handoff consumer $name"}
}
if {[lsort -unique $gpu_stage_observed_targets] ne [lsort -unique $gpu_stage_target_names]} {
    error "GPU handoff destination has no source"
}
set gpu_stage_pairs [lsort -unique $gpu_stage_pairs]
set_net_delay -min 0.5 -from $gpu_stage_sources -to $gpu_stage_targets
puts "GPU_STAGE_ROUTE_MIN_NS0.5 LOGICAL79 FIELDS13 SOURCES_[get_collection_size $gpu_stage_sources] TARGETS_[get_collection_size $gpu_stage_targets] PAIRS_[llength $gpu_stage_pairs] CPU_HOLD_PS150"
