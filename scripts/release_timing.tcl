# Release timing table for a PRIVATE COPY of a fitted Pocket project.
# quartus_sta -t release_timing.tcl   (cwd = the copied project)
# Prints one RELEASE_CLOCK line per clock and one RELEASE_TIMING line per
# corner, check and clock pair with paths:
#   RELEASE_TIMING <corner> <check> <paths> <slack> <from clock> -> <to clock>
# scripts/release_timing.py applies the release policy to these lines.
package require ::quartus::project
package require ::quartus::sta
project_open apf_top
create_timing_netlist
read_sdc
update_timing_netlist
foreach_in_collection clock [get_clocks *] {
    puts "RELEASE_CLOCK [get_clock_info -period $clock] [get_clock_info -name $clock]"
}
set corners 0
foreach_in_collection condition [get_available_operating_conditions] {
    set_operating_conditions $condition
    update_timing_netlist
    set corner [string map {" " _} [get_operating_conditions_info -display_name $condition]]
    foreach_in_collection from [get_clocks *] {
        foreach_in_collection to [get_clocks *] {
            foreach check {setup hold recovery removal} {
                set result [report_timing -$check -from_clock $from -to_clock $to -npaths 1 \
                    -file [file join [pwd] release-timing-scratch.txt]]
                if {[lindex $result 0] > 0} {
                    puts "RELEASE_TIMING $corner $check [lindex $result 0] [lindex $result 1]\
                        [get_clock_info -name $from] -> [get_clock_info -name $to]"
                }
            }
        }
    }
    incr corners
}
puts "RELEASE_CORNERS $corners"
delete_timing_netlist
project_close
