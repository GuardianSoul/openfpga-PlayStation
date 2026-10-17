# Protected copied routed project only. Exact existing53-cell Native profile.
# Source SHA identities, lanes, physical copies, ordinary clocks and150ps are
# checked; no timing or implementation change. The original3-cell auditor is
# intentionally separate. Requires sha256sum in PATH (fail closed if absent).
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh group audit output and optional fault"}
lassign $quartus(args) output fault
if {$fault ni {{} --fault-missing-cell --fault-source-lane --fault-hidden-path}} {error "Unknown group identity fault"}
if {[file exists $output]} {error "Fresh group identity output required"}
file mkdir $output
proc group53_key {name} {
 if {$name eq {ic|psx|component_core|idma|pocket_hold_dma11|identity_lut}} {return {dma 11}}
 if {$name eq {ic|psx|component_core|igpu|igpu_vram2cpu|pocket_hold_vram_empty|identity_lut}} {return {gpu 0}}
 if {[regexp {^ic\|native_memory\|controller\|pocket_hold_sdr([0-9]+)_cell\|identity_lut$} $name -> bit] && $bit<32} {return [list sdr $bit]}
 if {[regexp {^ic\|psx\|component_core\|icd_top\|[\\]pocket_hold_cd_lba:([0-9]+):cell\|identity_lut$} $name -> bit] && $bit<19} {return [list cd $bit]}
 error "Unknown group identity leaf $name"
}
proc group53_expected {} {
 set keys {{dma 11} {gpu 0}}
 foreach {label width} {sdr 32 cd 19} {for {set i 0} {$i<$width} {incr i} {lappend keys [list $label $i]}}
 return [lsort $keys]
}
proc group53_reg {label bit direction name} {
 set core {core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|}
 switch $label {
  dma {set owner ${core}dma:idma|;set stem [expr {$direction eq "source" ? {nextAddr} : {dmaSettings\.D_MADR}}]}
  sdr {set owner {core_top:ic|psx_native_memory:native_memory|psx_sdram_ctrl:controller|};set stem [expr {$direction eq "source" ? {wdata} : {dq_out}}];if {$direction eq "target"} {set bit [expr {$bit%16}]}}
  cd {set owner ${core}cd_top:icd_top|;set stem [expr {$direction eq "source" ? {currentLBA} : {readLBA}}]}
  gpu {
   if {$direction eq "target"} {
    if {[string first ${core}dma:idma| $name]==0 || [string first ${core}gpu:igpu| $name]==0} {return}
    error "Unknown GPU empty consumer $name"
   }
   set owner ${core}gpu:igpu|gpu_vram2cpu:igpu_vram2cpu|SyncFifoFallThrough:ififo|
   if {[string first $owner $name]!=0 || ![regexp {^Empty(~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} [string range $name [string length $owner] end]]} {error "Group identity source wiring differs $label/$bit/$name"}
   return
  }
  default {error "Unknown group identity bank"}
 }
 if {[string first $owner $name]!=0 || ![regexp [format {^%s\[%s\](~DUPLICATE[0-9]*|~_Duplicate_[0-9]+)?$} $stem $bit] [string range $name [string length $owner] end]]} {error "Group identity $direction wiring differs $label/$bit/$name"}
}
proc group53_names {collection} {
 set out {};foreach_in_collection n $collection {lappend out [get_node_info -name $n]};return [lsort -unique $out]
}
proc group53_validate_sources {} {
 set expected {
  dma.vhd 8326e23112bfa837b339ff90bbaf7c6890beac0e492a094e110b7e5f16f77d40
  psx_sdram_ctrl.sv 71eac7a9470be51a74a0cc636563678a6e79ed5784da49f19651162c508ef07c
  cd_top.vhd 08fe41425ccc96103b19d11fc7584fe3a98e6c05efdfc7305d33b193da9b6b87
  gpu_vram2cpu.vhd 897159344d4d60243e6e23f27c21b337c8f70a429ed946e0c0e4cf7b10e63640
  psx_hold_cell.sv a5533e51cf97214e5f113dab5ab7c26f907e48f4fa435af1ba8bb61884b2f46d
 }
 set seen {};set sha [auto_execok sha256sum]
 if {$sha eq {}} {error "sha256sum required for group source identities"}
 foreach kind {VHDL_FILE SYSTEMVERILOG_FILE} {
  foreach_in_collection row [get_all_global_assignments -name $kind] {
   set path [lindex $row 2];set name [file tail $path]
   if {![dict exists $expected $name]} {continue}
   if {[dict exists $seen $name] || ![file isfile $path]} {error "Group identity source assignment differs $name"}
   set result [exec $sha -- $path]
   if {![regexp {^([0-9a-f]{64})[ \t]} $result -> digest] || $digest ne [dict get $expected $name]} {error "Group identity source identity differs $name"}
   dict set seen $name $path
  }
 }
 if {[lsort [dict keys $seen]] ne [lsort [dict keys $expected]]} {error "Group identity source profile incomplete"}
 puts GROUP_IDENTITY_SOURCE_PROFILE_5
}
project_open apf_top
try {
 group53_validate_sources
 create_timing_netlist
 read_sdc
 if {![info exists pocket_cpu_hold_ns] || $pocket_cpu_hold_ns!=.15} {error "Group identity requires150ps reserve"}
 set cells [dict create]
 foreach_in_collection cell [get_cells -compatibility_mode -nowarn {*pocket_hold*identity_lut}] {
  set name [get_cell_info -name $cell];set key [group53_key $name]
  if {[get_cell_info -wysiwyg_type $cell] ne {cyclonev_lcell_comb} || [dict exists $cells $key]} {error "Invalid group identity atom"}
  dict set cells $key [list $cell $name]
 }
 if {$fault eq "--fault-missing-cell"} {dict unset cells [lindex [dict keys $cells] 0]}
 if {[lsort [dict keys $cells]] ne [group53_expected]} {error "Group identity cell census incomplete"}
 set groups {};set census [open [file join $output census.tsv] w]
 puts $census "label\tbit\tleaf\tinput_pin\toutput_pin\tsources\ttargets"
 dict for {key value} $cells {
  lassign $key label bit;lassign $value cell leaf
  set ip [get_cell_info -in_pins $cell];set op [get_cell_info -out_pins $cell]
  if {[get_collection_size $ip]!=1 || [get_collection_size $op]!=1} {error "Group identity pin geometry differs"}
  set ipname [lindex [group53_names $ip] 0];set opname [lindex [group53_names $op] 0]
  if {![regexp {\|data[a-g]$} $ipname] || ![string match *|combout $opname]} {error "Group identity connected pin differs"}
  # Keep collection handles: raw foreach node IDs are not query collections.
  set src [get_fanins -synch $ip];set dst [get_fanouts $op]
  set sources [group53_names $src];set targets [group53_names $dst]
  if {[llength $sources]<1 || [llength $sources]>4 || ![llength $targets]} {error "Group identity endpoints missing"}
  set source_bit $bit
  if {$fault eq "--fault-source-lane" && $key eq {sdr 0}} {set source_bit 1}
  foreach name $sources {group53_reg $label $source_bit source $name}
  foreach name $targets {group53_reg $label $bit target $name}
  foreach_in_collection n $src {if {[get_node_info -type $n] ne "reg"} {error "Group identity non-register source"}}
  foreach_in_collection n $dst {if {[get_node_info -type $n] ne "reg"} {error "Group identity non-register target"}}
  lappend groups [list $key $leaf $op $src $dst $sources $targets]
  puts $census [join [list $label $bit $leaf $ipname $opname $sources $targets] "\t"]
 }
 close $census
 if {$fault eq "--fault-hidden-path"} {
  foreach group $groups {
   lassign $group key leaf op src dst
   if {$key eq {cd 0}} {set_false_path -from $src -through $op -to $dst;break}
  }
 }
 set f [open [file join $output paths.tsv] w]
 puts $f "corner\tlabel\tbit\tkind\tslack_ns\tuncertainty_ns\tfrom\tto\tfrom_clock\tto_clock\tleaf"
 set corner 0;set violations {}
 foreach_in_collection condition [get_available_operating_conditions] {
  set_operating_conditions $condition
  update_timing_netlist
  foreach group $groups {
   lassign $group key leaf op src dst sources targets;lassign $key label bit
   foreach kind {setup hold} {
    if {[get_collection_size [get_timing_paths -false_path -$kind -from $src -through $op -to $dst -npaths 1]]} {error "Hidden group identity path"}
    set cap [expr {2*[llength $targets]+8}]
    set paths [get_timing_paths -$kind -from $src -through $op -to $dst -npaths $cap -nworst 1]
    if {[get_collection_size $paths]==$cap} {error "Group identity path census truncated"}
    set seen_src {};set seen_dst {}
    foreach_in_collection path $paths {
     set from [get_node_info -name [get_path_info -from $path]];set to [get_node_info -name [get_path_info -to $path]]
     lappend seen_src $from;lappend seen_dst $to
     set fc [get_clock_info -name [get_path_info -from_clock $path]];set tc [get_clock_info -name [get_path_info -to_clock $path]]
     set index [dict get {dma 0 sdr 2 cd 0 gpu 1} $label]
     set expected [format {ic|cpu_pll|general[%s].gpll~PLL_OUTPUT_COUNTER|divclk} $index]
     set allowed [list $expected]
     if {$label eq "gpu"} {lappend allowed {ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}}
     if {$fc ne $expected || $tc ni $allowed} {error "Group identity clock contract differs"}
     set seen 0
     foreach_in_collection point [get_path_info -arrival_points $path] {
      if {[get_point_info -type $point] ne "cell"} {continue}
      set node [get_point_info -node $point]
      if {$node eq {} || [get_node_info -type $node] ne "pin"} {continue}
      if {[get_node_info -name $node] eq [lindex [group53_names $op] 0]} {incr seen}
     }
     if {$seen!=1} {error "Group identity absent from timed path"}
     set unc {};set n 0
     foreach_in_collection point [get_path_info -required_points $path] {
      if {[get_point_info -type $point] eq "unc"} {set unc [get_point_info -incremental_delay $point];incr n}
     }
     if {$n!=1 || ($kind eq "hold" && $unc<.149)} {error "Group identity CPU margin absent"}
     set slack [get_path_info -slack $path]
     if {$slack<=0} {lappend violations [list $corner $key $kind $slack]}
     puts $f [join [list $corner $label $bit $kind $slack $unc $from $to $fc $tc $leaf] "\t"]
    }
    if {[lsort -unique $seen_src] ne $sources || [lsort -unique $seen_dst] ne $targets} {error "Group identity endpoint timing coverage incomplete"}
   }
  }
  puts "GROUP_IDENTITY_CORNER $corner"
  incr corner
 }
 close $f
 if {$corner!=4} {error "Missing group identity corners"}
 if {[llength $violations]} {error "Group identity ordinary timing fails: $violations"}
 puts PASS_GROUP_IDENTITY53_TIMING
} finally {delete_timing_netlist;project_close -dont_export_assignments}
