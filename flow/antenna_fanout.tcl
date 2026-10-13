# Fanout repair after detailed routing's antenna repair.
#
# Antenna repair runs last (inside OpenROAD.DetailedRouting, after every
# resizer step) and attaches its diodes to the violating nets. A diode's pin
# is an input, so it counts as a load against the library's fanout limit of
# eight: nets the resizer left at <= 8 loads ended with 9-13 (docs/physical.md).
# This splits each such net once routing is done:
#   * every cell sink forms a unit with the antenna diodes nearest to it (the
#     router places a diode next to the gate it protects);
#   * the unit farthest from the driver and the units nearest to it move
#     behind one new buffer, placed at their centroid, until the original net
#     (now also driving the buffer's input) is within the limit; the moved
#     units stay within it too;
#   * the buffer goes on the nearest free row sites (no existing cell may
#     move: they are routed); the original net is re-created (same name and
#     guides) so that it is routed afresh, both nets get guides over the
#     buffer and the moved loads, and detailed routing and its antenna loop
#     (as in LibreLane's drt.tcl) run again; a check that every pin of both
#     nets lies on its routed wire follows, then the fanout check repeats.
# A design without such nets passes through unchanged. It runs in detailed
# routing's process (flow/drt_antenna_fanout.tcl): a new process re-routed the
# whole design from scratch (38k violations at the first iteration) where the
# in-process antenna loop re-routes incrementally in minutes.
namespace eval protoemu_antfo {
    variable buffer_cell sg13cmos5l_buf_4
    variable max_rounds 3
    variable diode_master ""
    variable count 0

    proc is_diode {it} {
        variable diode_master
        return [expr {[[[$it getInst] getMaster] getName] eq $diode_master}]
    }

    proc loads {net} {
        set out [list]
        foreach it [$net getITerms] {
            if {[$it isInputSignal]} { lappend out $it }
        }
        return $out
    }

    proc driver {net} {
        foreach it [$net getITerms] {
            if {[$it isOutputSignal]} { return $it }
        }
        return ""
    }

    proc xy {it} {
        set b [[$it getInst] getBBox]
        return [list [expr {([$b xMin] + [$b xMax]) / 2}] [expr {([$b yMin] + [$b yMax]) / 2}]]
    }

    proc dist {a b} {
        return [expr {abs([lindex $a 0] - [lindex $b 0]) + abs([lindex $a 1] - [lindex $b 1])}]
    }

    # signal nets with more input pins than the limit; top-level ports are
    # not cell loads (STA's fanout check does not count them either)
    proc overloaded {limit} {
        set out [list]
        foreach net [[ord::get_db_block] getNets] {
            if {[$net getSigType] ne "SIGNAL"} { continue }
            if {[llength [loads $net]] > $limit} { lappend out $net }
        }
        return $out
    }

    proc units {net} {
        set sinks [list]
        set diodes [list]
        foreach it [loads $net] {
            if {[is_diode $it]} { lappend diodes $it } else { lappend sinks $it }
        }
        set u [dict create]
        foreach s $sinks { dict set u $s [list $s] }
        foreach d $diodes {
            set best ""
            set bd ""
            foreach s $sinks {
                set dd [dist [xy $d] [xy $s]]
                if {$bd eq "" || $dd < $bd} { set bd $dd; set best $s }
            }
            if {$best eq ""} { dict set u $d [list $d] } else { dict lappend u $best $d }
        }
        return [dict values $u]
    }

    # the gcell (global routing tile) around a point, as {x0 y0 x1 y1}
    proc gcell {x y} {
        set grid [[ord::get_db_block] getGCellGrid]
        set die [[ord::get_db_block] getDieArea]
        set c {}
        foreach v [list $x $y] lines [list [$grid getGridX] [$grid getGridY]] \
                hi [list [$die xMax] [$die yMax]] {
            set lo [lindex $lines 0]
            set up $hi
            foreach l $lines {
                if {$l <= $v} { set lo $l } else { set up $l; break }
            }
            lappend c $lo $up
        }
        return [list [lindex $c 0] [lindex $c 2] [lindex $c 1] [lindex $c 3]]
    }

    # Routing guides for detailed routing, the way global routing writes
    # them: a Metal1 access guide at each pin's gcell, and Metal2-Metal4 over
    # the gcells around all the pins. Global routing itself is not re-run: in
    # this process it was initialized from the detailed routes by antenna
    # repair, and an incremental run rewrote 22k guides and disabled clock
    # NDRs (run fo2).
    proc add_guides {net pins} {
        set tech [ord::get_db_tech]
        set x0 ""
        set seen [dict create]
        foreach it $pins {
            lassign [xy $it] x y
            lassign [gcell $x $y] gx0 gy0 gx1 gy1
            if {![dict exists $seen "$gx0 $gy0"]} {
                dict set seen "$gx0 $gy0" 1
                set g [odb::dbGuide_create $net [$tech findLayer Metal1] [$tech findLayer Metal2] \
                           [odb::new_Rect $gx0 $gy0 $gx1 $gy1] 0]
                $g setIsConnectedToTerm 1
            }
            if {$x0 eq ""} {
                lassign [list $gx0 $gy0 $gx1 $gy1] x0 y0 x1 y1
            } else {
                set x0 [expr {min($x0, $gx0)}]
                set y0 [expr {min($y0, $gy0)}]
                set x1 [expr {max($x1, $gx1)}]
                set y1 [expr {max($y1, $gy1)}]
            }
        }
        foreach {layer via} {Metal2 Metal3 Metal3 Metal4 Metal4 Metal4} {
            odb::dbGuide_create $net [$tech findLayer $layer] [$tech findLayer $via] \
                [odb::new_Rect $x0 $y0 $x1 $y1] 0
        }
    }

    proc centroid {its} {
        set sx 0
        set sy 0
        foreach it $its {
            lassign [xy $it] x y
            incr sx $x
            incr sy $y
        }
        return [list [expr {$sx / [llength $its]}] [expr {$sy / [llength $its]}]]
    }

    # Put an instance on the free row sites nearest to (cx, cy). Detailed
    # placement is not used: run after routing, it touched every instance and
    # detailed routing then re-routed the whole design (91k violations at its
    # first iteration, run fo3) instead of the changed nets.
    proc place_free {inst cx cy} {
        set block [ord::get_db_block]
        set w [[$inst getMaster] getWidth]
        set h [[$inst getMaster] getHeight]
        set reach 60000
        set obs [list]
        foreach o [$block getInsts] {
            if {$o eq $inst || ![$o isPlaced]} { continue }
            set b [$o getBBox]
            if {[$b xMax] < $cx - $reach - $w || [$b xMin] > $cx + $reach + $w ||
                [$b yMax] < $cy - $reach - $h || [$b yMin] > $cy + $reach + $h} { continue }
            lappend obs [list [$b xMin] [$b yMin] [$b xMax] [$b yMax]]
        }
        set best ""
        foreach row [$block getRows] {
            set rb [$row getBBox]
            set ry [lindex [$row getOrigin] 1]
            if {abs($ry + $h / 2 - $cy) > $reach || [$rb yMax] - [$rb yMin] < $h} { continue }
            set sp [$row getSpacing]
            set rx0 [lindex [$row getOrigin] 0]
            set blocked [list]
            foreach o $obs {
                lassign $o x0 y0 x1 y1
                if {$y1 > $ry && $y0 < $ry + $h} { lappend blocked [list $x0 $x1] }
            }
            set blocked [lsort -integer -index 0 $blocked]
            # free gaps between obstacles within the row
            set gaps [list]
            set at [$rb xMin]
            foreach b $blocked {
                lassign $b x0 x1
                if {$x0 > $at} { lappend gaps [list $at $x0] }
                if {$x1 > $at} { set at $x1 }
            }
            if {[$rb xMax] > $at} { lappend gaps [list $at [$rb xMax]] }
            foreach g $gaps {
                lassign $g g0 g1
                # first and last site-aligned positions that fit
                set lo [expr {$rx0 + (($g0 - $rx0 + $sp - 1) / $sp) * $sp}]
                set hi [expr {$rx0 + (($g1 - $w - $rx0) / $sp) * $sp}]
                if {$hi < $lo} { continue }
                set want [expr {$rx0 + round(double($cx - $w / 2 - $rx0) / $sp) * $sp}]
                set x [expr {min(max($want, $lo), $hi)}]
                set cost [expr {abs($x + $w / 2 - $cx) + abs($ry + $h / 2 - $cy)}]
                if {$best eq "" || $cost < [lindex $best 0]} {
                    set best [list $cost $x $ry [$row getOrient]]
                }
            }
        }
        if {$best eq ""} {
            puts "\[ERROR\] no free site for [$inst getName] near ([ord::dbu_to_microns $cx], [ord::dbu_to_microns $cy])"
            exit 1
        }
        lassign $best cost x y orient
        $inst setOrient $orient
        $inst setLocation $x $y
        $inst setPlacementStatus PLACED
        return $cost
    }

    proc split_net {net limit} {
        variable buffer_cell
        variable count
        set drv [driver $net]
        if {$drv eq ""} {
            puts "\[WARNING\] [$net getName]: [llength [loads $net]] loads and no cell driver; not split"
            return 0
        }
        # one compact group: the unit farthest from the driver, then the units
        # nearest to the group, until the original net (which also drives the
        # new buffer's input) is within the limit
        set total [llength [loads $net]]
        set left [units $net]
        set seed ""
        set best -1
        foreach u $left {
            set d [dist [xy $drv] [xy [lindex $u 0]]]
            if {$d > $best} { set best $d; set seed $u }
        }
        set moved $seed
        set left [lsearch -all -inline -not -exact $left $seed]
        while {$total - [llength $moved] + 1 > $limit && [llength $left]} {
            set c [centroid $moved]
            set best ""
            set bd ""
            foreach u $left {
                if {[llength $moved] + [llength $u] > $limit} { continue }
                set d [dist $c [xy [lindex $u 0]]]
                if {$bd eq "" || $d < $bd} { set bd $d; set best $u }
            }
            if {$best eq ""} { break }
            lappend moved {*}$best
            set left [lsearch -all -inline -not -exact $left $best]
        }
        if {[llength $moved] > $limit || $total - [llength $moved] + 1 > $limit} {
            puts "\[ERROR\] [$net getName]: $total loads, no split into two nets within the limit"
            exit 1
        }
        lassign [centroid $moved] cx cy
        # The edit is made in odb. Detailed routing in this process follows
        # odb's callbacks, but it does not re-route a net it has already
        # routed when a pin is added to it: the original net, kept, stayed
        # open at the new buffer's input (runs fo4 and fo5; LVS and the check
        # below). So that net is re-created under its own name, with its
        # guides, and both nets are routed afresh; every other route stays.
        set block [ord::get_db_block]
        set master [[ord::get_db] findMaster $buffer_cell]
        while {1} {
            incr count
            set name "antfo_buf_$count"
            set nname "antfo_net_$count"
            if {[$block findInst $name] eq "NULL" && [$block findNet $nname] eq "NULL"} { break }
        }
        set buf [odb::dbInst_create $block $master $name]
        foreach it [$buf getITerms] {
            set type [[$it getMTerm] getSigType]
            if {$type in {POWER GROUND}} {
                foreach n [$block getNets] {
                    if {[$n getSigType] eq $type && [$n isSpecial]} { $it connect $n; break }
                }
            }
        }
        set d [place_free $buf $cx $cy]
        set guides [list]
        foreach g [$net getGuides] {
            set b [$g getBox]
            lappend guides [list [$g getLayer] [$g getViaLayer] \
                                [$b xMin] [$b yMin] [$b xMax] [$b yMax] [$g isConnectedToTerm]]
        }
        set oname [$net getName]
        set sig [$net getSigType]
        set iterms [$net getITerms]
        set bterms [$net getBTerms]
        foreach it $iterms { $it disconnect }
        foreach bt $bterms { $bt disconnect }
        odb::dbNet_destroy $net
        set net [odb::dbNet_create $block $oname]
        $net setSigType $sig
        foreach g $guides {
            lassign $g layer via x0 y0 x1 y1 term
            set ng [odb::dbGuide_create $net $layer $via [odb::new_Rect $x0 $y0 $x1 $y1] 0]
            if {$term} { $ng setIsConnectedToTerm 1 }
        }
        set new [odb::dbNet_create $block $nname]
        foreach bt $bterms { $bt connect $net }
        foreach it $iterms {
            if {$it in $moved} { $it connect $new } else { $it connect $net }
        }
        foreach it [$buf getITerms] {
            if {[$it isInputSignal]} { $it connect $net }
            if {[$it isOutputSignal]} { $it connect $new }
        }
        puts "\[INFO\] [$net getName]: $total loads (driver [[$drv getInst] getName]);\
              [llength $moved] now behind $name\
              ([ord::dbu_to_microns $d] um from their centroid):\
              [lmap it $moved {string cat [[$it getInst] getName] / [[$it getMTerm] getName]}]"
        return [list $net $moved]
    }

    # Pins of a net that no point of its routed wire falls on (a quick
    # connectivity check after re-routing; LVS is the real one).
    proc open_pins {net} {
        set pts [list]
        set w [$net getWire]
        if {$w ne "NULL"} {
            set d [odb::dbWireDecoder]
            $d begin $w
            set keep [list $odb::dbWireDecoder_POINT $odb::dbWireDecoder_POINT_EXT \
                          $odb::dbWireDecoder_JUNCTION $odb::dbWireDecoder_SHORT \
                          $odb::dbWireDecoder_VWIRE]
            while {[set op [$d next]] != $odb::dbWireDecoder_END_DECODE} {
                if {$op in $keep} { lappend pts [lrange [$d getPoint] 0 1] }
            }
        }
        set open [list]
        foreach it [$net getITerms] {
            set b [$it getBBox]
            set hit 0
            foreach p $pts {
                lassign $p x y
                if {$x >= [$b xMin] && $x <= [$b xMax] && $y >= [$b yMin] && $y <= [$b yMax]} {
                    set hit 1
                    break
                }
            }
            if {!$hit} { lappend open "[[$it getInst] getName]/[[$it getMTerm] getName]" }
        }
        return $open
    }

    # LibreLane's drt.tcl: detailed routing, then its antenna repair loop
    proc route {tag} {
        set args [list -droute_end_iter $::env(DRT_OPT_ITERS) -or_seed 42 -verbose 1 \
                      -output_drc $::env(STEP_DIR)/$tag.drc]
        log_cmd detailed_route {*}$args
        if {![info exists ::env(DIODE_CELL)] || $::env(DRT_ANTENNA_REPAIR_ITERS) == 0} { return }
        set diode_cell [lindex [split $::env(DIODE_CELL) "/"] 0]
        set rargs [list $diode_cell -ratio_margin $::env(GRT_ANTENNA_REPAIR_MARGIN)]
        append_if_flag rargs GRT_ALLOW_CONGESTION -allow_congestion
        append_if_flag rargs DRT_ANTENNA_REPAIR_JUMPER_ONLY -jumper_only
        append_if_flag rargs DRT_ANTENNA_REPAIR_DIODE_ONLY -diode_only
        set i 1
        while {$i <= $::env(DRT_ANTENNA_REPAIR_ITERS) && [log_cmd check_antennas]} {
            puts "\[INFO\] Antenna repair iteration $i after the fanout split…"
            set inserted [log_cmd repair_antennas {*}$rargs]
            if {!$inserted && !$::env(DRT_ANTENNA_REPAIR_JUMPER_ONLY)} { break }
            log_cmd detailed_route {*}$args
            incr i
        }
    }

    proc run {} {
        variable diode_master
        variable max_rounds
        variable count
        set diode_master [lindex [split $::env(DIODE_CELL) "/"] 0]
        set limit $::env(MAX_FANOUT_CONSTRAINT)
        set block [ord::get_db_block]
        for {set round 1} {1} {incr round} {
            set nets [overloaded $limit]
            puts "\[INFO\] Nets over the fanout limit of $limit, antenna diodes included:\
                  [llength $nets] [lmap n $nets {$n getName}]"
            if {![llength $nets]} { break }
            if {$round > $max_rounds} {
                puts "\[ERROR\] [llength $nets] nets still over the fanout limit after $max_rounds rounds"
                exit 1
            }
            set before [dict create]
            foreach inst [$block getInsts] { dict set before $inst [$inst getLocation] }
            set splits [list]
            foreach net $nets {
                set s [split_net $net $limit]
                if {[llength $s]} { lappend splits $s }
            }
            if {![llength $splits]} {
                puts "\[ERROR\] no net could be split"
                exit 1
            }
            check_placement -verbose
            foreach inst [$block getInsts] {
                if {[dict exists $before $inst] && [dict get $before $inst] ne [$inst getLocation]} {
                    puts "\[ERROR\] routed instance [$inst getName] moved"
                    exit 1
                }
            }
            foreach s $splits {
                lassign $s net moved
                # the moved loads' new net is driven by the buffer
                set out [driver [[lindex $moved 0] getNet]]
                set in [lindex [lmap it [[$out getInst] getITerms] \
                                    {expr {[$it isInputSignal] ? $it : [continue]}}] 0]
                if {[$in getNet] ne $net} {
                    puts "\[ERROR\] [$net getName]: [[$out getInst] getName] is not the inserted buffer"
                    exit 1
                }
                # the original net reaches the buffer near the moved loads it
                # used to route to; the new net connects the buffer and them
                add_guides $net [concat [list $in] $moved]
                add_guides [$out getNet] [concat [list $out] $moved]
            }
            route "fanout-round-$round"
            foreach s $splits {
                lassign $s net moved
                foreach n [list $net [[lindex $moved 0] getNet]] {
                    set open [open_pins $n]
                    if {[llength $open]} {
                        puts "\[ERROR\] [$n getName] not routed to: $open"
                        exit 1
                    }
                }
            }
        }
        puts "\[INFO\] Fanout buffers inserted after antenna repair: $count"
        write_metric_int "design__antenna_fanout_buffer__count" $count
        return $count
    }
}
