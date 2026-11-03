#!/usr/bin/env python3
"""Mutation check: each mutant is a one-line bug in the model, the RTL, a
firmware program or the assembler, paired with the test that must catch it.
Each runs in a scratch copy of the tracked files; the working tree is never
touched. Exit status 1 if any mutant survives.

    python3 test/mutants.py [name or pattern ...]      # from the repo root (needs the test env)
    python3 test/mutants.py 'hw-*'                     # the hardware-day launcher and fabric guard
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


HW_TESTS = pytest("test/model/test_hw_day.py test/model/test_fabric_guard.py")

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
    "hw-no-prereq-check": ("fpga/kv260/host/hw_day.py", '    if left:\n',
        '    if False:\n', HW_TESTS),
    "hw-direct-needs-only": ("fpga/kv260/host/hw_day.py", '    return [n for n in closure(step) if not fresh(state, n)]',
        '    return [n for n in step.needs if not fresh(state, n)]', HW_TESTS),
    "hw-never-expires": ("fpga/kv260/host/hw_day.py", '0 <= time.time() - t < MAX_AGE_S',
        't >= 0', HW_TESTS),
    "hw-future-time-counts": ("fpga/kv260/host/hw_day.py", '0 <= time.time() - t < MAX_AGE_S',
        'time.time() - t < MAX_AGE_S', HW_TESTS),
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
    "hw-off-always-refuses": ("fpga/kv260/host/hw_day.py", '        fabric_guard.check(listapps_fn or listapps, env={fabric_guard.OVERRIDE: told} if told else {})\n    except fabric_guard.NoActiveApp as e:\n        raise Refused("NOTHING',
        '        raise fabric_guard.NoActiveApp("x")\n    except fabric_guard.NoActiveApp as e:\n        raise Refused("NOTHING', HW_TESTS),
    "hw-off-false-reassurance": ("fpga/kv260/host/hw_day.py", '"NOTHING WAS WRITTEN: don\'t assume the pins are off. Pull the wires from J2. If the table "',
        '"every pin is off "', HW_TESTS),
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
    "hw-no-app-check": ("fpga/kv260/host/hw_day.py", 'app = check_app(step, listapps_fn, told)',
        'app = told or "protoemu40"', HW_TESTS),
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
    "hw-guard-state-ignored": ("fpga/kv260/host/fabric_guard.py", 'if s is not None and s != "operating":',
        'if False:', HW_TESTS),
    "hw-guard-firmware-ignored": ("fpga/kv260/host/fabric_guard.py", 'if f is not None and f != app + ".bit.bin":',
        'if False:', HW_TESTS),
    "hw-guard-other-app": ("fpga/kv260/host/fabric_guard.py", 'if not app.startswith(APP_PREFIX):',
        'if False:', HW_TESTS),
    "hw-guard-override-beats-table": ("fpga/kv260/host/fabric_guard.py", 'if app is None and understood:',
        'if False:', HW_TESTS),
    "hw-guard-disagreement-allowed": ("fpga/kv260/host/fabric_guard.py", 'if app is not None and told and told != app:',
        'if False:', HW_TESTS),
    "hw-guard-xmutil-status": ("fpga/kv260/host/fabric_guard.py", 'if r.returncode:',
        'if False:', HW_TESTS),
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


def main(patterns):
    names = select(patterns)
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
    main(sys.argv[1:])
