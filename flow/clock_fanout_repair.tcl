# IHP SG13CMOS5L clock-tree repair. The three standard-cell Liberty views have
# default_max_fanout=8 and sg13cmos5l_buf_8/X implements A. Do not change SDC or
# electrical limits to make this step pass. Full corner timing is still needed.
namespace eval protoemu_clock {
    variable limit 8
    variable buffer_cell sg13cmos5l_buf_8

    proc loads {net} {
        set names {}
        foreach pin [$net getITerms] {
            if {[$pin getIoType] eq "INPUT"} {
                lappend names "[[$pin getInst] getName]/[[$pin getMTerm] getName]"
            }
        }
        return [lsort $names]
    }

    proc driver {net} {
        set outputs {}
        foreach pin [$net getITerms] {
            if {[$pin getIoType] eq "OUTPUT"} {lappend outputs $pin}
            if {[$pin getIoType] eq "INOUT"} {error "clock net has bidirectional pin: [$net getName]"}
        }
        # CTS internal branches have one cell driver and no top-level pins.
        # Decline unsupported topology before writing any candidate views.
        if {[llength $outputs] != 1 || [llength [$net getBTerms]] != 0} {
            error "unsupported overloaded clock topology: [$net getName]"
        }
        return [lindex $outputs 0]
    }

    proc run {} {
        variable limit
        variable buffer_cell
        set block [ord::get_db_block]
        set overloaded {}
        foreach net [$block getNets] {
            if {[$net getSigType] eq "CLOCK" && [llength [loads $net]] > $limit} {
                driver $net
                lappend overloaded [$net getName]
            }
        }
        set count 0
        set original_load_count 0
        foreach name [lsort $overloaded] {
            set net [$block findNet $name]
            set original [loads $net]
            set ndr [$net getNonDefaultRule]
            set source [driver $net]
            lassign [[$source getInst] getLocation] px py
            set location [list [ord::dbu_to_microns $px] [ord::dbu_to_microns $py]]
            puts "CLOCK_FANOUT_ORIGINAL $name $original"
            incr original_load_count [llength $original]
            # Repeating the grouping on the original net also handles more
            # than 64 loads: upper levels drive buffer inputs, at most eight.
            while {[llength [set before [loads $net]]] > $limit} {
                set size [llength $before]
                set groups [expr {($size + $limit - 1) / $limit}]
                set remaining {}
                set offset 0
                for {set group 0} {$group < $groups} {incr group} {
                    # Balanced groups avoid an unnecessary one-load branch.
                    set width [expr {$size / $groups + ($group < $size % $groups)}]
                    set names [lrange $before $offset [expr {$offset + $width - 1}]]
                    incr offset $width
                    set pins [get_pins $names]
                    if {[llength $pins] != $width} {error "clock load resolution failed: $names"}
                    insert_buffer -buffer_cell $buffer_cell -load_pins $pins \
                        -buffer_name clkbuf_protoemu_fanout_ -net_name clknet_protoemu_fanout_ \
                        -location $location
                    # Native insert_buffer appends a unique suffix to base
                    # names. Resolve the actual new net through a known load.
                    set branch [[$block findITerm [lindex $names 0]] getNet]
                    if {$branch eq $net} {error "clock loads were not moved"}
                    set new_driver [driver $branch]
                    set inst [$new_driver getInst]
                    set input [$inst findITerm A]
                    if {[[$inst getMaster] getName] ne $buffer_cell || $input eq "NULL" || [$input getNet] ne $net} {
                        error "inserted clock buffer does not preserve parent connection"
                    }
                    $branch setSigType CLOCK
                    if {$ndr ne "NULL"} {$branch setNonDefaultRule $ndr}
                    if {[loads $branch] ne $names} {error "inserted clock branch has a different load set"}
                    lappend remaining "[$inst getName]/A"
                    incr count
                    puts "CLOCK_FANOUT_INSERTED [$inst getName] [$branch getName] $names"
                }
                if {$offset != $size || [loads $net] ne [lsort $remaining]} {
                    error "clock parent retains unexpected loads"
                }
            }
        }
        foreach net [$block getNets] {
            if {[$net getSigType] eq "CLOCK" && [llength [loads $net]] > $limit} {
                error "clock fanout still exceeds $limit: [$net getName]"
            }
        }
        puts "CLOCK_FANOUT_REPAIR_COMPLETE [llength $overloaded] branches $count buffers $original_load_count preserved loads"
        return $count
    }
}
