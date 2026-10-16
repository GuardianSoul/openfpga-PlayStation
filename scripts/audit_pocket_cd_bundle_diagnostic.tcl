package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error "Expected fresh output and optional diagnostic fault"}
set fault [lindex $quartus(args) 1]
if {$fault ni {{} --fault-hidden-local --fault-relaxed-local --fault-hidden-pair}} {error "Unknown CD diagnostic fault"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh diagnostic output required"}
file mkdir $output
project_open apf_top
try {
 create_timing_netlist
 read_sdc
 if {$fault eq "--fault-hidden-local"} {
  set_false_path -from $cd_bundle_source(host_core) -to [cd_bundle_exact $cd_bundle_local_names(host_core)]
 }
 if {$fault eq "--fault-relaxed-local"} {
  set_min_delay -from $cd_bundle_source(host_core) -to [cd_bundle_exact $cd_bundle_local_names(host_core)] -13
 }
 if {$fault eq "--fault-hidden-pair"} {
  set a [cd_bundle_exact {{core_top:ic|psx_cd_stream:disc_stream|m_end[0]}}]
  set b [cd_bundle_exact {{core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|cd_top:icd_top|trackInfo_DataA[0]}}]
  set_false_path -from $a -to $b
 }
 update_timing_netlist
 set f [open [file join $output paths.tsv] w]
 puts $f "corner\tdirection\tkind\tclass\tslack_ns\tdata_ns\trelationship_ns\tfrom\tto\tfrom_clock\tto_clock"
 set corner 0;set violations {}
 foreach_in_collection cond [get_available_operating_conditions] {
  set_operating_conditions $cond;update_timing_netlist
  foreach direction {host_core core_host} {
   set from $cd_bundle_source($direction);set to $cd_bundle_target($direction)
   set crossed {};foreach_in_collection r $to {lappend crossed [get_node_info -name $r]}
   set local $cd_bundle_local_names($direction)
   set expected [lsort -unique [concat $crossed $local]]
   foreach kind {setup hold} {
    set observed {};set observed_pairs {};set count 0
    foreach_in_collection p [get_timing_paths -$kind -from $from -npaths 10000] {
     incr count
     set src [get_node_info -name [get_path_info -from $p]]
     set dst [get_node_info -name [get_path_info -to $p]];lappend observed $dst
     lappend observed_pairs [list $src $dst]
     set a [get_clock_info -name [get_path_info -from_clock $p]]
     set b [get_clock_info -name [get_path_info -to_clock $p]]
     set slack [get_path_info -slack $p];set delay [get_path_info -data_delay $p]
     set relation [get_path_info -clock_relationship $p]
     if {$a eq $b} {
      set class local
      if {$dst ni $local} {error "Cross scope contains a local consumer $src -> $dst"}
      set multi {};foreach field {setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle} {lappend multi [get_path_info -$field $p]}
      set period [get_clock_info -period [get_path_info -from_clock $p]]
      set edge [expr {$kind eq "setup" ? $period : 0}]
      if {$multi ne {1 1 0 0} || abs($relation-$edge)>.002} {error "Relaxed local CD relationship $src -> $dst $kind $relation $multi"}
     } else {
      set class crossing
      if {$dst ni $crossed} {error "Unscoped CD crossing $src -> $dst"}
      if {$direction eq "host_core"} {set fc clk_74a;set tc {ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}} else {set tc clk_74a;set fc {ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}}
      if {$a ne $fc || $b ne $tc} {error "Unknown CD clock pair $a $b"}
      if {$delay>13} {lappend violations "FLIGHT/$corner/$direction/$delay/$src/$dst"}
     }
     if {$slack<=0} {lappend violations "SLACK/$corner/$kind/$slack/$src/$dst"}
     puts $f [join [list $corner $direction $kind $class $slack $delay $relation $src $dst $a $b] "\t"]
    }
    if {$count==10000 || [lsort -unique $observed] ne $expected} {error "CD endpoint timing coverage incomplete $direction $kind"}
    if {[lsort -unique $observed_pairs] ne $cd_bundle_pairs($direction)} {error "CD physical pair timing coverage incomplete $direction $kind"}
   }
  }
  foreach stem {h_req_s h_done_s h_mdone_s c_resp_s c_mount_s} {
   cd_bundle_regs $stem 3
   for {set bit 1} {$bit<=2} {incr bit} {
    set from [get_registers -nowarn [list [format {core_top:ic|psx_cd_stream:disc_stream|%s[%d]} $stem [expr {$bit-1}]]]]
    set to [get_registers -nowarn [list [format {core_top:ic|psx_cd_stream:disc_stream|%s[%d]} $stem $bit]]]
    foreach kind {setup hold} {
     set paths [get_timing_paths -$kind -from $from -to $to -npaths 100]
     if {[get_collection_size $paths]<1} {error "Hidden CD toggle settling path $stem $bit $kind"}
     foreach_in_collection p $paths {
      set src [get_node_info -name [get_path_info -from $p]];set dst [get_node_info -name [get_path_info -to $p]]
      set a [get_clock_info -name [get_path_info -from_clock $p]];set b [get_clock_info -name [get_path_info -to_clock $p]]
      set period [get_clock_info -period [get_path_info -from_clock $p]]
      set relation [get_path_info -clock_relationship $p];set edge [expr {$kind eq "setup" ? $period : 0}]
      set multi {};foreach field {setup_end_multicycle setup_start_multicycle hold_end_multicycle hold_start_multicycle} {lappend multi [get_path_info -$field $p]}
      if {$a ne $b || $multi ne {1 1 0 0} || abs($relation-$edge)>.002} {error "Relaxed CD toggle settling relationship"}
      set slack [get_path_info -slack $p];if {$slack<=0} {lappend violations "SYNC/$corner/$stem/$bit/$kind/$slack"}
      puts $f [join [list $corner $stem $kind sync $slack [get_path_info -data_delay $p] $relation $src $dst $a $b] "\t"]
     }
    }
   }
  }
  report_net_delay -nworst 500 -file [file join $output corner${corner}-net.txt]
  report_max_skew -npaths 500 -file [file join $output corner${corner}-skew.txt]
  incr corner
 }
 close $f
 set v [open [file join $output violations.txt] w];puts $v [join $violations "\n"];close $v
 if {$corner!=4} {error "Expected four CD diagnostic corners"}
 if {[llength $violations]} {error "CD diagnostic timing/physical flight violations: $violations"}
 puts "PASS_CD_CONDITIONAL_DIAGNOSTIC corners4 flight13 ordinary_local_edges"
 # Net-delay reports can say No nets found for combinational source-to-D
 # cones. The independent per-path data_delay<=13 gate above is mandatory;
 # presence of a set_net_delay command alone is not physical flight proof.
} finally {catch {delete_timing_netlist};project_close -dont_export_assignments}
