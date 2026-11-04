#!/usr/bin/env python3
"""Mutation check: each mutant is a one-line bug in the model, the RTL, a
firmware program or the assembler, paired with the test that must catch it.
Each runs in a scratch copy of the tracked files; the working tree is never
touched. Exit status 1 if any mutant survives.

    python3 test/mutants.py [name or pattern ...]      # from the repo root (needs the test env)
    python3 test/mutants.py 'eth-*'                    # the 10BASE-T stack
    python3 test/mutants.py 'hw-*'                     # the hardware-day launcher and fabric guard
    python3 test/mutants.py 'fab-*'                    # eth_tap's FPGA backend (fabric_eth.py)
    python3 test/mutants.py 'reply-*'                  # match, then answer (eth10_reply)
"""
import fnmatch
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]

LOCKSTEP = ("cd test && LOCKSTEP_SEEDS=40 LOCKSTEP_CYCLES=1000 PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 "
            "make TB=chip COCOTB_RESULTS_FILE=m.xml > m.log 2>&1; python3 check_results.py m.xml 6")


def top(test):
    """Killed only if this test itself fails (TESTCASE reports the rest as skipped)."""
    return f"cd test && make TESTCASE={test} COCOTB_RESULTS_FILE=m.xml > m.log 2>&1"


def pytest(spec):
    return f"PYTHONPATH=tools python -m pytest -q -x -p no:cacheprovider --junitxml=m.xml {spec} > m.log 2>&1"


def ETH(selection):
    return pytest(f'test/model/test_eth_stack.py -k "{selection}"')

HW_TESTS = pytest("test/model/test_hw_day.py test/model/test_fabric_guard.py")
FAB = pytest("test/model/test_fabric_ping.py test/model/test_fabric_guard.py")


def REPLY(selection):
    return pytest(f'test/model/test_eth10_reply.py -k "{selection}"')


REPLY50 = REPLY("match_then_reply_timing and 50000000.0")

FSDATA = "test/model/test_usb_fs_data.py -k"   # full-speed data over endpoint 0

# name: (file, original text, mutated text, test command)
MUTANTS = {
    "model-alu-borrow": ("tools/pe/sim.py", "c = int(a < s)", "c = int(a <= s)", LOCKSTEP),
    "model-wait-not-sticky": ("tools/pe/sim.py", "self.wevent = int(e.edge_done)", "self.wevent = int(e.ev)", LOCKSTEP),
    "model-join-early-full": ("tools/pe/sim.py", "if core.txf and len(core.rxf) < self.fifo_depth:",
                              "if core.txf and len(core.rxf) < self.fifo_depth - 1:", LOCKSTEP),
    "rtl-wait-not-sticky": ("src/pe_core.sv", "wire        edge_done = wevent || (wkind == W_DIFF ? wdiff : (trans && qual));",
                            "wire        edge_done = (wkind == W_DIFF ? wdiff : (trans && qual));", LOCKSTEP),
    "rtl-join-ignores-full": ("src/pe_fifos.sv", "wire xfer      = fjoin && !txf_empty && !rxf_full;",
                              "wire xfer      = fjoin && !txf_empty;", LOCKSTEP),
    "rtl-pop-without-snapshot": ("src/pe_core.sv", "bus_commit && res_n != 2'd0 && res_v[0];", "bus_commit && res_n != 2'd0;",
                                 top("test_fifo_read_race")),
    "fw-usb-no-token-filter": ("firmware/usb_ls_rx.pasm",
                               "    jmp !z, eop_skip               ; another address or endpoint, or a bad CRC5",
                               "    nop", pytest("test/model/test_usb_model.py -k tokens_for_others")),
    "fw-i2c-data-wait-no-timeout": ("firmware/i2c_master.pasm",
                                    "    wait 1 pin SCL timeout r3     ; clock stretching",
                                    "    wait 1 pin SCL                ; clock stretching",
                                    pytest("test/model/test_i2c_timeout.py")),
    "fw-can-no-stuff-check": ("firmware/can_rx.pasm", "    jmp z, stuff_error                ; S+5   six identical bits",
                              "    nop                               ; S+5", pytest("test/model/test_can.py -k stuff")),
    "fw-eth10-short-tp-idl": ("firmware/eth10_tx.pasm", "    nop                       side 1 [9]",
                              "    nop                       side 1 [5]", pytest("test/model/test_eth10.py -k min_frame")),
    "asm-clkdiv-no-carry": ("tools/pe/isa.py", "    if f == 256:", "    if f == 257:", pytest("test/model/test_asm.py")),
    "rtl-config-in-base-field": ("src/pe_core.sv", "bus_wdata[12], bus_wdata[13], bus_wdata[20:16]}",
                                 "bus_wdata[12], bus_wdata[13], bus_wdata[21:17]}", LOCKSTEP),
    "rtl-line-no-falling-edge-sample": ("src/pe_chip.sv", "assign line_rx_mid = rx_is_pad ? (rx_byp ? rx_hq : rx_h2) : pins_in[line_rxp];",
                                        "assign line_rx_mid = pins_in[line_rxp];", top("test_eth10_rx_jitter")),
    "rtl-sync-bypass-ignored": ("src/pe_chip.sv", "wire [12:0] pad_in = (sync2 & ~sync_byp) | (sync1 & sync_byp);",
                                "wire [12:0] pad_in = sync2;", top("test_spi_master_fast")),
    "rtl-line-mid-ignores-bypass": ("src/pe_chip.sv", "(rx_byp ? rx_hq : rx_h2)", "rx_h2", LOCKSTEP),
    "model-sync-bypass-ignored": ("tools/pe/sim.py", "return (self.sync2 & ~self.sync_bypass) | (self.sync1 & self.sync_bypass)",
                                  "return self.sync2", pytest("test/model/test_spi_fast.py")),
    "model-line-fast-loop": ("tools/pe/line.py", "g = min(5, 1 + self.n // 6)", "g = 1",
                             pytest("test/model/test_eth10.py -k external")),
    "rtl-line-no-tx-stuff": ("src/pe_line.sv", "        if (k != 3'd0 && n_trun == k) begin                      // stuff bit",
                             "        if (1'b0) begin                                          // stuff bit", LOCKSTEP),
    "model-line-manchester-window": ("tools/pe/line.py", "-(48 * p12 if wide else 32 * p12)",
                                     "-(48 * p12 if wide else 44 * p12)", pytest("test/model/test_eth10.py -k external")),
    "rtl-line-nrzi-full-count": ("src/pe_line.sv", "wire [15:0] r_load = {1'b0, p[15:1]} - (edge1 ? 16'd2 : 16'd1);",
                                 "wire [15:0] r_load = {1'b0, p[15:1]} - 16'd1;", LOCKSTEP),
    "model-line-nrzi-full-count": ("tools/pe/line.py", "        return int(h != self.rprev)", "        return 0",
                                   pytest("test/model/test_usb_fs.py -k duty_cycle")),
    "rtl-wait-diff-not-sticky": ("src/pe_core.sv", "default: wbase = in_wait ? edge_done : wdiff;", "default: wbase = wdiff;",
                                 LOCKSTEP),
    "rtl-wait-diff-ignores-mask": ("src/pe_core.sv", "wire        wdiff = |((pins_rot[7:0] ^ wref[7:0]) & x[7:0]);",
                                   "wire        wdiff = |(pins_rot[7:0] ^ wref[7:0]);", LOCKSTEP),
    "model-wait-diff-not-sticky": ("tools/pe/sim.py", "done = e.diff if first else e.edge_done", "done = e.diff",
                                   pytest("test/model/test_wait_diff.py")),
    "fw-la-no-gap-check": ("firmware/la_capture.pasm", "    jmp rxf, full               ; W+4",
                           "    nop                         ; W+4",
                           pytest("test/model/test_reverse_engineering.py -k gap")),
    "rtl-call-link-ignores-wrap": ("src/pe_core.sv", "    lr_nv = pc_seq;", "    lr_nv = pc + 10'd1;", LOCKSTEP),
    "rtl-call-no-link": ("src/pe_core.sv", "lr_we = op == OP_CALL && cond_ok;", "lr_we = 1'b0;", LOCKSTEP),
    "model-call-links-untaken": ("tools/pe/sim.py", "        if self._op_jmp(e):", "        if self._op_jmp(e) or True:",
                                 pytest("test/model/test_call.py")),
    "fw-i2c-device-no-rstart": ("firmware/i2c_device.pasm",
                                "    jmp start                          ; SDA fell while SCL high: repeated START",
                                "    jmp idle", pytest("test/model/test_reverse_engineering.py -k impersonate")),
    "rtl-wait-diff-ref-r0": ("src/pe_core.sv", "wire [15:0] wref = rsel(x[15:14], r0, r1, r2, r3);",
                             "wire [15:0] wref = r0;", LOCKSTEP),
    "rtl-mov-lr-ignored": ("src/pe_core.sv", "          L_LR: begin\n            lr_we = 1'b1;",
                           "          L_LR: begin\n            lr_we = 1'b0;", LOCKSTEP),
    "asm-call-not-relocated": ("tools/pe/asm.py", "op in (isa.OP_JMP, isa.OP_CALL)", "op == isa.OP_JMP",
                               pytest("test/model/test_asm.py")),
    "driver-call-target-unchecked": ("tools/micropython/protoemu.py", "if w >> 27 in (0, 13) and", "if w >> 27 == 0 and",
                                     pytest("test/model/test_host_validation.py")),
    "fw-usb-record-no-marker": ("firmware/usb_ls_rx.pasm", "    ldi r1, 0x81                   ; trailer status: good",
                                "    ldi r1, 0x01                   ; trailer status: good",
                                pytest("test/model/test_usb_model.py -k rx_packets")),
    "fw-mdio-master-no-release": ("firmware/mdio_master.pasm",
                                  "    pin MDIO, hiz           side 0 [H-1]  ; turnaround: ours ends here",
                                  "    nop                     side 0 [H-1]  ; turnaround: ours ends here",
                                  pytest("test/model/test_mdio.py -k master_turnaround")),
    "fw-mdio-master-short-preamble": ("firmware/mdio_master.pasm", "ldi r0, PRE-2", "ldi r0, PRE-3",
                                      pytest("test/model/test_mdio.py -k exact_waveform")),
    "fw-mdio-master-write-no-release": ("firmware/mdio_master.pasm",
                                        "    pin MDIO, hiz           side 0 [H-1]  ; @point f\n",
                                        "    nop                     side 0 [H-1]  ; @point f\n",
                                        pytest("test/model/test_mdio.py -k master_turnaround")),
    "fw-mdio-master-late-sample": ("firmware/mdio_master.pasm",
                                   "    in pins, 1              side 1 [H-1]  ; @point r @point ta2",
                                   "    nop                     side 1\n    in pins, 1              side 1 [H-2]",
                                   pytest("test/model/test_mdio.py -k master_phy_clock_to_output")),
    "fw-mdio-master-no-idle": ("firmware/mdio_master.pasm",
                               "    nop                     side 1 [H-1]  ; one idle clock   @point r\n    ldi r0, 4",
                               "    ldi r0, 4", pytest("test/model/test_mdio.py -k exact_waveform")),
    "fw-mdio-master-misses-ta2": ("firmware/mdio_master.pasm", "    ldi r0, 16              side 0\n",
                                  "    ldi r0, 15              side 0\n", pytest("test/model/test_mdio.py -k exact_waveform")),
    "fw-mdio-phy-31-ones": ("firmware/mdio_phy.pasm", "    ldi r0, 31\n", "    ldi r0, 30\n",
                            pytest("test/model/test_mdio.py -k preamble_needs_32")),
    "fw-mdio-phy-zero-no-restart": ("firmware/mdio_phy.pasm", "    jmp !pin MDIO, h0 ", "    nop ;",
                                    pytest("test/model/test_mdio.py -k zero_in_preamble")),
    "fw-mdio-phy-any-address": ("firmware/mdio_phy.pasm", "    shr r1, 5                         ; ST1 OP1 OP0 PHYAD",
                                "    shr r1, 5\n    and r1, 0xE0\n    or r1, PHYAD",
                                pytest("test/model/test_mdio.py -k other_address")),
    "fw-mdio-phy-no-release": ("firmware/mdio_phy.pasm", "    pin MDIO, hiz                     ; @point drv", "    nop",
                               pytest("test/model/test_mdio.py -k phy_turnaround")),
    "fw-mdio-phy-early-ta": ("firmware/mdio_phy.pasm", "    wait rise MDC                     ; TA1: the STA",
                             "    pin MDIO, lo\n    wait rise MDC                     ; TA1: the STA",
                             pytest("test/model/test_mdio.py -k phy_turnaround")),
    "fw-mdio-phy-no-ta-check": ("firmware/mdio_phy.pasm", "    cmp r0, 1                         ; TA = 1, 0", "    cmp r0, r0",
                                pytest("test/model/test_mdio.py -k bad_turnaround")),
    "fw-mdio-phy-bmcr-no-self-clear": ("firmware/mdio_phy.pasm", "ldi r1, 0x7DFF", "ldi r1, 0xFFFF",
                                       pytest("test/model/test_mdio.py -k write_control")),
    "fw-mdio-phy-write-wrong-reg": ("firmware/mdio_phy.pasm", "    cmp r1, 4\n", "    cmp r1, 1\n",
                                  pytest("test/model/test_mdio.py -k read_only_writes")),
    "fw-mdio-phy-table-swap": ("firmware/mdio_phy.pasm", "    ldi r1, V5\n", "    ldi r1, V6\n",
                               pytest("test/model/test_mdio.py -k every_register")),
    "fw-mdio-phy-record-blocks": ("firmware/mdio_phy.pasm", "    push noblock\n", "    push block\n",
                                  pytest("test/model/test_mdio.py -k fifo_full")),
    "qsfp-no-page-rollover": ("tools/pe/qsfpdev.py", "return (a & 0x80) | ((a + 1) & 0x7F)", "return (a + 1) & 0xFF",
                              pytest("test/model/test_qsfpdev.py -k rolls")),
    "qsfp-accepts-any-page": ("tools/pe/qsfpdev.py", "self.lower[a] = b if b in PAGES else 0", "self.lower[a] = b if b in self.upper else self.page",
                              pytest("test/model/test_qsfpdev.py -k unsupported")),
    "qsfp-no-write-abort": ("tools/pe/qsfpdev.py", "self.pending = []            # a repeated START", "pass  # a repeated START",
                            pytest("test/model/test_qsfpdev.py -k repeated_start")),
    "qsfp-tears-a-read": ("tools/pe/qsfpdev.py", "v = self.view[self.ptr]", "v = self.image()[self.ptr]",
                          pytest("test/model/test_qsfpdev.py -k coherent")),
    "qsfp-temperature-scale": ("tools/pe/qsfpdev.py", "raw = round(celsius * 256)", "raw = round(celsius * 128)",
                               pytest("test/model/test_qsfpdev.py -k temperature_format")),
    "qsfp-vendor-offset": ("tools/pe/qsfpdev.py", "put(148, _ascii(vendor, 16))", "put(149, _ascii(vendor, 16))",
                           pytest("test/model/test_qsfpdev.py -k vendor_fields")),
    "qsfp-check-code": ("tools/pe/qsfpdev.py", "u[191 - 128] = check_code(u[:63])", "u[191 - 128] = check_code(u[1:63])",
                        pytest("test/model/test_qsfpdev.py -k check_codes")),
    "pmbus-mantissa-unsigned": ("tools/pe/pmbus.py", "y = y - 2048 if y & 0x400 else y", "y = y",
                                pytest("test/model/test_pmbus.py -k boundaries")),
    "pmbus-exponent-unsigned": ("tools/pe/pmbus.py", "    n = n - 32 if n & 0x10 else n\n    y =", "    y =",
                                pytest("test/model/test_pmbus.py -k boundaries")),
    "pmbus-returns-bad-pec": ("tools/pe/pmbus.py",
                              '    if record["result"] == "pec_bad":\n        raise BadPec(record)\n    if record["result"] != "pec_ok":',
                              '    if record["result"] not in ("pec_ok", "pec_bad"):',
                              pytest("test/model/test_pmbus.py -k every_single_bit")),
    "pmbus-byte-order": ("tools/pe/pmbus.py", "return high << 8 | low", "return low << 8 | high",
                         pytest("test/model/test_pmbus.py -k through_the_firmware")),
    "fw-smbus-pec-not-checked": ("firmware/smbus_host.pasm", "    ldi r1, 0x8200\n", "    ldi r1, 0x8100\n",
                                 pytest("test/model/test_pmbus.py -k every_single_bit")),
    # full-speed data over endpoint 0 (pe.usbfsdata; KV260 bug of 2026-11-03)
    "usbfs-zlp-staged-twice": ("tools/pe/usbfsdata.py",
                               "            self.incoming, self.expected = bytearray(), wlen   # service() stages the status ZLP once\n",
                               "            self.incoming, self.expected = bytearray(), wlen\n"
                               "            if not wlen:\n                self.pending = [b\"\"]\n",
                               pytest('test/model/test_usb_fs_data.py -k "zero_length_writes_with_software or nothing_left_staged"')),
    "usbfs-read0-no-status": ("tools/pe/usbfsdata.py", " % 8 == 0 or not wlen:", " % 8 == 0:",
                              pytest('test/model/test_usb_fs_data.py -k "round_trips and 0"')),
    "usbdev-zlp-at-wlength": ("tools/pe/usbdev.py", "if len(desc) % 8 == 0 and (len(desc) < wlen or not wlen):",
                              "if len(desc) % 8 == 0:", pytest('test/model/test_usb_fs_data.py -k "descriptor_ending or nothing_left_staged"')),
    "usbdev-restage-in-setup": ("tools/pe/usbdev.py", "return bool(flags & F_SETUP or rx_level)", "return bool(rx_level)",
                                pytest(f'{FSDATA} "no_restage and token"')),
    "usbdev-restage-after-setup": ("tools/pe/usbdev.py", "return bool(flags & F_SETUP or rx_level)",
                                   "return bool(flags & F_SETUP)", pytest(f'{FSDATA} "no_restage and not token"')),
    "usbdev-restage-unchecked": ("tools/pe/usbdev.py", "if elapsed >= 100 and not self.setup_since(flags):",
                                 "if elapsed >= 100:", pytest(f'{FSDATA} "no_restage and decision"')),
    "usbdev-fset-unchecked-before": ("tools/pe/usbdev.py",
                                     "        if self.setup_since(mh.read(sim.A_FSET)):\n            self.dirty = True",
                                     "        if False:\n            self.dirty = True", pytest(f'{FSDATA} "no_restage and first"')),
    "usbdev-fset-unchecked-after": ("tools/pe/usbdev.py",
                                    "        if self.setup_since(mh.read(sim.A_FSET)):\n            mh.write(sim.A_FCLR, F_STAGED)",
                                    "        if False:\n            mh.write(sim.A_FCLR, F_STAGED)", pytest(f'{FSDATA} "no_restage and FSET"')),
    "fw-usb-fs-no-in-guard": ("firmware/usb_fs_dev.pasm", "    jmp txne, send_data\nnak_in:", "    jmp send_data\nnak_in:",
                              pytest('test/model/test_usb_in_guard.py -k "fifo-full"')),
    "hw-guard-run-signals-ignored": ("fpga/kv260/host/usb_guard_run.py",
                                     "            signal.signal(s, self.handler)", "            pass",
                                     pytest('test/model/test_usb_guard_run.py -k "signal_stops"')),
    "hw-guard-run-child-not-told": ("fpga/kv260/host/usb_guard_run.py",
                                    "                self.child.send_signal(signal.SIGTERM)", "                pass",
                                    pytest('test/model/test_usb_guard_run.py -k "signal_stops"')),
    "hw-guard-run-no-give-up": ("fpga/kv260/host/usb_guard_run.py", "                if empty == a.give_up:",
                                "                if False:", pytest('test/model/test_usb_guard_run.py -k "no_device_stops"')),
    "hw-demo-dies-on-sigterm": ("fpga/kv260/host/usb_fs_data_demo.py",
                                "    for s in STOP_SIGNALS:\n        signal.signal(s, handler)",
                                "    pass", pytest('test/model/test_usb_guard_run.py -k "signal_stops"')),
    "hw-demo-client-keeps-handlers": ("fpga/kv260/host/usb_fs_data_demo.py",
                                      "            for s in STOP_SIGNALS:\n                signal.signal(s, signal.SIG_DFL)\n",
                                      "", pytest('test/model/test_usb_fs_data_demo_board.py -k "group_signal"')),
    "hw-demo-client-recorded-late": ("fpga/kv260/host/usb_fs_data_demo.py",
                                     '    LIVE["client"] = child\n    signal.pthread_sigmask(signal.SIG_SETMASK, old)\n',
                                     '    signal.pthread_sigmask(signal.SIG_SETMASK, old)\n    LIVE["client"] = child\n',
                                     pytest('test/model/test_usb_fs_data_demo_board.py -k "fork_unblock"')),
    "hw-demo-client-exception-escapes": ("fpga/kv260/host/usb_fs_data_demo.py",
                                         "        try:\n"
                                         "            for s in STOP_SIGNALS:\n"
                                         "                signal.signal(s, signal.SIG_DFL)\n"
                                         "            signal.pthread_sigmask(signal.SIG_SETMASK, old)\n"
                                         "            os._exit(client(sizes, a.rounds, a.seed, a.nodata, a.rt, a.rq))\n"
                                         "        finally:\n"
                                         "            os._exit(1)\n",
                                         "        for s in STOP_SIGNALS:\n"
                                         "            signal.signal(s, signal.SIG_DFL)\n"
                                         "        signal.pthread_sigmask(signal.SIG_SETMASK, old)\n"
                                         "        os._exit(client(sizes, a.rounds, a.seed, a.nodata, a.rt, a.rq))\n",
                                         pytest('test/model/test_usb_fs_data_demo_board.py -k "client_that_raises"')),
    "hw-guard-run-raw-buffer-size": ("fpga/kv260/host/usb_guard_run.py",
                                     "        if size.isdigit():\n            put(\"buffer_size_kb\", size + \"\\n\")",
                                     "        if True:\n            put(\"buffer_size_kb\", self.saved[\"buffer_size_kb\"])",
                                     pytest('test/model/test_usb_guard_run.py -k "fake_tracefs"')),
    "fw-usb-ls-tx-no-setup-flush": ("firmware/usb_ls_tx.pasm",
                                    "    jmp f F_SETUP, setup_flush     ; a SETUP aborts the transfer (usb_ls_rx clears F_STAGED)\n",
                                    "", pytest('test/model/test_usb_in_guard.py -k "setup_drops"')),
    "fw-usb-ls-tx-in-kept-in-setup": ("firmware/usb_ls_tx.pasm",
                                      "    flag clr F_IN                  ; the host software pushed for the abandoned transfer,\n",
                                      "    nop\n", pytest('test/model/test_usb_in_setup.py -k "two_core"')),
    "fw-usb-ls-tx-no-in-guard": ("firmware/usb_ls_tx.pasm",
                                 "    jmp txne, staged               ; F_STAGED over an empty TX FIFO (flushed below): NAK\n",
                                 "    jmp staged\n", pytest('test/model/test_usb_in_guard.py -k "two_core_in_with"')),
    "fw-usb-ls-no-in-guard": ("firmware/usb_ls_dev.pasm", "    jmp txne, send_data\nnak_in:", "    jmp send_data\nnak_in:",
                              pytest('test/model/test_usb_in_guard.py -k "fifo-low"')),
    "usbdev-dirty-not-cleared": ("tools/pe/usbdev.py", "        if self.dirty:                             # the host waits",
                                 "        if False:                                  # the host waits",
                                 pytest(f'{FSDATA} "no_restage and first"')),
    # the 10BASE-T stack (`python3 test/mutants.py 'eth-*'`)
    "eth-fw-no-window": ("firmware/eth10_tx_cs.pasm", "    jmp t, frame              side 0       ; quiet all the way",
                         "    jmp frame                 side 0       ; quiet all the way", ETH("carrier_sense_defers")),
    "eth-fw-95-bit-window": ("firmware/eth10_tx_cs.pasm", ".define IFG_TICKS 96 * 4", ".define IFG_TICKS 95 * 4",
                             ETH("carrier_sense_defers")),
    "eth-fw-no-timer-restart": ("firmware/eth10_tx_cs.pasm",
                                "    ldi r1, 9999              side 0       ; the next link pulse is 16 ms away",
                                "    nop                       side 0       ; the next link pulse is 16 ms away",
                                ETH("restarts_the_pulse_timer")),
    "eth-no-fcs-check": ("tools/pe/eth.py", "if not MIN_FRAME <= len(raw) <= MAX_FRAME or not fcs_ok(raw):",
                         "if not MIN_FRAME <= len(raw) <= MAX_FRAME:", ETH("no_reply_at_the_pads")),
    "eth-no-size-check": ("tools/pe/eth.py", "if not MIN_FRAME <= len(raw) <= MAX_FRAME or not fcs_ok(raw):",
                          "if not fcs_ok(raw):", ETH("illegal_sizes_at_the_pads")),
    "eth-no-mac-check": ("tools/pe/eth.py", "if dst != self.mac and dst != BROADCAST:", "if False:",
                         ETH("no_reply_at_the_pads")),
    "eth-no-ip-check": ("tools/pe/eth.py", 'if dst != self.ip or src == b"\\xff\\xff\\xff\\xff" or src == bytes(4):',
                        'if src == b"\\xff\\xff\\xff\\xff" or src == bytes(4):', ETH("no_reply_at_the_pads")),
    "eth-no-ip-source-check": ("tools/pe/eth.py", 'if dst != self.ip or src == b"\\xff\\xff\\xff\\xff" or src == bytes(4):',
                               "if dst != self.ip:", ETH("station_rejects")),
    "eth-no-ip-length-check": ("tools/pe/eth.py", "if ihl < 20 or total < ihl or total > len(p) or checksum(p[:ihl]):",
                               "if ihl < 20 or total < ihl or checksum(p[:ihl]):", ETH("station_rejects")),
    "eth-rejects-dribble-bits": ("tools/pe/eth.py", "    if start is None:\n", "    if start is None or (count - start) & 7:\n",
                                 ETH("core_behind_at_a_frames_end")),
    "eth-spim-select-not-nested": ("fpga/kv260/host/eth_tap.py",
                                   "            if sel is not None:\n                spim.enable_quad(False)\n        finally:\n",
                                   "            if sel is not None:\n                spim.enable_quad(False)\n        except ZeroDivisionError:\n            pass\n        else:\n",
                                   ETH("stuck_master")),
    "eth-spim-no-sighup": ("fpga/kv260/host/eth_tap.py", "signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)",
                           "signals = (signal.SIGINT, signal.SIGTERM)", ETH("measure_spim_signal")),
    "eth-spim-second-signal": ("fpga/kv260/host/eth_tap.py", "        live[0] = False            # from here on a signal raises nothing\n",
                               "        live[0] = True\n", ETH("second_signal")),
    "eth-echoes-padding": ("tools/pe/eth.py", "return p[12:16], p[16:20], p[9], p[ihl:total], p[8]",
                           "return p[12:16], p[16:20], p[9], p[ihl:], p[8]", ETH("station_answers")),
    "eth-blind-fifo-read": ("tools/pe/ethhost.py", "        n = (st >> 4) & 15\n", "        n = 8\n", ETH("arp_then_ping")),
    "eth-no-rx-recovery": ("tools/pe/ethhost.py", "            self._rx_recover()\n", "",
                           ETH("overflow_loses_one_frame")),
    "eth-no-overflow-check": ("tools/pe/ethhost.py", "if self.port.read(A_LINESTAT) & LS_RX_OVERFLOW:", "if False:",
                              ETH("overflow_loses_one_frame")),
    "eth-no-underrun-rule": ("tools/pe/ethhost.py", "if self.go and left and self.since_us(t0) > (txf + rxf) * WORD_US:",
                             "if False:", ETH("tx_underrun_abandons")),
    "eth-tx-load-during-rx": ("tools/pe/ethhost.py", "if not self.go or self.skipped & 7:", "if False:",
                              ETH("rate_table and 50000000.0-True-7")),
    "eth-no-deferral-limit": ("tools/pe/ethhost.py", "late = self.since_us(self.t_go) > self.tx_timeout_us", "late = False",
                              ETH("line_never_quiet")),
    "eth-wrong-rx-period": ("tools/pe/ethhost.py", "return (1 << 8) | 0x40, 5 ", "return (1 << 8) | 0x40, 4 ",
                            ETH("arp_then_ping")),
    # match, then answer (`python3 test/mutants.py 'reply-*'`, docs/eth10-reaction.md)
    "reply-fw-no-window": ("firmware/eth10_reply.pasm", "    jmp t, first\n", "    jmp first\n", REPLY50),
    "reply-95-bit-window": ("tools/pe/ethreply.py", "IFG_BITS = 96", "IFG_BITS = 95",
                            REPLY("match_then_reply_timing and 40000000.0")),
    "reply-window-a-clock-short": ("tools/pe/ethreply.py", "WINDOW_TRIM = 8", "WINDOW_TRIM = 9", REPLY50),
    "reply-fw-no-low-wait": ("firmware/eth10_reply.pasm", "    wait 0 pin RXP                ;",
                             "    nop                           ;", REPLY50),
    "reply-fw-carrier-not-heard": ("firmware/eth10_reply.pasm", "    jmp frame                     ; carrier:",
                                   "    jmp defer                     ; carrier:", REPLY("back_to_back")),
    "reply-fw-quiet-ends-on-carrier": ("firmware/eth10_reply.pasm", "    jmp quiet\n", "    jmp ended\n",
                                       REPLY("frame_just_after_our_reply and 50000000.0")),
    "reply-quiet-too-short": ("tools/pe/ethreply.py", "QUIET_BITS = 40", "QUIET_BITS = 30",
                              REPLY("back_to_back and 50000000.0")),
    "reply-fw-owed-not-counted": ("firmware/eth10_reply.pasm", "    add r0, 1 ", "    nop        ", REPLY50),
    "reply-fw-owed-never-paid": ("firmware/eth10_reply.pasm", "    sub r0, 1\n", "    nop\n", REPLY50),
    "reply-fw-second-preamble": ("firmware/eth10_reply.pasm", "    ldi r1, 0x5555                ; the next",
                                 "    ldi r1, 0x5554                ; the next", REPLY("back_to_back and 40000000.0")),
    "reply-short-tp-idl": ("tools/pe/ethreply.py", "END_BITS = 3 ", "END_BITS = 2 ", REPLY50),
    "reply-tx-rate": ("tools/pe/ethreply.py", "(128 if p == 5 else 0)", "(64 if p == 5 else 0)", REPLY50),
    "reply-chunks-any-last-bit": ("tools/pe/ethreply.py", "    if raw[-1] >> 7:", "    if False:", REPLY("chunks_refuse")),
    "reply-chunks-no-fcs-check": ("tools/pe/ethreply.py", "    if not eth.fcs_ok(raw):", "    if False:",
                                  REPLY("chunks_refuse")),
    "reply-wire-phase-ignored": ("tools/pe/ethsim.py", ' + q["offset"]', "", REPLY("blind_window and 50000000.0")),
    "hw-no-prereq-check": ("fpga/kv260/host/hw_day.py", '    if left:\n',
        '    if False:\n', HW_TESTS),
    "hw-direct-needs-only": ("fpga/kv260/host/hw_day.py", '    return [n for n in closure(step) if not fresh(state, n)]',
        '    return [n for n in step.needs if not fresh(state, n)]', HW_TESTS),
    "hw-never-expires": ("fpga/kv260/host/hw_day.py", '    return 0 <= age < MAX_AGE_S',
        '    return age >= 0', HW_TESTS),
    "hw-future-time-counts": ("fpga/kv260/host/hw_day.py", '    return 0 <= age < MAX_AGE_S',
        '    return age < MAX_AGE_S', HW_TESTS),
    "hw-usb-pi-keeps-usb-off": ("fpga/kv260/host/hw_day.py", '         clears=("usb-off",),\n',
        '', HW_TESTS),
    "hw-clear-only-at-pass": ("fpga/kv260/host/hw_day.py", '            if step.clears:                 # the wiring is on now, whatever happens next',
        '            if False:', HW_TESTS),
    "hw-rx-no-usb-removal": ("fpga/kv260/host/hw_day.py", '         remove=("USB plug out of the Pi first, before anything else here", "USB cable\'s black wire out of a24, and taped",\n',
        '         remove=(\n', HW_TESTS),
    "hw-root-at-import": ("fpga/kv260/host/hw_day.py", 'STATE = "/var/tmp/hw_day_state.json"',
        'ROOT = HERE.parents[2]\nSTATE = "/var/tmp/hw_day_state.json"', HW_TESTS),
    "hw-phrase-ignored": ("fpga/kv260/host/hw_day.py", 'if prompt(read, "> ") != step.typed():',
        'if prompt(read, "> ") == "never":', HW_TESTS),
    "hw-pins-not-turned-off": ("fpga/kv260/host/hw_day.py", '                        rig.spim.pmod({})\n                    finally:',
        '                        pass\n                    finally:', HW_TESTS),
    "hw-close-skipped": ("fpga/kv260/host/hw_day.py", '                    finally:\n                        rig.close()',
        '                    finally:\n                        pass', HW_TESTS),
    "hw-signal-interrupts-off": ("fpga/kv260/host/hw_day.py", '                    sig.live = False             # nothing interrupts the turning off\n',
        '', HW_TESTS),
    "hw-sighup-not-caught": ("fpga/kv260/host/hw_day.py", 'NAMES = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)',
        'NAMES = (signal.SIGINT, signal.SIGTERM)', HW_TESTS),
    "hw-off-writes-nothing": ("fpga/kv260/host/hw_day.py", '    Spim().pmod({})\n',
        '    Spim()\n', HW_TESTS),
    "hw-off-always-refuses": ("fpga/kv260/host/hw_day.py", "        fabric_guard.check(listapps_fn or listapps)\n    except",
        "        raise fabric_guard.NoActiveApp('x')\n    except", HW_TESTS),
    "hw-off-false-reassurance": ("fpga/kv260/host/hw_day.py",
        '"NOTHING WAS WRITTEN: don\'t assume the pins are off. Pull the wires from J2, "', '"every pin is off "', HW_TESTS),
    "hw-forget-does-nothing": ("fpga/kv260/host/hw_day.py", '            state.pop(a.step, None)\n',
        '', HW_TESTS),
    "hw-i2c-capture-drives": ("fpga/kv260/host/hw_day.py", '    k.pfd.setup(rig)\n    la_load(rig, k, 0b11)\n',
        '    k.pfd.setup(rig, poe=0b11)\n    la_load(rig, k, 0b11)\n', HW_TESTS),
    "hw-tx-bench-pins-first": ("fpga/kv260/host/hw_day.py", '    k.pfd.setup(rig)\n    ctx.spim.pmod({0: UO0, 1: UO1})\n    for column',
        '    ctx.spim.pmod({0: UO0, 1: UO1})\n    k.pfd.setup(rig)\n    for column', HW_TESTS),
    "hw-i2c-lines-not-released": ("fpga/kv260/host/hw_day.py", 'owners={UIO0: 1, UIO1: 1}, od=0b11, pout=0b11',
        'owners={UIO0: 1, UIO1: 1}, od=0, pout=0', HW_TESTS),
    "hw-pi-send-on-a-network": ("fpga/kv260/host/hw_day.py", '    if default_route_on(iface, route):\n',
        '    if False:\n', HW_TESTS),
    "hw-pi-send-off-a-pi": ("fpga/kv260/host/hw_day.py", '    if not is_pi(model):\n        raise Refused("pi-send',
        '    if False:\n        raise Refused("pi-send', HW_TESTS),
    "hw-pi-send-no-link": ("fpga/kv260/host/hw_day.py", '    if "carrier 1 " not in link_line(iface, net):',
        '    if False:', HW_TESTS),
    "hw-pi-watch-off-a-pi": ("fpga/kv260/host/hw_day.py", '    if not is_pi(model):\n        raise Refused("pi-watch',
        '    if False:\n        raise Refused("pi-watch', HW_TESTS),
    "hw-no-app-check": ("fpga/kv260/host/hw_day.py", "app = check_app(step, listapps_fn)",
        'app = "protoemu40"', HW_TESTS),
    "hw-wrong-app": ("fpga/kv260/host/hw_day.py", '    if app not in step.apps:\n',
        '    if False:\n', HW_TESTS),
    "hw-guard-open": ("fpga/kv260/host/hw_day.py", 'if tt is not None and tt not in self._allowed.get(pin, ()):',
        'if False:', HW_TESTS),
    "hw-other-scripts-ignored": ("fpga/kv260/host/hw_day.py", '        if others:\n',
        '        if False:\n', HW_TESTS),
    "hw-lock-open": ("fpga/kv260/host/hw_day.py", 'fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)',
        'pass', HW_TESTS),
    "hw-pass-not-asked": ("fpga/kv260/host/hw_day.py", 'if prompt(read, "> ") != phrase:',
        'if False:', HW_TESTS),
    "hw-usb-off-not-needed": ("fpga/kv260/host/hw_day.py", 'Step("rails", "Find GND and 3.3 V on J2 without a meter", needs=("usb-off",)',
        'Step("rails", "Find GND and 3.3 V on J2 without a meter", needs=("preflight",)', HW_TESTS),
    "hw-isolated-cable-not-needed": ("fpga/kv260/host/hw_day.py", 'needs=("tx-pi",), apps=("protoemu40",),\n         action="tx", pins={0: {UO0}, 1: {UO1}}, phrase="not poe',
        'needs=("rails",), apps=("protoemu40",),\n         action="tx", pins={0: {UO0}, 1: {UO1}}, phrase="not poe', HW_TESTS),
    "hw-poe-phrase-gone": ("fpga/kv260/host/hw_day.py", 'phrase="not poe wired tx-switch",',
        '', HW_TESTS),
    "hw-read-pin": ("fpga/kv260/host/hw_day.py", 'READ_PIN = 3 ',
        'READ_PIN = 2 ', HW_TESTS),
    "hw-tx-divider": ("fpga/kv260/host/hw_day.py", '{40e6: 1 << 8, 50e6: 320}',
        '{40e6: 320, 50e6: 320}', HW_TESTS),
    "hw-rails-one-read": ("fpga/kv260/host/hw_day.py", 'need(reads == [1, 0, 1]',
        'need(reads[0] == 1', HW_TESTS),
    "hw-clk-div-unchecked": ("fpga/kv260/host/hw_day.py", 'if rig.ident["clk_div"] != APPS[app]:',
        'if False:', HW_TESTS),
    "hw-stuck-pad-ignored": ("fpga/kv260/host/hw_day.py", 'need(high < 0.5,',
        'need(True,', HW_TESTS),
    "hw-eth-stack-unchecked": ("fpga/kv260/host/hw_day.py", '    if absent:\n        raise Refused(f"{step.name} needs',
        '    if False:\n        raise Refused(f"{step.name} needs', HW_TESTS),
    "hw-ethsim-unlisted": ("fpga/kv260/host/hw_day.py", '"tools/pe/eth.py", "tools/pe/ethsim.py", "firmware',
        '"tools/pe/eth.py", "firmware', HW_TESTS),
    "hw-hit-below-threshold": ("fpga/kv260/host/hw_day.py", 'need(not any(r[0] is not None for r in results[3000])',
        'need(True or any(r[0] is not None for r in results[3000])', HW_TESTS),
    "hw-sampler-too-short": ("fpga/kv260/host/hw_day.py", 'rig.burst(1, 4096, 1, True)[9:]',
        'rig.burst(1, 16, 1, True)[9:]', HW_TESTS),
    "hw-hit-pin-wrong": ("fpga/kv260/host/hw_day.py", '.replace(".define HIT 8", ".define HIT 1")',
        '.replace(".define HIT 8", ".define HIT 3")', HW_TESTS),
    "hw-mitm-wrong-register": ("fpga/kv260/host/hw_day.py", 'reg = min(mem)',
        'reg = max(mem)', HW_TESTS),
    "hw-rx-parts-overlap": ("fpga/kv260/host/hw_day.py", '("1 kohm", "d17 to d21"),',
        '("1 kohm", "d17 to d27"),', HW_TESTS),
    "hw-cut-no-wiring": ("fpga/kv260/host/hw_day.py", 'phrase="pin risk accepted wired cut",\n         wires=RX_WIRES,',
        'phrase="pin risk accepted wired cut",\n         wires=(("Same as step rx", "nothing moves"),),', HW_TESTS),
    "hw-corrupt-state-crashes": ("fpga/kv260/host/hw_day.py", '    except ValueError:\n        state = None\n',
        '', HW_TESTS),
    "hw-spim-unguarded": ("fpga/kv260/host/pe_spim.py", '        fabric_guard.require_app()',
        '        pass', HW_TESTS),
    "hw-kria-unguarded": ("fpga/kv260/host/pe_kria.py", '        fabric_guard.require_app()',
        '        pass', HW_TESTS),
    "hw-lab-unguarded": ("fpga/kv260/host/pe_lab.py", '        fabric_guard.require_app()',
        '        pass', HW_TESTS),
    "hw-guard-any-line-a-row": ("fpga/kv260/host/fabric_guard.py", '        r = row(line)\n',
        '        r = row(line) or (t[0], t[-1])\n', HW_TESTS),
    "hw-guard-column-count": ("fpga/kv260/host/fabric_guard.py", '    if len(t) == len(HEADER):\n',
        '    if len(t) >= len(HEADER):\n', HW_TESTS),
    "hw-guard-slot-count-unchecked": ("fpga/kv260/host/fabric_guard.py", 'or not SLOTS.fullmatch(slots) or',
        'or', HW_TESTS),
    "hw-guard-merged-any-suffix": ("fpga/kv260/host/fabric_guard.py",
        'if len(merged) <= len(name) or not merged.endswith(name):', 'if len(merged) <= len(name):', HW_TESTS),
    "hw-guard-merged-not-read": ("fpga/kv260/host/fabric_guard.py", '    elif len(t) == len(HEADER) - 1:',
        '    elif False:', HW_TESTS),
    "hw-guard-base-not-name": ("fpga/kv260/host/fabric_guard.py", 'not NAME.fullmatch(name) or base != name or',
        'not NAME.fullmatch(name) or', HW_TESTS),
    "hw-guard-any-type": ("fpga/kv260/host/fabric_guard.py", 'or not TYPE.fullmatch(kind) or', 'or', HW_TESTS),
    "hw-guard-any-pid": ("fpga/kv260/host/fabric_guard.py", ' or pid != PID\n', '\n', HW_TESTS),
    "hw-guard-any-base-type": ("fpga/kv260/host/fabric_guard.py", 'or base_type != kind or', 'or', HW_TESTS),
    "hw-guard-duplicate-rows": ("fpga/kv260/host/fabric_guard.py", '    if len(set(names)) != len(names):\n',
        '    if False:\n', HW_TESTS),
    "hw-preflight-substring": ("fpga/kv260/host/hw_day.py",
        '    apps = [name for name, _ in fabric_guard.rows(listapps())]', '    apps = listapps()', HW_TESTS),
    "hw-guard-slot-field-loose": ("fpga/kv260/host/fabric_guard.py", 'ACTIVE = re.compile(r"\\d+->\\d+,")',
        'ACTIVE = re.compile(r"\\d+.*")', HW_TESTS),
    "hw-guard-two-active": ("fpga/kv260/host/fabric_guard.py", '    if len(active) > 1:\n',
        '    if False:\n', HW_TESTS),
    "hw-guard-no-header-needed": ("fpga/kv260/host/fabric_guard.py", '    if headers != 1 or not out:\n',
        '    if not out:\n', HW_TESTS),
    "hw-guard-prefix-match": ("fpga/kv260/host/fabric_guard.py", '    if app not in KNOWN:\n',
        '    if not app.startswith("protoemu"):\n', HW_TESTS),
    "hw-guard-firmware-absent-ok": ("fpga/kv260/host/fabric_guard.py", '    if f != app + ".bit.bin":\n',
        '    if f is not None and f != app + ".bit.bin":\n', HW_TESTS),
    "hw-guard-firmware-ignored": ("fpga/kv260/host/fabric_guard.py", '    if f != app + ".bit.bin":\n',
        '    if False:\n', HW_TESTS),
    "hw-guard-state-absent-ok": ("fpga/kv260/host/fabric_guard.py", '    if s != "operating":\n',
        '    if s is not None and s != "operating":\n', HW_TESTS),
    "hw-guard-state-ignored": ("fpga/kv260/host/fabric_guard.py", '    if s != "operating":\n',
        '    if False:\n', HW_TESTS),
    "hw-guard-not-installed-ok": ("fpga/kv260/host/fabric_guard.py", '    if not (firmware_dir / app / f"{app}.bit.bin").is_file():\n',
        '    if False:\n', HW_TESTS),
    "hw-guard-old-dt-path": ("fpga/kv260/host/fabric_guard.py", 'FIRMWARE_NAME = "/proc/device-tree/fpga-region/firmware-name"',
        'FIRMWARE_NAME = "/proc/device-tree/fpga-full/firmware-name"', HW_TESTS),
    "hw-guard-xmutil-status": ("fpga/kv260/host/fabric_guard.py", 'if r.returncode:',
        'if False:', HW_TESTS),
    "hw-guard-refusal-cached": ("fpga/kv260/host/fabric_guard.py", '        _checked = check(**kw)\n',
        "        try:\n            _checked = check(**kw)\n        except NoActiveApp:\n            _checked = 'protoemu'\n", HW_TESTS),
    "hw-guard-no-sequence": ("fpga/kv260/host/fabric_guard.py", '{SEQUENCE}. {STOP}")\n    if app not in KNOWN',
        '{STOP}")\n    if app not in KNOWN', HW_TESTS),
    "hw-recover-expires": ("fpga/kv260/host/hw_day.py", '    if name in BY_NAME and BY_NAME[name].once:\n        return age >= 0\n',
        '', HW_TESTS),
    "hw-recover-not-once": ("fpga/kv260/host/hw_day.py", 'Step("recover", "Bring the board back; find out why loadapp failed", once=True,',
        'Step("recover", "Bring the board back; find out why loadapp failed",', HW_TESTS),
    "hw-check-app-skips-guard": ("fpga/kv260/host/hw_day.py", '        app = fabric_guard.check(read or listapps)\n',
        '        app = (read or listapps)().split()[0]\n', HW_TESTS),
    "hw-off-unguarded": ("fpga/kv260/host/hw_day.py", '        fabric_guard.check(listapps_fn or listapps)\n',
        '        pass\n', HW_TESTS),
    "hw-chained-load-line": ("fpga/kv260/host/hw_day.py", 'f"  sudo xmutil unloadapp\\n  sudo xmutil loadapp {app}\\n  sudo xmutil listapps\\n"',
        'f"  sudo xmutil unloadapp; sudo xmutil loadapp {app} && sudo xmutil listapps\\n"', HW_TESTS),
    # the tailnet name back as install.sh's default (split so this file stays clean)
    "privacy-kria-default-host": ("fpga/kv260/install.sh", 'kria=${WL_KRIA:-${KRIA:-}}',
        'kria=${WL_KRIA:-${KRIA:-ubuntu@kria.tail' '[tailnet label redacted].ts.net}}', pytest("test/model/test_private_hosts.py")),
    # any other default host: install.sh must still refuse with no host given
    "privacy-kria-other-default": ("fpga/kv260/install.sh", 'kria=${WL_KRIA:-${KRIA:-}}',
        'kria=${WL_KRIA:-${KRIA:-ubuntu@kria.local}}', pytest("test/model/test_private_hosts.py -k install")),
    # eth_tap's FPGA backend through the KV260 fabric (`python3 test/mutants.py 'fab-*'`)
    "fab-zero-words-not-restored": ("fpga/kv260/host/fabric_eth.py", "        out += words + [last, w]",
        "        out += [x for x in words if x] + [last, w]", FAB),
    "fab-last-word-dropped": ("fpga/kv260/host/fabric_eth.py", "        last = burst[i - 1] if i else 0",
        "        last = 0", FAB),
    "fab-no-cadence-check": ("fpga/kv260/host/fabric_eth.py", "if not top < k < full or abs(j - i0 - k * r) > tol:",
        "if not top < k < full:", FAB),
    "fab-cadence-not-refined": ("fpga/kv260/host/fabric_eth.py", "                r = (j - i0) / k\n", "", FAB),
    "fab-generator-unbounded": ("fpga/kv260/host/fabric_eth.py", "    if len(out) > PG_DEPTH:\n", "    if False:\n", FAB),
    "fab-tx-fifos-unbounded": ("fpga/kv260/host/fabric_eth.py", "if not eth.MIN_FRAME <= len(raw) <= TX_MAX:",
        "if not eth.MIN_FRAME <= len(raw) <= eth.MAX_FRAME:", FAB),
    "fab-both-high-uncounted": ("fpga/kv260/host/fabric_eth.py", "        both += v == 3\n", "        both += 0\n", FAB),
    "fab-any-app-will-do": ("fpga/kv260/host/fabric_eth.py",
        "    fabric_guard.require_app(expect=fabric_guard.APP_FOR_MHZ[round(clock_hz / 1e6)])",
        "    fabric_guard.require_app()", FAB),
    "fab-guard-expect-ignored": ("fpga/kv260/host/fabric_guard.py", "    if expect is not None and _checked != expect:\n",
        "    if False:\n", FAB),
    "fab-measure-spim-any-app": ("fpga/kv260/host/eth_tap.py",
        "fabric_guard.require_app(expect=fabric_guard.APP_FOR_MHZ[int(a.clock)])", "fabric_guard.require_app()", FAB),
    "fab-board-not-closed": ("fpga/kv260/host/eth_tap.py", "            backend.close()\n", "            pass\n", FAB),
    "fab-no-sighup": ("fpga/kv260/host/eth_tap.py", "    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)",
        "    signals = (signal.SIGTERM, signal.SIGINT)", FAB),
    "fab-sigint-not-ours": ("fpga/kv260/host/eth_tap.py", "    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)",
        "    signals = (signal.SIGHUP, signal.SIGTERM)", FAB),
    "fab-stop-keeps-live": ("fpga/kv260/host/eth_tap.py",
        "        if live[0]:\n            live[0] = False\n            raise KeyboardInterrupt if signum == signal.SIGINT",
        "        if live[0]:\n            raise KeyboardInterrupt if signum == signal.SIGINT", FAB),
    "fab-way-out-live-on-return": ("fpga/kv260/host/eth_tap.py",
        "        live[0] = False                        # from here on, signals are ignored\n", "", FAB),
    "fab-way-out-live-on-exception": ("fpga/kv260/host/eth_tap.py",
        "        live[0] = False                        # an exception from make or the body, Ctrl-C included\n",
        "", FAB),
    "fab-holder-not-closed": ("fpga/kv260/host/eth_tap.py", "            if not held.close() and backend is not None:",
        "            if backend is not None:", FAB),
    "fab-compare-blind-to-edges": ("fpga/kv260/host/eth_tap.py", "if seen in phases:", "if True:", FAB),
    "fab-compare-blind-to-pulses": ("fpga/kv260/host/eth_tap.py",
        'return dict(pulses=[list(p) for p in r.get("tx_pulses") or []], edges=r.get("tx_edges"))',
        'return dict(pulses=[], edges=r.get("tx_edges"))', FAB),
    "fab-probe-ignores-pulse": ("fpga/kv260/host/eth_tap.py",
        "eth.fcs_ok(f) and link_pulse_ok(tx, fab.clk_ns)", "eth.fcs_ok(f)", FAB),
    "fab-give-back-leaves-quad": ("fpga/kv260/host/fabric_eth.py", "            spim.write(0x80B, 0)  ",
        "            pass  ", FAB),
    "fab-give-back-sel-read-unguarded": ("fpga/kv260/host/fabric_eth.py", "        step(quad_off)\n",
        "        quad_off()\n", FAB),
    "fab-held-after-the-rig": ("fpga/kv260/host/fabric_eth.py",
        "    hold(closer)\n    board.reset()                                # what Hw() does when it builds its own board\n"
        "    closer.rig = pfd.HwRig(hw=Hw(board=board, spim=spim, lab=lab))\n",
        "    board.reset()\n    closer.rig = pfd.HwRig(hw=Hw(board=board, spim=spim, lab=lab))\n    hold(closer)\n", FAB),
    "fab-closer-gives-back-parts-only": ("fpga/kv260/host/fabric_eth.py", "        if self.rig is not None:\n            try:",
        "        if False:\n            try:", FAB),
    "fab-rig-built-before-handlers": ("fpga/kv260/host/eth_tap.py", "        return run_closing(make, run_probe)",
        "        return run_closing(lambda held, b=make(Held()): b, run_probe)", FAB),
    "fab-bad-fcs-to-linux": ("fpga/kv260/host/eth_tap.py", "            if f is None or not eth.fcs_ok(f):",
        "            if f is None:", FAB),
    "fab-port-not-given-back": ("fpga/kv260/host/postfreeze_demo.py", "        self.spim.select(fabric=False)\n",
        "        pass\n", FAB),
}


def result(work, cmd):
    """Only an executed test's failure kills a mutant. Missing XML, collection
    errors, compilation failures and empty selections are infrastructure errors.
    """
    xml = work / ("test/m.xml" if cmd.startswith("cd test") else "m.xml")
    xml.unlink(missing_ok=True)
    rc = subprocess.run(cmd, shell=True, cwd=work, env=os.environ).returncode
    if not xml.exists():
        return "INFRASTRUCTURE (no fresh test results)"
    try:
        cases = ET.parse(xml).findall(".//testcase")
    except ET.ParseError:
        return "INFRASTRUCTURE (invalid test results)"
    if not cases or any(c.find("error") is not None for c in cases):
        return "INFRASTRUCTURE (test collection/execution error)"
    failed = [c.get("name", "?") for c in cases if c.find("failure") is not None]
    if failed:
        return "killed: " + ", ".join(failed)
    if rc != 0 or not any(c.find("skipped") is None for c in cases):
        return "INFRASTRUCTURE (runner failed or no tests executed)"
    return "pass"


def source_files(root):
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [f for f in files if (root / f).is_file()]


def snapshot(scratch):
    """Copy the checkout (tracked and new files) once: every baseline and mutant
    starts from this copy, so edits made while the run goes on don't leak in."""
    work = pathlib.Path(scratch) / "candidate"
    work.mkdir()
    files = source_files(ROOT)
    for name in files:
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, work / name)
    return work, files


def run(name, scratch, baselines, candidate, files):
    path, old, new, cmd = MUTANTS[name]
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"{name}-", dir=scratch))
    tar = subprocess.Popen(["tar", "cf", "-", "--", *files], cwd=candidate, stdout=subprocess.PIPE)
    subprocess.run(["tar", "xf", "-"], cwd=work, stdin=tar.stdout, check=True)
    if tar.wait() != 0:
        return "INFRASTRUCTURE (source copy failed)"
    f = work / path
    src = f.read_text()
    if src.count(old) != 1:
        return "STALE (mutation site not found)"
    if cmd not in baselines:
        baseline = result(work, cmd)
        baselines[cmd] = baseline
        log = work / ("test/m.log" if cmd.startswith("cd test") else "m.log")
        if log.exists():
            log.rename(log.with_name("baseline.log"))
        xml = work / ("test/m.xml" if cmd.startswith("cd test") else "m.xml")
        if xml.exists():
            xml.rename(xml.with_name("baseline.xml"))
    if baselines[cmd] != "pass":
        return "BASELINE FAILED: " + baselines[cmd]
    f.write_text(src.replace(old, new))
    t = time.time()
    status = result(work, cmd)
    return f"{'SURVIVED' if status == 'pass' else status} ({time.time() - t:.0f} s)"


def select(patterns):
    """Mutant names for shell-style patterns; a pattern that matches none is an error."""
    names = []
    for pattern in patterns:
        hit = fnmatch.filter(MUTANTS, pattern)
        if not hit:
            sys.exit(f"no mutant matches {pattern!r}")
        names += hit
    return names


def main(names):
    scratch = tempfile.mkdtemp(prefix="protoemu-mutants-")
    bad = 0
    baselines = {}
    candidate, files = snapshot(scratch)
    for name in names or MUTANTS:
        r = run(name, scratch, baselines, candidate, files)
        bad += not r.startswith("killed")
        print(f"{name:32s} {r}", flush=True)
    print(f"{len(names or MUTANTS) - bad} of {len(names or MUTANTS)} mutants killed (scratch: {scratch})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(select(sys.argv[1:]))
