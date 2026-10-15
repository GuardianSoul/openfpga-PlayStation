# PRIVATE routed copy only: quartus_sta -t <script> <fresh output directory>.
# A successful audit invocation is not whole-core timing closure.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh APF blank audit directory"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh APF blank audit directory required"}
file mkdir $output
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
set first [apf_blank_reg blank_meta]
set second [apf_blank_reg blank_sync]
set third [apf_blank_reg blank_video]
set pixels [get_registers -nowarn {core_top:ic|psx_apf_video:apf_video|rgb[*]}]
if {[get_collection_size $pixels]<16} {error "Missing APF RGB consumer geometry"}
set table [open [file join $output paths.tsv] w]
puts $table "corner\tscope\tkind\tslack_ns\tfrom\tto\tfrom_clock\tto_clock"
proc apf_blank_timed {label from to} {
    global output corner table apf_blank_video_clock
    foreach kind {setup hold} {
        if {[get_collection_size [get_timing_paths -false_path -$kind -from $from -to $to -npaths 1]]!=0} {
            error "APF blank later stage/consumer false-pathed ($label $kind)"
        }
        set p [get_timing_paths -$kind -from $from -to $to -npaths 100]
        if {[get_collection_size $p]==0} {error "Missing timed APF blank path ($label $kind)"}
        foreach_in_collection path $p {
            set row [list $corner $label $kind [get_path_info -slack $path]]
            foreach field {from to} {lappend row [get_node_info -name [get_path_info -$field $path]]}
            foreach field {from_clock to_clock} {
                set clock [get_clock_info -name [get_path_info -$field $path]]
                foreach_in_collection c $apf_blank_video_clock {set expected [get_clock_info -name $c]}
                if {$clock ne $expected} {error "APF blank later stage/consumer on unexpected clock"}
                lappend row $clock
            }
            puts $table [join $row "\t"]
        }
        report_timing -$kind -from $from -to $to -npaths 100 -detail full_path \
            -file [file join $output corner${corner}-${label}-${kind}.txt]
    }
}
set corner 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    foreach kind {setup hold} {
        if {[get_collection_size [get_timing_paths -$kind -to $first -npaths 1]]!=0} {
            error "APF blank metastability input unexpectedly timed"
        }
        set p [get_timing_paths -false_path -$kind -from $apf_blank_source -to $first -npaths 1]
        if {[get_collection_size $p]!=1} {error "Missing exact APF blank first-stage exception"}
        foreach_in_collection path $p {
            foreach {field collection} [list from_clock $apf_blank_cpu_clock to_clock $apf_blank_video_clock] {
                foreach_in_collection c $collection {set expected [get_clock_info -name $c]}
                if {[get_clock_info -name [get_path_info -$field $path]] ne $expected} {
                    error "APF blank first-stage clock geometry changed"
                }
            }
        }
        foreach {sink origin} [list $second $first $third $second] {
            if {[get_collection_size [get_timing_paths -false_path -$kind -to $sink -npaths 1]]!=0} {
                error "APF blank later stage has an unreviewed exception"
            }
            set all [get_timing_paths -$kind -to $sink -npaths 100]
            if {[get_collection_size $all]!=1} {error "APF blank later stage has unexpected fan-in"}
            foreach_in_collection path $all {
                foreach_in_collection node $origin {set expected [get_node_info -name $node]}
                if {[get_node_info -name [get_path_info -from $path]] ne $expected} {
                    error "APF blank later stage is fed by something other than its predecessor"
                }
            }
        }
        # This old unsafe path must be absent, including paths hidden by cuts.
        foreach option [list [list -$kind] [list -false_path -$kind]] {
            if {[get_collection_size [get_timing_paths {*}$option -from $apf_blank_source -to $pixels -npaths 1]]!=0} {
                error "Raw CPU reset still drives APF RGB"
            }
        }
        report_timing -false_path -$kind -from $apf_blank_source -to $first -npaths 10 -detail full_path \
            -file [file join $output corner${corner}-first-${kind}.txt]
        report_timing -$kind -npaths 20 -detail full_path \
            -file [file join $output corner${corner}-global-${kind}.txt]
    }
    apf_blank_timed second $first $second
    apf_blank_timed third $second $third
    apf_blank_timed consumer $third $pixels
    incr corner
}
if {$corner!=4} {error "Expected four APF blank operating conditions"}
close $table
report_exceptions -npaths 20 -file [file join $output exceptions.txt]
delete_timing_netlist
project_close -dont_export_assignments
