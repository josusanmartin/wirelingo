# SRAM pin buffers, run after post-GRT timing repair (before the last design
# repair and global routing).
#
# The IHP SRAM's pin limits are tight: 0.595 ns input transition and 0.064 pF
# on each DOUT at the slow corner. Global routing's estimate of the nets into
# bank 1's pins came out up to 0.2 ns faster than extraction after detailed
# routing (weak drivers such as an a21oi_1 on A_ADDR, buf_1 fanout buffers on
# A_DIN), and timing repair sizes a DOUT's first buffer up to buf_16. So:
#   * each driven SRAM input pin gets its own buffer placed at the pin (it
#     drives only the pin and the pin's antenna diode, over a few microns),
#     except A_ADDR (on the fetch loop), whose _1 drivers are swapped for the
#     _2 cell where the library has one (and only _1 drivers without one get
#     the buffer);
#   * a buf_16 that timing repair put on a DOUT is sized down to buf_8 (its
#     input capacitance took DOUT[17] 1-3 % over the limit in t48ad).
# Constant (tied) inputs are left alone. Detailed placement legalizes next.
namespace eval protoemu_sram_pins {
    variable in_cell sg13cmos5l_buf_4
    variable out_cell sg13cmos5l_buf_8

    proc driver {net} {
        foreach it [$net getITerms] { if {[$it getIoType] eq "OUTPUT"} { return $it } }
        return ""
    }

    proc pin_xy {it} {
        set b [$it getBBox]
        set x [expr {([$b xMin] + [$b xMax]) / 2}]
        set y [expr {([$b yMin] + [$b yMax]) / 2}]
        return [list [ord::dbu_to_microns $x] [ord::dbu_to_microns $y]]
    }

    # a get_pins pattern for an odb pin: STA names carry plain brackets where
    # odb instance names carry escaped ones, so every bracket becomes "?"
    # (the caller checks that exactly one pin matched)
    proc pname {it} {
        set n "[[$it getInst] getName]/[[$it getMTerm] getName]"
        return [string map [list "\\\[" ? "\\\]" ? "\[" ? "\]" ?] $n]
    }

    proc run {} {
        variable in_cell
        variable out_cell
        set block [ord::get_db_block]
        set nin 0
        set nout 0
        set nup 0
        foreach inst [$block getInsts] {
            if {![string match RM_IHPSG13_* [[$inst getMaster] getName]]} { continue }
            foreach it [$inst getITerms] {
                set m [[$it getMTerm] getName]
                set net [$it getNet]
                if {$net eq "NULL" || [$net getSigType] ne "SIGNAL"} { continue }
                if {[string match A_BIST* $m] || $m in {A_CLK A_MEN A_DLY}} { continue }
                if {[$it getIoType] eq "INPUT"} {
                    set drv [driver $net]
                    if {$drv eq ""} { continue }
                    set dm [[[$drv getInst] getMaster] getName]
                    if {[string match sg13cmos5l_tie* $dm]} { continue }
                    # A_ADDR is on the fetch loop (SRAM -> next PC -> SRAM): a
                    # stronger driver rather than one more stage where the
                    # library has one
                    if {[string match A_ADDR* $m]} {
                        regsub {_1$} $dm {_2} up
                        if {$up ne $dm && [[ord::get_db] findMaster $up] ne "NULL"} {
                            set cell [get_cells [string map [list "\\\[" ? "\\\]" ? "\[" ? "\]" ?] [[$drv getInst] getName]]]
                            if {[llength $cell] != 1} { error "cell lookup failed: [[$drv getInst] getName]" }
                            replace_cell $cell [lindex [get_lib_cells */$up] 0]
                            puts "SRAM_PIN_BUFFERS [$inst getName]/$m: driver $dm -> $up"
                            incr nup
                            continue
                        }
                        if {![string match *_1 $dm]} { continue }
                    }
                    # the pin and its antenna diode move behind the new buffer
                    set loads [list [pname $it]]
                    foreach o [$net getITerms] {
                        set on [[$o getInst] getName]
                        if {[$o getIoType] eq "INPUT" && [[[$o getInst] getMaster] getName] eq "sg13cmos5l_antennanp"
                            && [string first "ANTENNA_" $on] == 0 && [string first "sram" $on] >= 0} {
                            lappend loads [pname $o]
                        }
                    }
                    set pins [get_pins $loads]
                    if {[llength $pins] != [llength $loads]} { error "pin lookup failed: $loads" }
                    insert_buffer -buffer_cell $in_cell -load_pins $pins \
                        -buffer_name sram_inbuf_ -net_name sram_innet_ -location [pin_xy $it]
                    incr nin
                } elseif {[$it getIoType] eq "OUTPUT"} {
                    set fixed 0
                    foreach o [$net getITerms] {
                        if {[$o getIoType] ne "INPUT"} { continue }
                        set om [[[$o getInst] getMaster] getName]
                        if {$om eq "sg13cmos5l_buf_16"} {
                            set cell [get_cells [string map [list "\\\[" ? "\\\]" ? "\[" ? "\]" ?] [[$o getInst] getName]]]
                            if {[llength $cell] != 1} { error "cell lookup failed: [[$o getInst] getName]" }
                            replace_cell $cell [lindex [get_lib_cells */$out_cell] 0]
                            set fixed 1
                        }
                    }
                    if {$fixed} {
                        puts "SRAM_PIN_BUFFERS [$inst getName]/$m: buf_16 load sized down to $out_cell"
                        incr nout
                    }
                }
            }
        }
        puts "SRAM_PIN_BUFFERS_COMPLETE $nin inputs buffered, $nup address drivers upsized, $nout output loads sized down"
    }
}
