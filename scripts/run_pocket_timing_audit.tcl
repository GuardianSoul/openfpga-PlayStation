# Invoke an existing audit without allowing its error path to export settings.
# quartus_sta -t <this script> <auditor.tcl> <auditor arguments...>
# Numeric timing and the auditor's result remain unchanged.
package require ::quartus::project
package require ::quartus::sta
if {![llength $quartus(args)]} {error "Expected timing auditor and its arguments"}
set timing_auditor [file normalize [lindex $quartus(args) 0]]
if {![file isfile $timing_auditor]} {error "Timing auditor file missing"}
set quartus(args) [lrange $quartus(args) 1 end]
try {
    source $timing_auditor
} finally {
    if {[is_project_open]} {
        catch {delete_timing_netlist}
        project_close -dont_export_assignments
    }
}
