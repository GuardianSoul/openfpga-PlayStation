# Select actual parent hard-resource locations from a private CDB export.
# Run with quartus_sh -t BEFORE compilation. Preserve raw QSF quoting:
# Tcl list serialization puts braces around indexed targets, but Quartus QSF
# retains those braces as literal node-name characters and ignores the target.
set arguments $argv
if {[info exists quartus(args)]} {set arguments $quartus(args)}
if {[llength $arguments] != 4} {
    error "Expected original QSF, exported QSF, physical TSV, fresh output QSF"
}
lassign $arguments before_path after_path physical_path output_path
if {[file exists $output_path]} {error "Fresh output required"}
set fd [open $physical_path r]
if {[gets $fd] ne "name\tlocation\ttype"} {error "Unknown physical inventory header"}
set physical [dict create]
while {[gets $fd line] >= 0} {
    set fields [split $line "\t"]
    if {[llength $fields] != 3} {error "Unknown physical inventory row"}
    lassign $fields name location type
    if {$name eq {} || $location eq {} || [dict exists $physical $name]} {
        error "Missing or duplicate physical cell"
    }
    dict set physical $name [list $location $type]
}
close $fd
set before [dict create]
set fd [open $before_path r]
while {[gets $fd line] >= 0} {
    if {[string trim $line] eq {} || [string index [string trimleft $line] 0] eq "#"} {continue}
    # Parse list words without executing QSF commands or target substitutions.
    dict incr before [lrange $line 0 end]
}
close $fd
set selected {}; set seen [dict create]
set fd [open $after_path r]
while {[gets $fd line] >= 0} {
    if {[string trim $line] eq {} || [string index [string trimleft $line] 0] eq "#"} {continue}
    set words [lrange $line 0 end]
    if {[dict exists $before $words] && [dict get $before $words] > 0} {
        dict incr before $words -1
        continue
    }
    if {[lindex $words 0] ne "set_location_assignment"} {continue}
    if {[llength $words] != 4 || [lindex $words 2] ne "-to"} {error "Unknown location grammar"}
    lassign $words command location flag target
    set parts {}
    foreach part [split $target |] {
        set colon [string first : $part]
        lappend parts [expr {$colon < 0 ? $part : [string range $part $colon+1 end]}]
    }
    set name [join $parts |]
    if {![dict exists $physical $name]} {continue}
    lassign [dict get $physical $name] expected type
    if {$type ni {cyclonev_ram_block cyclonev_mac cyclonev_io_obuf cyclonev_io_ibuf cyclonev_ddio_out cyclonev_clkena}} {continue}
    if {$location ne $expected} {error "Actual parent location mismatch: $name"}
    if {[dict exists $seen $name]} {error "Ambiguous physical target: $name"}
    dict set seen $name 1
    lappend selected $line
}
close $fd
if {![llength $selected]} {error "No physical hard-resource locations selected"}
# Do not create a partial fragment when a guard fails. Never adopt the rest
# of the CDB export: it may remove include assignments or change settings.
set fd [open $output_path {WRONLY CREAT EXCL}]
foreach line $selected {puts $fd $line}
close $fd
puts "POCKET_HARD_LOCATIONS [llength $selected]"
