# Resolve Gray launch registers after legal register replication/merging.
# Gray MSB == binary MSB: both initialize/reset to zero and are assigned the
# same next-pointer bit on precisely the same enabled edges. Quartus may retain
# either name. Only that proven top-bit alias is accepted; losing any lower
# Gray bit, multiple instances or another geometry is an error.
proc pocket_audio_gray_bus {prefix pointer} {
    set gray_pattern [format {%s%s_gray[*]} $prefix $pointer]
    set gray [get_registers $gray_pattern]
    set bits {}
    set instances {}
    foreach_in_collection node $gray {
        set name [get_node_info -name $node]
        set expression [format {^(.*)\|%s_gray\[([0-6])\](~DUPLICATE[0-9]*)?$} $pointer]
        if {![regexp $expression $name ignored instance bit replica]} {
            error "Unrecognized physical audio Gray register: $name"
        }
        lappend bits $bit
        lappend instances $instance
    }
    if {[llength [lsort -unique $instances]] != 1} {error "Ambiguous audio Gray instance"}
    set bits [lsort -integer -unique $bits]
    if {$bits == {0 1 2 3 4 5 6}} {
        puts "AUDIO_GRAY_PHYSICAL $pointer [get_collection_size $gray] logical_bits_7"
        return $gray
    }
    if {$bits != {0 1 2 3 4 5}} {error "Missing audio $pointer Gray bits: $bits"}
    set high_gray [get_registers [format {%s%s_gray[6]} $prefix $pointer]]
    set high_binary [get_registers [format {%s%s_bin[6]} $prefix $pointer]]
    if {[get_collection_size $high_gray] != 0 || [get_collection_size $high_binary] < 1} {
        error "Unproven audio $pointer Gray register removal"
    }
    # Fitter may replicate the retained binary MSB after placement. Its Gray
    # value is still identical on every enabled/reset edge. Admit each copy
    # only after checking the exact register, bit and original instance.
    set expression [format {^(.*)\|%s_bin\[6\](~DUPLICATE[0-9]*)?$} $pointer]
    foreach_in_collection node $high_binary {
        set name [get_node_info -name $node]
        if {![regexp $expression $name ignored instance replica] ||
            $instance ne [lindex $instances 0]} {
            error "Unproven physical audio MSB replica: $name"
        }
    }
    set bus [get_registers [list $gray_pattern [format {%s%s_bin[6]} $prefix $pointer]]]
    if {[get_collection_size $bus] != [expr {[get_collection_size $gray]+[get_collection_size $high_binary]}]} {error "Audio $pointer MSB alias scope mismatch"}
    puts "AUDIO_GRAY_MSB_ALIAS $pointer binary_bit6 physical_copies_[get_collection_size $high_binary]"
    return $bus
}
