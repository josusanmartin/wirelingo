#!/usr/bin/env python3
"""One command per step of docs/hardware-day.md. Read that document first.

On the Kria (`hd` is the alias the document sets up):

    hd list                 the steps, in order, and which are confirmed
    hd show tx-pi           what a step expects; touches no hardware
    hd run tx-pi            prints the wiring, asks for a typed confirmation,
                            enables the step's pins, runs, turns them off
    hd off                  every PMOD pin disabled, now
    hd forget tx-pi         takes back a confirmation (the wiring changed)

On the Raspberry Pi (a plain copy of this file, nothing else needed):

    sudo python3 hw_day.py pi-watch --seconds 300   link state and tcpdump, to a log
    sudo python3 hw_day.py pi-send --seconds 60     frames for the chip: isolated cable only

What it enforces, and tests in test/model/test_hw_day.py check:

* A step runs only when the steps it needs were confirmed by a person.
* No pin is enabled before the exact typed phrase, and then only the pins the
  step names: anything else raises and turns every pin off.
* Every exit from a step (end, exception, SIGINT, SIGTERM, SIGHUP) turns every
  pin off. SIGKILL can't be caught: `hd off`.
* /dev/mem is not opened until fabric_guard.py shows a protoemu app active: reading
  the fabric's addresses with no app loaded can hang the board. On 2026-11-03 it
  probably did. A failed `xmutil loadapp` means stop: docs/hardware-day.md, step 0.
* One process: a lock, and a refusal if another board script is running. `hd off`
  takes no lock: it is the emergency stop, and writes only PMOD0 and PMOD1.
* A step needs its whole chain of prerequisites confirmed, each within MAX_AGE_S,
  except `recover`, the one-time recovery, which doesn't expire: a board that answers
  with an app active must never be sent back to a power cycle.
  Confirming usb-pi's wiring takes back usb-off: the USB wiring is on J2 again.

This file never reboots anything, never runs a command on the Kria's eth0,
never runs or imports pmod_test.py. pi-* subcommands refuse to run off a Pi.

The board libraries and fabric_guard are imported inside the actions: the Pi has none.
"""
import argparse
import binascii
import fcntl
import json
import os
import pathlib
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
STATE = "/var/tmp/hw_day_state.json"
LOCK = "/var/tmp/hw_day.lock"
I2C_IMAGE = "/var/tmp/hw_day_i2c.json"

APPS = {"protoemu": 24, "protoemu40": 30, "protoemu48": 25}      # app -> clk_div
ANY_APP = tuple(APPS)
OTHER_BOARD_SCRIPTS = ("bringup_kria.py", "pmod_test.py", "spim_test.py", "usb_demo.py", "usb_stress.py",
                       "usb_fs_data_demo.py", "usb_replay_demo.py", "v2_demo.py", "legacy_demo.py",
                       "postfreeze_demo.py", "postfreeze2_demo.py", "postfreeze3_demo.py",
                       "spi_uart_demo.py", "eth_tap.py")

MAX_AGE_S = 12 * 3600             # a confirmation is of wiring on a bench: it doesn't outlast the day
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2

# TT pin numbers in pe_spim.Spim.pmod(): 0-7 uio, 8-14 uo0-6, 16-20 ui0-4
UIO0, UIO1, UIO2, UO0, UO1, UI0 = 0, 1, 2, 8, 9, 16
# pmod[] index -> the J2 silkscreen number (fpga/kv260/README.md, "columns normal")
SILK = {0: 1, 1: 3, 2: 5, 3: 7, 4: 2, 5: 4, 6: 6, 7: 8}
READ_PIN = 3                      # pmod[3] = silkscreen 7: the steps' read-back pad

CHIP_IP, PI_IP = "10.77.0.2", "10.77.0.1"
CHIP_MAC = "02:57:4c:00:00:02"
SPI_BUDGET_US = 18.0              # docs/eth10-network.md's go/no-go: an estimate built on a tested 15.5 us
F_GO = 0                          # eth10_tx's flag
LINECFG_ETH = 0x4005              # EN | CODE Manchester | RX_EN
ETHERTYPE = 0x88B5                # local experimental, as eth10_match expects
FIELD = 2000                      # the field pi-send puts in its frames
MATCH_LAST_BIT = 64 + 8 * 16 - 1  # eth10_cut: the field's last bit, counted from the first preamble bit


def root():
    """The checkout, on the Kria. Only there: the Pi's copy may live at /home/pi/hw_day.py."""
    return HERE.parents[2]


class Refused(Exception):
    """A safety rule stopped the step. Nothing was enabled by this refusal."""


class Stopped(Exception):
    """A signal ended the step, which then turned the pins off."""


@dataclass
class Step:
    name: str
    title: str
    needs: tuple = ()
    wires: tuple = ()                 # (from, to), one wire or part per row, in the order to place them
    remove: tuple = ()                # what must be taken out first
    off: tuple = ()                   # what must stay disconnected
    pins: dict = field(default_factory=dict)   # pmod index -> set of TT pins this step may enable
    apps: tuple = ()                  # apps that may be loaded; () means this step has no board action
    action: str = ""                  # act_<action>
    phrase: str = ""                  # default "wired <name>"; only asked when pins is not empty
    files: tuple = ()                 # paths under root() the step needs: on eth-stack, not in this tree
    clears: tuple = ()                # confirmations this step's wiring takes back
    once: bool = False                # confirmed once for good: it doesn't expire
    pi: tuple = ()                    # commands for the Pi, printed
    kria: tuple = ()                  # read-only commands for the Kria, printed; none of them maps anything
    ok: str = ""
    fail: str = ""

    def typed(self):
        return self.phrase or f"wired {self.name}"


ETH_STACK = ("tools/pe/ethhost.py", "tools/pe/eth.py", "tools/pe/ethsim.py", "firmware/eth10_tx_cs.pasm",
             "fpga/kv260/host/eth_tap.py")
NO_POE = ("Every other switch, router or wall network socket: this wiring may only ever touch the "
          "Tenda SG105 or the Pi. Any of them could carry 48 V PoE",)
NO_RAILS = ("No wire between the Kria's 3.3 V (J2 silkscreen 11, 12) and anything on the Pi",
            "Pi pins 2 and 4 (5 V): nothing on them, ever",)
TX_WIRES = (("J2 silkscreen 1 (TD+)", "a2"), ("220 ohm", "b2 to b6"), ("100 nF", "c6 to c8"),
            ("J2 silkscreen 3 (TD-)", "a12"), ("220 ohm", "b12 to b16"), ("100 nF", "c16 to c18"))
RX_WIRES = TX_WIRES + (("RJ45 pin 3", "a8"), ("RJ45 pin 6", "a18"),
                       ("J2 silkscreen 9 (GND)", "a29"), ("220 ohm", "d25 to d29"), ("220 ohm", "e25 to e29"),
                       ("1 kohm", "c21 to c25"), ("1 kohm", "b21 to b29"), ("1 kohm", "d17 to d21"),
                       ("J2 silkscreen 11 (3.3 V)", "a17"), ("J2 silkscreen 7 (RX in)", "a21"),
                       ("RJ45 pin 2", "c29"), ("RJ45 pin 1", "a25"),
                       ("Patch cable: breakout to Pi eth0, last", "not to the switch"))
RX_OFF = ("The switch and every other network port: the 220 ohm termination is sized for the Pi's pair",
          "RJ45 pins 4, 5, 7, 8", "The USB plug, in any port",
          "Any GND wire to the Pi: RJ45 pin 2 ties the Pi's isolated pair to the Kria's GND here")
PI_GROUND = (("J2 silkscreen 9 (GND)", "a24"), ("Pi pin 6 (GND), first of the Pi wires", "b24"))

STEPS = [
    Step("recover", "Bring the board back; find out why loadapp failed", once=True,
         kria=("uptime", "sudo xmutil listapps", "cat /sys/class/fpga_manager/fpga0/state",
               "cat /proc/device-tree/fpga-region/firmware-name; echo",
               "sudo dmesg | grep -iE 'fpga|dfx|overlay|zynqmp' | tail -n 60",
               "sudo journalctl -u dfx-mgr -b --no-pager | tail -n 60",
               "sudo journalctl -u dfx-mgr -b -1 --no-pager | tail -n 60",
               "ls -l /lib/firmware/xilinx/protoemu/", "ls -ld ~/protoemu ~/protoemu40 ~/protoemu48"),
         off=("Every wire in J2: out, before anything else",),
         ok="the Kria answers, the cause of the failed load is written down, and a clean load shows protoemu active",
         fail="no answer, or a load fails again: stop. No tool runs on a board with no active app"),
    Step("preflight", "Read the board, change nothing", needs=("recover",), apps=ANY_APP, action="preflight",
         off=("Nothing changes in this step: leave the wiring as it is",),
         ok="active app named, chip ID read, PMOD0 and PMOD1 both 00000000",
         fail="no protoemu app active: run the xmutil commands it prints, one at a time. PMOD not zero: `hd off`, run again"),
    Step("usb-off", "Take the USB wiring off the Kria", needs=("preflight",),
         remove=("USB plug out first, of whichever port holds it, the Kria's or the Pi's (and leave it out)",
                 "Jumper J2 silkscreen 1 to a30: remove both ends", "Jumper J2 silkscreen 3 to a20: remove both ends",
                 "Jumper J2 silkscreen 11 to i3: remove both ends",
                 "After usb-pi: jumper J2 silkscreen 9 to b24, both ends, and the black wire out of a24 and taped"),
         off=("J2 empty: not one wire in it", "USB plug in no port; red VBUS and black GND still taped"),
         ok="J2 is empty and the USB plug is lying loose; the breadboard's parts stay where they are",
         fail="`hd off`, then finish removing the wires"),
    Step("rails", "Find GND and 3.3 V on J2 without a meter", needs=("usb-off",), apps=ANY_APP, action="rails",
         wires=(("J2 silkscreen 7", "a24"), ("220 ohm", "b24 to b28"),
                ("test jumper, one end", "c28"), ("test jumper, other end", "J2 silkscreen 11, moved to 9 and back when asked")),
         off=("J2: only those two wires", "The Pi", "Every network cable"),
         ok="1.00, 0.00, 1.00: silkscreen 11 pulls the pad high and silkscreen 9 pulls it low",
         fail="any other reading: stop, no Pi ground wire goes on J2 until it is understood"),
    Step("pi-net", "Give the Pi a network; the Kria's cable stays", needs=("preflight",),
         wires=(("Tenda SG105 power", "its own adapter"),
                ("A free router port, only if one exists", "Tenda SG105 port 1"), ("Pi eth0", "Tenda SG105 port 2")),
         off=("The Kria's network cable, both ends: it stays where it is, all day",
              "The Kria and the switch: nothing joins them", "J2: no chip wiring near the switch yet") + NO_POE,
         ok="the Pi reaches your network, over the switch or its own Wi-Fi; or you are at it with a keyboard and screen",
         fail="no free router port: use the Pi's Wi-Fi, or a keyboard and screen. Never unplug anything to make room"),
    Step("pi-prep", "Prepare the Pi while it has a network", needs=("pi-net",),
         pi=("cat /proc/device-tree/model; echo", "pinout",
             "sudo apt-get update && sudo apt-get install -y tcpdump i2c-tools python3-spidev python3-gpiozero",
             "sudo raspi-config nonint do_spi 0",
             "sudo raspi-config nonint do_serial_hw 0; sudo raspi-config nonint do_serial_cons 1",
             "echo 'dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=2,i2c_gpio_scl=3,i2c_gpio_delay_us=100' "
             "| sudo tee -a /boot/firmware/config.txt",
             "restart the Pi (the Pi, not the Kria), and copy hw_day.py to it from your checkout with scp"),
         off=("J2: nothing to the Pi yet",),
         ok="the model line names a Raspberry Pi 3, `pinout` matches the document's Pi pin table, tcpdump --version works",
         fail="no raspi-config: it is not Raspberry Pi OS; the commands in the document's Pi section need the local equivalent"),
    Step("budget", "Is Python fast enough for ping?", needs=("preflight",), apps=ANY_APP, action="budget",
         files=ETH_STACK, off=("J2: empty or anything: no pin is enabled",),
         ok=f"it prints us per pass, at most {SPI_BUDGET_US:.0f} (estimate): ping can be tried",
         fail="over that: Python can't stream a frame; skip `ping`, everything else still runs"),
    Step("tx-bench", "Check the transmit wiring, no cable yet", needs=("rails",), apps=("protoemu40",), action="tx_bench",
         pins={0: {UO0}, 1: {UO1}},
         wires=TX_WIRES + (("test jumper, one end", "J2 silkscreen 7"), ("test jumper, other end", "e6, moved to e16 when asked")),
         off=("RJ45 breakout: no wire from it on the breadboard", "The Pi and the switch"),
         ok="column 6 follows TD+ only, column 16 follows TD- only",
         fail="fix the wire it names; the launcher has already turned the pins off"),
    Step("tx-pi", "Transmit to the Pi on an isolated cable", needs=("tx-bench", "pi-prep"), apps=("protoemu40",),
         action="tx", pins={0: {UO0}, 1: {UO1}},
         wires=TX_WIRES + (("RJ45 pin 3", "a8"), ("RJ45 pin 6", "a18"),
                           ("Patch cable: breakout to Pi eth0, last", "not to the switch")),
         off=("RJ45 pins 1, 2, 4, 5, 7, 8", "J2 silkscreen 5, 7, 9, 11", "Any GND wire between the boards: "
              "the Pi's Ethernet port is transformer-isolated and needs none") + NO_POE,
         pi=("sudo nohup python3 hw_day.py pi-watch --seconds 600 > watch.out 2>&1 &",),
         ok="watch.log: carrier 1, speed 10, half duplex, then tcpdump shows ethertype 0x88b5 frames, FCS good",
         fail="carrier stays 0: add the second 220 ohm on each line (d2 to d6, d12 to d16), then swap RJ45 pins 3 and 6"),
    Step("tx-switch", "Transmit into the switch, seen by tcpdump", needs=("tx-pi",), apps=("protoemu40",),
         action="tx", pins={0: {UO0}, 1: {UO1}}, phrase="not poe wired tx-switch",
         remove=("Any cable from a router in Tenda port 1: this test wants no network on the switch",),
         wires=TX_WIRES + (("RJ45 pin 1", "a8"), ("RJ45 pin 2", "a18"),
                           ("Pi eth0", "Tenda SG105 port 2"),
                           ("Patch cable: breakout to Tenda SG105 port 4, last", "the Tenda, no other"),),
         off=("RJ45 pins 3, 4, 5, 6, 7, 8", "J2 silkscreen 5, 7, 9, 11") + NO_POE,
         pi=("sudo nohup python3 hw_day.py pi-watch --seconds 600 --filter > watch.out 2>&1 &",),
         ok="the Pi, on the switch, sees broadcast frames from 02:57:4c:00:00:02 with ethertype 0x88b5",
         fail="port LED dark: the switch's receiver didn't take the link. No frames but LED on: a bad FCS is dropped silently here"),
    Step("uart", "UART both ways with the Pi", needs=("rails", "pi-prep"), apps=ANY_APP, action="uart",
         pins={0: {UO0}, 3: {UI0}},
         wires=PI_GROUND + (("J2 silkscreen 1 (chip TX)", "a2"), ("220 ohm", "b2 to b6"), ("Pi pin 10 (GPIO15, RXD)", "a6"),
                            ("J2 silkscreen 7 (chip RX)", "a12"), ("220 ohm", "b12 to b16"), ("Pi pin 8 (GPIO14, TXD)", "a16")),
         off=NO_RAILS + ("Every Ethernet wire and the RJ45 breakout", "The USB plug"),
         pi=("stty -F /dev/serial0 115200 raw -echo; timeout 25 cat /dev/serial0 & for i in 1 2 3 4 5; do "
             "printf 'HELLO FROM PI\\n' > /dev/serial0; sleep 2; done",),
         ok="the Pi prints lines WLGO 000, WLGO 001, ... and the launcher prints HELLO FROM PI",
         fail="one way only: swap nothing yet, check that wire's column. Neither way: the ground wire, or /dev/serial0"),
    Step("spi", "Capture the Pi's SPI master", needs=("uart",), apps=ANY_APP, action="spi",
         pins={0: {UIO0}, 1: {UIO1}, 2: {UIO2}},
         wires=PI_GROUND + (("J2 silkscreen 1 (SCK in)", "a2"), ("220 ohm", "b2 to b6"), ("Pi pin 23 (GPIO11, SCLK)", "a6"),
                            ("J2 silkscreen 3 (MOSI in)", "a12"), ("220 ohm", "b12 to b16"), ("Pi pin 19 (GPIO10, MOSI)", "a16"),
                            ("J2 silkscreen 5 (CE0 in)", "a22"), ("220 ohm", "b22 to b26"), ("Pi pin 24 (GPIO8, CE0)", "a26")),
         off=NO_RAILS + ("Pi pin 21 (MISO): nothing drives it here", "Every Ethernet wire and the USB plug"),
         pi=("python3 -c \"import spidev,time; s=spidev.SpiDev(0,0); s.max_speed_hz=20000; s.mode=0; "
             "[(s.xfer2(list(b'WIRELINGO')), time.sleep(0.5)) for _ in range(20)]\"",),
         ok="the launcher prints the bytes WIRELINGO, one CS frame each, with no gap",
         fail="capture has gaps: Python fell behind; lower max_speed_hz to 8000. No edges: the ground wire, or SPI not enabled"),
    Step("i2c-capture", "Capture a real I2C part", needs=("rails", "pi-prep"), apps=ANY_APP, action="i2c_capture",
         pins={0: {UIO0}, 1: {UIO1}},
         wires=PI_GROUND + (("J2 silkscreen 1 (SDA)", "a2"), ("220 ohm", "b2 to b6"), ("Pi pin 3 (GPIO2, SDA)", "a6"), ("Part SDA", "c6"),
                            ("J2 silkscreen 3 (SCL)", "a12"), ("220 ohm", "b12 to b16"), ("Pi pin 5 (GPIO3, SCL)", "a16"), ("Part SCL", "c16"),
                            ("Part GND", "c24"), ("Pi pin 1 (3.3 V)", "a28"), ("Part VCC", "b28")),
         off=NO_RAILS[1:] + ("J2 silkscreen 11, 12: the part runs from the Pi's 3.3 V alone",
                             "Any added pull-up: the Pi's fixed 1.8 kohm are on these two lines"),
         pi=("i2cdetect -y 3", "i2ctransfer -y 3 w1@0x50 0x10 r8"),
         ok="a transcript with the part's address and the 8 bytes the Pi printed; register image saved",
         fail="i2cdetect shows nothing: part wiring or its address. Gaps: slow the bus further (i2c_gpio_delay_us)"),
    Step("i2c-clone", "Impersonate the part", needs=("i2c-capture",), apps=ANY_APP, action="i2c_clone",
         pins={0: {UIO0}, 1: {UIO1}},
         remove=("The I2C part, all four wires: an unpowered part can load the bus",),
         wires=PI_GROUND + (("J2 silkscreen 1 (SDA)", "a2"), ("220 ohm", "b2 to b6"), ("Pi pin 3 (GPIO2, SDA)", "a6"),
                            ("J2 silkscreen 3 (SCL)", "a12"), ("220 ohm", "b12 to b16"), ("Pi pin 5 (GPIO3, SCL)", "a16")),
         off=NO_RAILS + ("The I2C part: on the bench, wired to nothing",),
         pi=("i2cdetect -y 3", "i2ctransfer -y 3 w1@0x50 0x10 r8"),
         ok="the Pi reads the same 8 bytes with the part on the bench",
         fail="i2cdetect shows the address but reads differ: the part may use 2-byte addresses, which the clone can't"),
    Step("i2c-mitm", "Change a register on its way to the Pi", needs=("i2c-clone",), apps=ANY_APP, action="i2c_mitm",
         pins={0: {UIO0}, 1: {UIO1}},
         wires=PI_GROUND + (("J2 silkscreen 1 (SDA)", "a2"), ("220 ohm", "b2 to b6"), ("Pi pin 3 (GPIO2, SDA)", "a6"),
                            ("J2 silkscreen 3 (SCL)", "a12"), ("220 ohm", "b12 to b16"), ("Pi pin 5 (GPIO3, SCL)", "a16")),
         off=NO_RAILS + ("The I2C part: on the bench, wired to nothing",),
         pi=("i2ctransfer -y 3 w1@0x50 0x10 r8",),
         ok="the Pi's bytes equal the capture's except the one register the launcher names, inverted",
         fail="as i2c-clone"),
    Step("usb-pi", "USB with the Pi as a second host", needs=("rails", "pi-prep"), apps=("protoemu48",), action="usb_pi",
         pins={0: {UO0, UIO1}, 1: {UO0, UIO0}, 2: {UO0}, 3: {UO0}}, phrase="vbus taped wired usb-pi",
         clears=("usb-off",),
         wires=(("USB cable black wire, untaped (GND)", "a24"), ("J2 silkscreen 9 (GND)", "b24"),
                ("J2 silkscreen 1 (D+)", "a30"), ("J2 silkscreen 3 (D-)", "a20"), ("J2 silkscreen 11 (3.3 V)", "i3"),
                ("USB plug, last, Pi powered off", "any USB-A port on the Pi")),
         off=("USB red wire (VBUS, 5 V): taped, connected to nothing", "The Kria's USB ports: nothing in them",
              "J2 silkscreen 5 and 7: empty, they get a low pulse in the wiring check", "Any other wire from the Pi"),
         pi=("lsusb -d 1209:5634", "dmesg | tail -n 20"),
         ok="the Pi's lsusb lists 1209:5634 and its dmesg says new full-speed USB device",
         fail="no pull-up found: the jumper f11-f22 or the 1 kohm legs. device descriptor read/64 errors: D+/D-, or GND"),
    Step("rx", "Receive with resistors alone", needs=("tx-pi",), apps=("protoemu40", "protoemu"), action="rx",
         pins={0: {UO0}, 1: {UO1}, 3: {UI0}}, phrase="pin risk accepted wired rx",
         remove=("USB plug out of the Pi first, before anything else here", "USB cable's black wire out of a24, and taped",
                 "Jumper J2 silkscreen 1 to a30: remove both ends", "Jumper J2 silkscreen 3 to a20: remove both ends",
                 "Jumper J2 silkscreen 11 to i3: remove both ends", "Jumper J2 silkscreen 9 to b24: remove both ends",
                 "Every UART, SPI and I2C wire and part",
                 "The three 1 kohm from the USB wiring (j3-j7, i7-i11, g7-g11): keep them; photograph first"),
         wires=RX_WIRES, off=RX_OFF + NO_POE,
         pi=("sudo python3 hw_day.py pi-send --seconds 120 --gap-us 1000",),
         ok="good frames counted, FCS checked, at least 10",
         fail="idle reads high, or all short records: stop. This attempt is allowed to fail; docs/eth10-receiver.md's front end is the real answer"),
    Step("cut", "Mid-frame reaction on live traffic", needs=("rx",), apps=("protoemu", "protoemu40"), action="cut",
         pins={0: {UO0}, 1: {UO1}, 3: {UIO0}}, phrase="pin risk accepted wired cut",
         wires=RX_WIRES, off=RX_OFF + NO_POE,
         pi=("sudo python3 hw_day.py pi-send --seconds 120 --gap-us 200",),
         ok="threshold 1000: HIT on whole frames, reaction near 10 clocks; threshold 3000: no HIT",
         fail="no HIT at threshold 1000, with rx passing: note it; the FPGA demo with the generator remains the evidence"),
    Step("ping", "Real ping", needs=("rx", "budget"), apps=("protoemu40", "protoemu"), action="ping",
         pins={0: {UO0}, 1: {UO1}, 3: {UI0}}, phrase="pin risk accepted wired ping", files=ETH_STACK,
         wires=RX_WIRES, off=RX_OFF + NO_POE,
         pi=(f"sudo ip addr add {PI_IP}/24 dev eth0", f"ping -c 5 -W 2 {CHIP_IP}", f"sudo ip addr del {PI_IP}/24 dev eth0"),
         ok="ping replies; the launcher counts ARP and echo answers",
         fail="ARP unanswered: see its counters. Overflow counted: Python too slow, which `budget` warned of"),
]
BY_NAME = {s.name: s for s in STEPS}


# ---------------------------------------------------------------- state

def load_state(path):
    try:
        state = json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        state = None
    if not isinstance(state, dict):
        raise Refused(f"{path} is not a confirmations file. Delete it, and confirm the steps again")
    return state


def save_state(path, state):
    p = pathlib.Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(p)


def closure(step):
    """Every step this one depends on, directly or not, in the day's order."""
    seen, todo = set(), list(step.needs)
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += BY_NAME[n].needs
    return [s.name for s in STEPS if s.name in seen]


def fresh(state, name):
    """Confirmed, within MAX_AGE_S. /var/tmp outlives the day; the wiring may not have.
    A `once` step stays confirmed: it is a recovery, not wiring."""
    t = state.get(name)
    if not isinstance(t, (int, float)) or isinstance(t, bool) or t != t:
        return False
    age = time.time() - t
    if name in BY_NAME and BY_NAME[name].once:
        return age >= 0
    return 0 <= age < MAX_AGE_S


def missing_prereqs(step, state):
    return [n for n in closure(step) if not fresh(state, n)]


# ---------------------------------------------------------------- printing

def describe(step, out=print):
    out(f"== {step.name}: {step.title}")
    if step.needs:
        out("needs: " + ", ".join(step.needs))
    if step.apps:
        out("app: " + " or ".join(step.apps))
    if step.remove:
        out("REMOVE FIRST:")
        for r in step.remove:
            out(f"  {r}")
    if step.wires:
        out("WIRING (in this order; rows a-e are the breadboard's lower half):")
        for a, b in step.wires:
            out(f"  {a}  ->  {b}")
    if step.off:
        out("MUST STAY DISCONNECTED:")
        for x in step.off:
            out(f"  {x}")
    if step.pins:
        out("PINS THIS STEP ENABLES: " + ", ".join(f"silkscreen {SILK[p]}" for p in sorted(step.pins)))
    if step.kria:
        out("ON THE KRIA, READ-ONLY:")
        for c in step.kria:
            out(f"  {c}")
    if step.pi:
        out("ON THE PI:")
        for c in step.pi:
            out(f"  {c}")
    out(f"success: {step.ok}")
    out(f"failure: {step.fail}")


# ---------------------------------------------------------------- guards

class PinGuard:
    """Stands in for pe_spim.Spim: pmod() takes only what the step named."""

    def __init__(self, spim, allowed):
        self._spim, self._allowed = spim, allowed

    def pmod(self, mapping):
        for pin, tt in mapping.items():
            if tt is not None and tt not in self._allowed.get(pin, ()):
                self._spim.pmod({})
                raise Refused(f"pmod[{pin}] = TT pin {tt} is not in this step's wiring: every pin turned off")
        self._spim.pmod(mapping)

    def __getattr__(self, name):
        return getattr(self._spim, name)


def listapps():
    import fabric_guard
    return fabric_guard.listapps()


def load_line(app):
    return ("The load, one command at a time, reading what each prints:\n"
            f"  sudo xmutil unloadapp\n  sudo xmutil loadapp {app}\n  sudo xmutil listapps\n"
            f"The table must show {app} as its one active row. Any error line, `Load Error` in particular: stop, "
            "run nothing, go to step 0 of docs/hardware-day.md")


def check_app(step, read=None):
    """Before anything opens /dev/mem. Returns the app's name, or raises Refused."""
    import fabric_guard
    try:
        app = fabric_guard.check(read or listapps)
    except fabric_guard.NoActiveApp as e:
        raise Refused(str(e))
    if app not in APPS:
        raise Refused(f"active app is {app!r}, not one this runbook knows. Nothing was opened. {load_line(step.apps[0])}")
    if app not in step.apps:
        raise Refused(f"step {step.name} needs {' or '.join(step.apps)}, and {app} is active. {load_line(step.apps[0])}")
    return app


def other_board_scripts(proc="/proc"):
    found = []
    for p in pathlib.Path(proc).iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        found += [f"{p.name}: {s}" for s in OTHER_BOARD_SCRIPTS if s in cmd]
    return found


class Lock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.f = open(self.path, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise Refused("another hw_day.py is running: one process on the Kria at a time")
        return self

    def __exit__(self, *a):
        self.f.close()


class Signals:
    """SIGINT, SIGTERM and SIGHUP (a dropped ssh) raise Stopped once; later ones are
    ignored, so one can't cut the pins' turning off short."""
    NAMES = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

    def __enter__(self):
        self.live = True

        def stop(signum, frame):
            if self.live:
                self.live = False
                raise Stopped(f"signal {signum}")
        self.old = [signal.signal(n, stop) for n in self.NAMES]
        return self

    def __exit__(self, *a):
        self.live = False
        for n, h in zip(self.NAMES, self.old):
            signal.signal(n, h)


# ---------------------------------------------------------------- what a step does

class Ctx:
    def __init__(self, step, rig, ask, say, app=None):
        self.step, self.rig, self.ask, self.say, self.app = step, rig, ask, say, app
        self.spim = PinGuard(rig.spim, step.pins)


def t():
    """The board libraries, on the Kria only."""
    from types import SimpleNamespace
    sys.path.insert(0, str(HERE))
    import postfreeze_demo as pfd
    from postfreeze2_demo import rd
    from pe import asm, host, sim
    return SimpleNamespace(pfd=pfd, rd=rd, asm=asm, host=host, sim=sim, FIFO=pfd.FIFO, STATUS=pfd.STATUS)


def ctrl(rig, sim, mask):
    rig.write(sim.A_CTRL, mask | (mask << 8))
    if getattr(rig, "model", False):
        rig.wait(1e-6)


def nap(rig, s):
    if getattr(rig, "model", False):
        rig.wait(s)
    else:
        time.sleep(s)


def read_pad(ctx, n=20):
    """The share of n reads of the read-back pad that were high."""
    return sum((ctx.spim.pmod_in() >> READ_PIN) & 1 for _ in range(n)) / n


def need(cond, msg):
    if not cond:
        raise RuntimeError(msg)


def act_preflight(ctx):
    sys.path.insert(0, str(HERE))
    from pe_spim import PMOD0, PMOD1
    import fabric_guard
    apps = [name for name, _ in fabric_guard.rows(listapps())]      # rows, not text: protoemu is in protoemu40
    absent = [a for a in APPS if a not in apps]
    ctx.say(f"installed apps checked: {'all three' if not absent else 'MISSING ' + ', '.join(absent)}")
    i = ctx.rig.ident
    p0, p1 = ctx.spim.mem.rd(PMOD0), ctx.spim.mem.rd(PMOD1)
    ctx.say(f"RTL revision {i['rtl_rev'][:12]}, clk_div {i['clk_div']}, built {i['build_utc']} UTC, "
            f"clock {ctx.rig.f_meas / 1e6:.4f} MHz")
    ctx.say(f"PMOD0 {p0:08x} PMOD1 {p1:08x} PMODIN {ctx.spim.pmod_in():02x}")
    need(p0 == 0 and p1 == 0, "a PMOD pin is enabled: `hd off`, and find out who left it on")
    need(not absent, f"{absent} not installed: the steps that need them will refuse")


def act_rails(ctx):
    """An undriven pad holds its level, so each pin must move it from the other's."""
    reads = []
    for pin in (11, 9, 11):
        ctx.ask(f"Test jumper's free end in J2 silkscreen {pin}, then press Enter")
        reads.append(read_pad(ctx))
    ctx.say(f"silkscreen 11, 9, 11: read high {reads[0]:.2f}, {reads[1]:.2f}, {reads[2]:.2f} of 20 reads")
    need(reads == [1, 0, 1], "not 1, 0, 1: no GND wire goes on J2 until this is understood")


def act_budget(ctx):
    sys.path.insert(0, str(HERE))
    import eth_tap
    us = eth_tap.measure_spim_session(ctx.rig.spim)
    ctx.say(f"{us:.1f} us per pass; the go/no-go is about {SPI_BUDGET_US:.0f} us (an estimate, docs/eth10-network.md)")
    need(us <= SPI_BUDGET_US, "over budget: skip `ping`")


def act_tx_bench(ctx):
    rig, k = ctx.rig, t()
    k.pfd.setup(rig)
    ctx.spim.pmod({0: UO0, 1: UO1})
    for column, mine, other in (("e6", UO0, UO1), ("e16", UO1, UO0)):
        ctx.ask(f"Test jumper's free end in {column}, then press Enter")
        seen = []
        for level in (0, 1, 0, 1, 0, 1):
            rig.write(k.sim.A_POUT, level << mine)
            time.sleep(0.001)
            seen.append(read_pad(ctx, 4))
        rig.write(k.sim.A_POUT, 1 << other)
        time.sleep(0.001)
        cross = read_pad(ctx, 4)
        rig.write(k.sim.A_POUT, 0)
        ctx.say(f"{column}: follows {seen}, with the other line high instead reads {cross}")
        need(seen == [0, 1, 0, 1, 0, 1] and cross == 0, f"{column} is not wired as the step says")


def tx_divider(f_clk):
    return {40e6: 1 << 8, 50e6: 320}[f_clk]


def line_period(f_clk):
    return {40e6: 4, 50e6: 5}[f_clk]


def tx_frame(n):
    """A 64-byte broadcast, ethertype ETHERTYPE, FCS included; the frame the Pi's tcpdump shows."""
    body = b"\xff" * 6 + bytes.fromhex(CHIP_MAC.replace(":", "")) + struct.pack(">H", ETHERTYPE)
    body += (b"WIRELINGO HW DAY %04d " % n).ljust(46, b".")
    return body + struct.pack("<I", binascii.crc32(body))


def tx_load(rig, k, core):
    k.pfd.load(rig, core, k.pfd.fw("eth10_tx", F_GO=F_GO), side_pins=UO0, clkdiv=tx_divider(rig.f_clk))


def tx_send(rig, k, core, frame):
    """Up to 64 bytes: the two-stage TX FIFO holds them, so Python's pace doesn't matter."""
    need(len(frame) <= 64, "tx_send: a longer frame would need streaming")
    b = frame + bytes(-len(frame) & 3)
    w = [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]
    rig.write(k.sim.core_reg(core, k.sim.C_R0), 8 * len(frame) - 1)
    rig.write(k.FIFO(core), w[:8], fixed=True)
    nap(rig, 1e-5)
    rig.write(k.FIFO(core), w[8:], fixed=True)
    rig.write(k.sim.A_FSET, 1 << F_GO)


def tx_wait(rig, k, polls=500):
    for _ in range(polls):
        if not rig.read(k.sim.A_FSET) & (1 << F_GO):
            return
        nap(rig, 1e-4)
    raise RuntimeError("eth10_tx didn't finish the frame")


def act_tx(ctx, seconds=120, frames=600):
    """Link pulses from the core all the time, a broadcast every 0.2 s (5 frames/s: 600 frames)."""
    rig, k = ctx.rig, t()
    k.pfd.setup(rig, owners={UO0: 0, UO1: 0})
    tx_load(rig, k, 0)
    ctx.spim.pmod({0: UO0, 1: UO1})
    ctrl(rig, k.sim, 0b01)
    ctx.say(f"link pulses on; first frame in 3 s, then {frames} over {seconds} s. Ctrl-C ends it.")
    time.sleep(3)
    for n in range(frames):
        tx_send(rig, k, 0, tx_frame(n))
        tx_wait(rig, k)
        time.sleep(seconds / frames)
    ctx.say(f"{frames} frames sent")


def act_uart(ctx, seconds=25):
    rig, k = ctx.rig, t()
    div = k.host.clkdiv_for(rig.f_clk, 8 * 115200)
    k.pfd.setup(rig, owners={UO0: 0}, pout=1 << UO0)
    k.pfd.load(rig, 1, k.pfd.fw("uart_rx", RX=UI0), clkdiv=div, in_pins=UI0)
    k.pfd.load(rig, 0, k.pfd.fw("uart_tx"), clkdiv=div, out_pins=(UO0, 1), side_pins=UO0)
    ctx.spim.pmod({0: UO0, 3: UI0})
    ctrl(rig, k.sim, 0b11)
    got, end, n = b"", time.monotonic() + seconds, 0
    while time.monotonic() < end:
        rig.write(k.FIFO(0), list(b"WLGO %03d" % (n % 1000)), fixed=True)   # 8 bytes: the FIFO's depth
        n += 1
        time.sleep(0.05)
        rig.write(k.FIFO(0), 10, fixed=True)
        time.sleep(0.05)
        w = k.rd(rig, k.FIFO(1), (rig.read(k.STATUS(1)) >> 4) & 15)
        got += bytes(x & 0xFF for x in w)
    ctx.say(f"sent {n} lines; received from the Pi: {got!r}")
    need(b"HELLO FROM PI" in got, "nothing right from the Pi")


def drain(rig, k, core, seconds, each=None):
    """Tight loop on STATUS, then that many words. la_capture records are never 0."""
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        out += k.rd(rig, k.FIFO(core), (rig.read(k.STATUS(core)) >> 4) & 15)
        if each:
            each()
    return out


def la_load(rig, k, mask):
    prog = k.asm.assemble((root() / "firmware" / "la_capture.pasm").read_text(), {"MASK": mask})
    k.pfd.load(rig, 0, prog, in_pins=0, clkdiv=1 << 8)


def spi_decode(edges, sck=0, mosi=1, cs=2):
    """Bytes per CS frame, SPI mode 0, MSB first, from (tick, state) edges."""
    frames, bits, psck, pcs = [], [], None, None
    for _, s in edges:
        c, k, m = (s >> cs) & 1, (s >> sck) & 1, (s >> mosi) & 1
        if pcs == 1 and c == 0:
            bits = []
        if c == 0 and psck == 0 and k == 1:
            bits.append(m)
        if pcs == 0 and c == 1:
            frames.append(bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - 7, 8)))
        psck, pcs = k, c
    return frames


def act_spi(ctx, seconds=15):
    rig, k = ctx.rig, t()
    from pe import la
    k.pfd.setup(rig)
    la_load(rig, k, 0b111)
    ctx.spim.pmod({0: UIO0, 1: UIO1, 2: UIO2})
    ctrl(rig, k.sim, 0b01)
    ctx.say(f"capturing for {seconds} s: run the Pi command now")
    cap = la.decode_records(drain(rig, k, 0, seconds), 0b111)
    frames = spi_decode(cap.edges)
    ctx.say(f"{len(cap.edges)} edges, {len(cap.gaps)} gaps; CS frames: {[f.decode(errors='replace') for f in frames[:5]]}")
    need(frames and cap.exact, "no CS frame, or a gap in the capture")


def act_i2c_capture(ctx, seconds=30):
    rig, k = ctx.rig, t()
    from pe import la
    k.pfd.setup(rig)
    la_load(rig, k, 0b11)
    ctx.spim.pmod({0: UIO0, 1: UIO1})
    ctrl(rig, k.sim, 0b01)
    ctx.say(f"capturing for {seconds} s: run the Pi commands now")
    cap = la.decode_records(drain(rig, k, 0, seconds), 0b11)
    ev = la.i2c_events(cap, 0, 1)
    for line in la.i2c_summary(ev):
        ctx.say(f"  {line}")
    image = la.i2c_register_image(ev)
    ctx.say(f"{len(cap.edges)} edges, {len(cap.gaps)} gaps; registers seen: "
            f"{ {hex(a): len(m) for a, m in image.items()} }")
    need(image and cap.exact, "no register image, or a gap in the capture")
    pathlib.Path(I2C_IMAGE).write_text(json.dumps({str(a): {str(r): v for r, v in m.items()} for a, m in image.items()}))


def clone(ctx, seconds, on_read=None, image=None):
    """i2c_device on core 1 at the captured address, la_capture still on core 0."""
    rig, k = ctx.rig, t()
    from pe import i2cdev, la
    image = image or json.loads(pathlib.Path(I2C_IMAGE).read_text())
    addr = next(a for a, m in image.items() if m)
    mem = {int(r): v for r, v in image[addr].items()}
    dev = i2cdev.Device(int(addr), mem=mem, on_read=on_read and on_read(mem))
    k.pfd.setup(rig, owners={UIO0: 1, UIO1: 1}, od=0b11, pout=0b11)     # released, before any pin is mapped
    la_load(rig, k, 0b11)
    prog = k.pfd.fw("i2c_device", SDA=UIO0, SCL=UIO1)
    for a, w in k.host.coalesce(dev.setup_writes(1, prog, UIO0, UIO1, base=256)):
        rig.write(a, w)
    ctx.spim.pmod({0: UIO0, 1: UIO1})
    ctrl(rig, k.sim, 0b11)
    ctx.say(f"answering as 0x{int(addr):02x} for {seconds} s: run the Pi commands now")

    def serve():
        for w in k.rd(rig, k.FIFO(1), (rig.read(k.STATUS(1)) >> 4) & 15):
            for tx in dev.handle(w):
                rig.write(k.FIFO(1), tx, fixed=True)
    cap = la.decode_records(drain(rig, k, 0, seconds, serve), 0b11)
    for line in la.i2c_summary(la.i2c_events(cap, 0, 1)):
        ctx.say(f"  {line}")
    ctx.say(f"the device's log: {dev.log}")
    need(dev.log, "the Pi never addressed the clone")
    return dev


def act_i2c_clone(ctx, seconds=30):
    clone(ctx, seconds)


def invert_first(mem):
    """on_read for the man in the middle: the lowest captured register reads inverted."""
    reg = min(mem)

    def on_read(r, v):
        return v ^ 0xFF if r == reg else v
    on_read.reg = reg
    return on_read


def act_i2c_mitm(ctx, seconds=30):
    image = json.loads(pathlib.Path(I2C_IMAGE).read_text())
    addr = next(a for a, m in image.items() if m)
    ctx.say(f"register 0x{min(int(r) for r in image[addr]):02x} will read inverted")
    clone(ctx, seconds, on_read=lambda mem: invert_first(mem), image=image)


def act_usb_pi(ctx):
    sys.path.insert(0, str(HERE))
    import usb_demo
    rig = ctx.rig
    order = usb_demo.prepare(rig.board, ctx.spim, rig.pe, "full", log=ctx.say)
    ctx.ask("Attached: on the Pi run the printed commands, then press Enter here to detach")
    usb_demo.detach(rig.board, ctx.spim, rig.pe, "full", order)


def rx_load(rig, k, rxp):
    k.pfd.load(rig, 0, k.pfd.fw("eth10_rx"), clkdiv=1 << 8)
    rx_line(rig, k, rxp)


def rx_line(rig, k, rxp):
    rig.write(k.sim.A_LINEDIV, line_period(rig.f_clk) << 16)
    rig.write(k.sim.A_LINEPINS, rxp << 8)
    rig.write(k.sim.A_LINECFG, LINECFG_ETH)


def rx_tally(words, want):
    """(good, bad FCS or wrong bytes, short records) from one blind burst. Zero
    words are an empty FIFO; pi-send's frames have no 24-bit run of zeros."""
    sys.path.insert(0, str(HERE))
    import legacy_demo as ld
    try:
        records = ld.rx_bits([w for w in words if w])
    except Exception:
        return 0, 1, 0
    good = bad = short = 0
    for bits in records:
        if len(bits) <= 64:
            short += 1
            continue
        got = ld.frame_from_bits(bits)
        if got == want:
            good += 1
        else:
            bad += 1
    return good, bad, short


def act_rx(ctx, rounds=30):
    """Core 1 keeps the Pi's link up with pulses. Each round restarts core 0 and takes
    one blind burst in the fabric, about 6.5 ms with no Python in it: the budget of
    docs/eth10-network.md doesn't apply, because nothing streams."""
    rig, k = ctx.rig, t()
    k.pfd.setup(rig, owners={UO0: 1, UO1: 1})
    rx_load(rig, k, UI0)
    tx_load(rig, k, 1)
    ctx.spim.pmod({0: UO0, 1: UO1, 3: UI0})
    ctrl(rig, k.sim, 0b11)
    ctx.say("link pulses on, waiting 3 s for the Pi's link; start pi-send")
    time.sleep(3)
    ctx.ask("With pi-send running, press Enter")
    high = read_pad(ctx, 2000)
    ctx.say(f"the pad reads high {100 * high:.1f} % of the time (a few percent is right: 67 us of frame in every 1000 us)")
    need(high < 0.5, "the pad sits high: the divider is wrong, or the input trips below its idle level")
    want = pi_frame(FIELD) + struct.pack("<I", binascii.crc32(pi_frame(FIELD)))
    div = max(1, round(rig.f_clk * 1.6e-6 / 8))
    good = bad = short = 0
    for _ in range(rounds):
        rig.write(k.sim.A_LINECFG, 0)
        rig.write(k.sim.A_CTRL, 0b11 | (1 << 8) | (1 << 16))
        rx_line(rig, k, UI0)
        g, b, s = rx_tally(rig.burst(0, 4096, div, True), want)
        good, bad, short = good + g, bad + b, short + s
    ctx.say(f"{rounds} bursts: {good} frames equal with a good FCS, {bad} wrong, {short} short records")
    need(good >= 10, "fewer than 10 good frames")


def analyse_cut(samples, period, last_bit=MATCH_LAST_BIT):
    """Whole frames in pad samples, one sample a clock, bit 0 the line and bit 1 HIT:
    [(rise - field's last mid-bit edge, or None without HIT; clocks from HIT to the
    frame's last edge; edge found near where the bit count puts it)]. As eth10_cut."""
    rxp = [v & 1 for v in samples]
    hit = [v >> 1 & 1 for v in samples]
    edges = [i for i in range(1, len(rxp)) if rxp[i] != rxp[i - 1]]
    out, start = [], 0
    for i in range(1, len(edges) + 1):
        if i < len(edges) and edges[i] - edges[i - 1] < 8 * period:
            continue
        fr = edges[start:i]
        start = i
        whole = fr[0] >= 8 * period and len(rxp) - fr[-1] >= 8 * period     # idle seen on both sides
        if not whole or len(fr) < 400:
            continue
        rise = next((j for j in range(fr[0], fr[-1]) if hit[j] and not hit[j - 1]), None)
        if rise is None:
            out.append((None, None, True))
            continue
        want = fr[0] + period * last_bit
        mid = min(fr, key=lambda e: abs(e - want))
        out.append((rise - mid, fr[-1] - rise, abs(mid - want) <= 3))
    return out


def act_cut(ctx):
    """eth10_match on core 0; HIT is uio1, never mapped to a pad. Core 1 sends the
    link pulses that keep the Pi's link up, then for one burst (0.8 ms) becomes the
    clock-by-clock sampler, because the chip has no third core. The link needs a pulse
    only every 50 to 150 ms (from memory of 802.3, docs/eth10-network.md)."""
    rig, k = ctx.rig, t()
    text = (root() / "firmware" / "eth10_match.pasm").read_text().replace(".define HIT 8", ".define HIT 1")
    prog = k.asm.assemble(text)
    samp = k.asm.assemble((HERE / "usb_sampler.pasm").read_text(), {"TRIG": 2})
    p = line_period(rig.f_clk)
    results = {}
    for threshold in (1000, 3000):
        k.pfd.setup(rig, owners={UIO1: 0, UO0: 1, UO1: 1}, poe=0b10, gpio=0)
        k.pfd.load(rig, 0, prog, clkdiv=1 << 8)
        rx_line(rig, k, UIO0)
        tx_load(rig, k, 1)
        ctx.spim.pmod({0: UO0, 1: UO1, 3: UIO0})
        rig.write(k.FIFO(0), threshold, fixed=True)
        ctrl(rig, k.sim, 0b11)
        time.sleep(3)
        rig.write(k.sim.A_CTRL, 0b01)                 # core 1 stops, its TD pins low
        k.pfd.load(rig, 1, samp, in_pins=0, clkdiv=1 << 8)
        rig.write(k.sim.A_CTRL, 0b11 | (0b10 << 8) | (0b10 << 16))
        rig.gpio_uio(0b100)                           # the sampler's trigger, before the burst:
        words = rig.burst(1, 4096, 1, True)[9:]       # so RXF filled first; samples are contiguous after that
        rig.write(k.sim.A_CTRL, 0)
        rig.gpio_uio(0)
        samples = [(x >> (3 * (9 - i))) & 7 for x in words if x for i in range(10)]
        results[threshold] = analyse_cut(samples, p)
        ctx.say(f"threshold {threshold}: {results[threshold]} (clocks; {1e9 / rig.f_clk:.0f} ns each)")
    hits = [r for r in results[1000] if r[0] is not None]
    need(hits and all(r[2] for r in hits), "no HIT on a whole frame, or the bit count didn't line up")
    need(not any(r[0] is not None for r in results[3000]), "HIT below the threshold")


def act_ping(ctx, seconds=120):
    from pe import eth, ethhost
    rig = ctx.rig
    s = rig.spim
    s.div, s.quad = 1, True
    h = ethhost.Eth10Host(s, clock_hz=rig.f_clk)
    h.configure(ethhost.load_writes(rig.f_clk))
    station = eth.Station(eth.mac(CHIP_MAC), eth.ip(CHIP_IP))
    ctx.spim.pmod({0: UO0, 1: UO1, 3: UI0})
    ctx.say(f"answering as {CHIP_IP} for {seconds} s: start the Pi's ping")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for frame in h.service():
            for reply in station.handle(frame):
                h.send(reply)
    s.quad = False
    ctx.say(f"station {station.stats}; host {h.stats}")
    need(station.stats.get("icmp", 0) or station.stats.get("arp", 0), "no ARP or echo answered")


# ---------------------------------------------------------------- run one step

def real_rig():
    sys.path.insert(0, str(HERE))
    import postfreeze_demo as pfd
    return pfd.HwRig()


def run_step(step, state_path, make_rig=real_rig, read=input, out=print, listapps_fn=None,
             proc="/proc", lock=LOCK):
    state = load_state(state_path)
    left = missing_prereqs(step, state)
    if left:
        raise Refused(f"{step.name} needs {', '.join(left)} confirmed first (hd run {left[0]})")
    absent = [f for f in step.files if not (root() / f).exists()]
    if absent:
        raise Refused(f"{step.name} needs {', '.join(absent)}: they are on branch eth-stack, not in this tree")
    with Lock(lock):
        others = other_board_scripts(proc)
        if others:
            raise Refused(f"other board scripts are running: {others}. One process on the Kria at a time")
        describe(step, out)
        if step.pins:
            out(f"\nType exactly `{step.typed()}` if the wiring above is what is on the bench. "
                "Anything else stops here, with no pin enabled.")
            if prompt(read, "> ") != step.typed():
                raise Refused("not confirmed: nothing was enabled")
            if step.clears:                 # the wiring is on now, whatever happens next
                state = load_state(state_path)
                for n in step.clears:
                    state.pop(n, None)
                save_state(state_path, state)
                out(f"{', '.join(step.clears)} is no longer confirmed: run it again before the next step")
        if step.action:
            app = check_app(step, listapps_fn)
            rig = make_rig()
            if rig.ident["clk_div"] != APPS[app]:
                rig.close()
                raise Refused(f"{app} should have clk_div {APPS[app]}, the fabric says {rig.ident['clk_div']}")
            ctx = Ctx(step, rig, lambda m: prompt(read, m + " > "), out, app)
            status = EXIT_OK
            with Signals() as sig:
                try:
                    globals()["act_" + step.action](ctx)
                except Refused as e:
                    out(f"REFUSED: {e}")
                    status = EXIT_REFUSED
                except Stopped as e:
                    out(f"stopped by {e}")
                    status = EXIT_FAILED
                except Exception as e:           # an unknown error is a failed step, with the pins turned off below
                    out(f"FAILED: {e!r}")
                    status = EXIT_FAILED
                finally:
                    sig.live = False             # nothing interrupts the turning off
                    try:
                        rig.spim.pmod({})
                    finally:
                        rig.close()
            out("every PMOD pin is off")
            if status:
                return status
        phrase = f"passed {step.name}"
        out(f"\nSuccess looks like: {step.ok}\nType exactly `{phrase}` if you saw it. Anything else leaves it unconfirmed.")
        if prompt(read, "> ") != phrase:
            out("not confirmed")
            return EXIT_FAILED
    state = load_state(state_path)
    state[step.name] = time.time()
    save_state(state_path, state)
    out(f"{step.name} confirmed")
    return EXIT_OK


def prompt(read, text):
    try:
        return read(text).strip()
    except EOFError:
        return ""


def turn_off(listapps_fn=None):
    """Every PMOD pin disabled; nothing else touched."""
    import fabric_guard
    try:
        fabric_guard.check(listapps_fn or listapps)
    except fabric_guard.NoActiveApp as e:
        raise Refused("NOTHING WAS WRITTEN: don't assume the pins are off. Pull the wires from J2, "
                      f"and run nothing else on the board. {e}")
    from pe_spim import Spim
    Spim().pmod({})
    print("every PMOD pin is off")


# ---------------------------------------------------------------- on the Raspberry Pi

def is_pi(model="/proc/device-tree/model"):
    try:
        return "Raspberry Pi" in pathlib.Path(model).read_text(errors="replace")
    except OSError:
        return False


def default_route_on(iface, route="/proc/net/route"):
    """True if the interface carries a default route: this is not the isolated cable."""
    try:
        lines = pathlib.Path(route).read_text().splitlines()[1:]
    except OSError:
        return True
    return any(x.split()[0] == iface and x.split()[1] == "00000000" for x in lines if x.split())


def pi_frame(field_value):
    """60 bytes; the Pi's NIC appends the FCS. eth10_match reads the field. No 24-bit
    run of zeros anywhere, which a blind burst couldn't tell from an empty FIFO."""
    body = b"\xff" * 6 + bytes.fromhex("025aa5c33c7e") + struct.pack(">H", ETHERTYPE)
    return body + struct.pack("<H", field_value) + bytes(((i * 11) & 255) | 1 for i in range(44))


def pi_send(iface, seconds, gap_us, model="/proc/device-tree/model", route="/proc/net/route", net="/sys/class/net"):
    if not is_pi(model):
        raise Refused("pi-send runs on the Raspberry Pi only")
    if default_route_on(iface, route):
        raise Refused(f"{iface} has a default route: it is on a network, and this floods it. "
                      "pi-send is for the isolated cable to the chip only")
    if "carrier 1 " not in link_line(iface, net):
        raise Refused(f"{iface} has no link: the chip's link pulses aren't reaching the Pi, or the chip isn't running")
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    s.bind((iface, 0))
    frame, n, end = pi_frame(FIELD), 0, time.monotonic() + seconds
    while time.monotonic() < end:
        s.send(frame)
        n += 1
        t0 = time.monotonic()
        while time.monotonic() - t0 < gap_us / 1e6:
            pass
    print(f"{n} frames sent")
    return EXIT_OK


def link_line(iface, net="/sys/class/net"):
    def get(name):
        try:
            return (pathlib.Path(net) / iface / name).read_text().strip()
        except OSError:
            return "-"
    return (f"carrier {get('carrier')} speed {get('speed')} duplex {get('duplex')} "
            f"rx {get('statistics/rx_packets')} errors {get('statistics/rx_errors')} crc {get('statistics/rx_crc_errors')}")


def pi_watch(iface, seconds, only_ours, log="watch.log", pcap="watch.pcap", model="/proc/device-tree/model"):
    """Writes to files, so it can run with the Pi's only Ethernet port on the chip."""
    if not is_pi(model):
        raise Refused("pi-watch runs on the Raspberry Pi only: never tcpdump the Kria's eth0")
    which = ["ether", "proto", hex(ETHERTYPE)] if only_ours else []

    def start(n):       # -Z root: tcpdump would otherwise drop privileges and be unable to write here
        return subprocess.Popen(["tcpdump", "-Z", "root", "-i", iface, "-U", "-n", "-w", f"{pcap}.{n}"] + which,
                                stderr=subprocess.DEVNULL)
    n = 0
    dump = start(n)
    try:
        with open(log, "w") as f:
            for _ in range(seconds):
                line = time.strftime("%H:%M:%S ") + link_line(iface)
                print(line, flush=True)
                f.write(line + "\n")
                f.flush()
                time.sleep(1)
                if dump.poll() is not None:      # moving the cable can end tcpdump
                    n += 1
                    dump = start(n)
    finally:
        dump.terminate()
        dump.wait()
    print(f"read them with: for f in {pcap}.*; do tcpdump -r $f -e -n -x | head -n 40; done")
    return EXIT_OK


# ---------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=STATE, help="confirmations file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("show", "forget"):
        sub.add_parser(name).add_argument("step", choices=list(BY_NAME))
    sub.add_parser("run").add_argument("step", choices=list(BY_NAME))
    sub.add_parser("off")
    w = sub.add_parser("pi-watch")
    w.add_argument("--iface", default="eth0")
    w.add_argument("--seconds", type=int, default=300)
    w.add_argument("--filter", action="store_true", help="only ethertype 0x88b5 (on a shared network)")
    s = sub.add_parser("pi-send")
    s.add_argument("--iface", default="eth0")
    s.add_argument("--seconds", type=float, default=60)
    s.add_argument("--gap-us", type=float, default=1000)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            state = load_state(a.state)
            for i, st in enumerate(STEPS):
                when = time.strftime("confirmed %H:%M UTC", time.gmtime(state[st.name])) if fresh(state, st.name) else "-"
                print(f"{i:2} {st.name:12} {when:24} {st.title}")
        elif a.cmd == "show":
            describe(BY_NAME[a.step])
        elif a.cmd == "forget":
            state = load_state(a.state)
            state.pop(a.step, None)
            save_state(a.state, state)
        elif a.cmd == "off":
            turn_off()
        elif a.cmd == "pi-watch":
            return pi_watch(a.iface, a.seconds, a.filter)
        elif a.cmd == "pi-send":
            return pi_send(a.iface, a.seconds, a.gap_us)
        else:
            return run_step(BY_NAME[a.step], a.state)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
