# Read fitted physical cells without loading or modifying timing constraints.
# Use a protected compiled copy; this is an inventory, not timing acceptance.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)]!=1} {error "Expected fresh placement TSV path"}
set output [file normalize [lindex $quartus(args) 0]]
if {[file exists $output]} {error "Fresh placement inventory required"}
project_open apf_top
try {
    create_timing_netlist
    set cells [get_cells -hierarchical *]
    if {[get_collection_size $cells]<1000} {error "Missing full Pocket physical netlist"}
    set fd [open $output w]
    puts $fd "name\tlocation\ttype"
    foreach_in_collection cell $cells {
        puts $fd [join [list [get_cell_info -name $cell] \
            [get_cell_info -location $cell] [get_cell_info -wysiwyg_type $cell]] "\t"]
    }
    close $fd
    puts "POCKET_PLACEMENT_CELLS [get_collection_size $cells]"
} finally {
    catch {delete_timing_netlist}
    project_close -dont_export_assignments
}
