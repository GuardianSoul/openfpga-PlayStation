# PRIVATE fitted-copy audit. No saved constraints are modified.
# quartus_sta -t <script> <fresh output> [--fault-output]
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]<1 || [llength $quartus(args)]>2} {error {Expected fresh status output and optional fault}}
set fault [expr {[llength $quartus(args)]==2 ? [lindex $quartus(args) 1] : ""}]
if {$fault ni {{} --fault-output}} {error {Unknown status audit option}}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error {Fresh status audit output required}}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
if {![info exists status_root] || $status_root ne {core_top:ic|psx_status_cdc:status_readback|}} {
    error {Saved status channel scope absent}
}
set ram [get_keepers -nowarn "${status_root}*ram_block*"]
set host [get_registers -nowarn {*io_bridge_peripheral:ibs|spis_word_tx[*]}]
set raw [get_registers -nowarn {*|cd_top:icd_top|cd_hps_lba[*] *|cd_top:icd_top|cd_hps_lba[*]~DUPLICATE*}]
if {[get_collection_size $ram]==0 || [get_collection_size $host]!=32 || [get_collection_size $raw]==0} {
    error {Missing mapped status RAM, raw source or host consumer}
}
if {$fault eq "--fault-output"} {set_false_path -from $ram -to $host}
update_timing_netlist
set nodes [open [file join $output ram-nodes.tsv] w]
puts $nodes "name"
foreach_in_collection n $ram {puts $nodes [get_node_info -name $n]}
close $nodes
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    if {[get_collection_size [get_timing_paths -setup -from $raw -to $host -npaths 1]]!=0} {
        error {Raw CD diagnostic still reaches host register through logic}
    }
    foreach scope {request-later ack-later source-ram ram-host} {
        switch -- $scope {
            request-later {
                set from [status_reg $status_root request_meta 1]
                set to [status_reg $status_root request_sync 1]
            }
            ack-later {
                set from [status_reg $status_root acknowledge_meta 1]
                set to [status_reg $status_root acknowledge_sync 1]
            }
            source-ram {set from {};set to $ram}
            ram-host {set from $ram;set to $host}
        }
        foreach kind {setup hold} {
            if {$scope eq "source-ram"} {
                set paths [get_timing_paths -$kind -from_clock $status_source_clock -to $to -npaths 16]
            } else {
                set paths [get_timing_paths -$kind -from $from -to $to -npaths 16]
            }
            if {[get_collection_size $paths]==0} {error "Missing timed status $scope $kind paths"}
            foreach_in_collection path $paths {
                set a [get_clock_info -name [get_path_info -from_clock $path]]
                set b [get_clock_info -name [get_path_info -to_clock $path]]
                if {$a ne $b} {error "Unexpected status cross-clock logic path $scope: $a -> $b"}
                set slack [get_path_info -slack $path]
                puts $table [join [list $corner $scope $kind $slack \
                    [get_node_info -name [get_path_info -from $path]] \
                    [get_node_info -name [get_path_info -to $path]] $a $b] "\t"]
                if {$slack<0} {error "Status $scope $kind timing remains negative: $slack"}
            }
        }
    }
    incr corner
}
close $table
if {$corner!=4} {error {Expected four status timing corners}}
report_exceptions -npaths 100 -file [file join $output exceptions.txt]
delete_timing_netlist
project_close -dont_export_assignments
puts {PASS_STATUS_TIMING corners_4 raw_logic_paths_0}
