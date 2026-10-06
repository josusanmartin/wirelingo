# SRAM clock advance (useful skew), run right after CTS.
#
# Every slow-corner setup violator starts at an SRAM output: the macro's
# clock-to-DOUT is about 5.4 ns at the slow corner, and the instruction it
# reads is decoded and executed in the same cycle. CTS balances the SRAM
# clock pins to the register tree (it adds a chain of delay buffers in front
# of each macro), so the SRAMs launch as late as every flop. Driving each
# SRAM's A_CLK from a buffer on the macro tree's root net instead launches the
# read about 0.8 ns earlier. Paths into the SRAM (address, write data, enables)
# lose the same amount of setup slack and gain it as hold slack; at v2c's slow
# corner the worst of them that doesn't start at an SRAM had +2.8 ns at 48 MHz.
# The SRAM -> SRAM fetch-address loop starts and ends on the same pin and is
# unchanged.
#
# The buffer is placed at the root driver and drives the long branch itself,
# so the root only sees one more buffer input. CTS's delay buffers left
# without loads are removed. Detailed placement legalizes the new buffers.
namespace eval protoemu_sram_clk {
    variable buffer_cell sg13cmos5l_buf_8

    proc out_driver {net} {
        foreach it [$net getITerms] {
            if {[$it getIoType] eq "OUTPUT"} { return $it }
        }
        return ""
    }

    proc sink_count {net} {
        set n [llength [$net getBTerms]]
        foreach it [$net getITerms] {
            if {[$it getIoType] ne "OUTPUT"} { incr n }
        }
        return $n
    }

    # The net driven by the topmost clock buffer above a sink: the buffer
    # whose input net is driven by a top-level port.
    proc root_net {iterm} {
        set net [$iterm getNet]
        set chain {}
        while {1} {
            set drv [out_driver $net]
            if {$drv eq ""} { break }
            set inst [$drv getInst]
            set in [$inst findITerm A]
            if {$in eq "NULL" || [[$inst getMaster] getName] ni {sg13cmos5l_buf_1 sg13cmos5l_buf_2 sg13cmos5l_buf_4 sg13cmos5l_buf_8 sg13cmos5l_buf_16}} {
                error "unexpected cell [$inst getName] in the clock path of [[$iterm getInst] getName]"
            }
            lappend chain $net
            set net [$in getNet]
        }
        if {[llength [$net getBTerms]] != 1} { error "clock path does not start at a port" }
        return [lindex $chain end]
    }

    proc run {} {
        variable buffer_cell
        set block [ord::get_db_block]
        set count 0
        foreach inst [$block getInsts] {
            if {![string match RM_IHPSG13_* [[$inst getMaster] getName]]} { continue }
            set clk [$inst findITerm A_CLK]
            set old [$clk getNet]
            if {$old eq "NULL"} { continue }
            set tap [root_net $clk]
            if {$tap eq $old} { continue }
            set ndr [$old getNonDefaultRule]
            set tap_drv [[out_driver $tap] getInst]
            lassign [$tap_drv getLocation] px py
            $clk disconnect
            $clk connect $tap
            set pin [get_pins "[$inst getName]/A_CLK"]
            if {[llength $pin] != 1} { error "cannot find [$inst getName]/A_CLK" }
            insert_buffer -buffer_cell $buffer_cell -load_pins $pin \
                -buffer_name clkbuf_sram_adv_ -net_name clknet_sram_adv_ \
                -location [list [ord::dbu_to_microns $px] [ord::dbu_to_microns $py]]
            set new [$clk getNet]
            if {$new eq $tap} { error "buffer not inserted for [$inst getName]" }
            $new setSigType CLOCK
            if {$ndr ne "NULL"} { $new setNonDefaultRule $ndr }
            puts "SRAM_CLOCK_ADVANCE [$inst getName]: [$old getName] -> [[[out_driver $new] getInst] getName] on [$tap getName]"
            incr count
            # remove CTS buffers left driving nothing
            set net $old
            while {[sink_count $net] == 0} {
                set drv [out_driver $net]
                if {$drv eq ""} { break }
                set b [$drv getInst]
                set up [[$b findITerm A] getNet]
                puts "SRAM_CLOCK_ADVANCE removed [$b getName]"
                odb::dbInst_destroy $b
                odb::dbNet_destroy $net
                set net $up
            }
        }
        puts "SRAM_CLOCK_ADVANCE_COMPLETE $count macros"
        return $count
    }
}
