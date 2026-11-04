# Hardware day runbook

An ordered list of what to do at the KV260 in a few hours, safest and cheapest
first. Each step has one command, the wiring by pin and breadboard hole, what must
stay disconnected, what success looks like, and what to try when it isn't that.

`fpga/kv260/host/hw_day.py` is the launcher. Every step's wiring table below is
checked against it by `test/model/test_hw_day.py`.

Commands that reach the Kria from another machine use `"$WL_KRIA"`, the board's
ssh address: `ubuntu@` and a LAN IP or a tailnet name (`<kria-host>` in the
line below). Set it once in each terminal you open on that machine:

```
export WL_KRIA=ubuntu@<kria-host>
```

**Nothing here has been run on hardware.** The launcher has run against fakes and the
cycle model only, and nobody has built these circuits. Results from this day are
RTL in FPGA fabric, not silicon. What a step proves is limited to that level.

No SWD work of any kind is part of this day.

**The Kria is down as of 2026-11-03, after a failed load. Start at step 0.**

## Rules

Read these before you touch anything. Each is a way to break something or to lose
the board.

1. **PoE can destroy FPGA pins.** A PoE port can put 48 V on an Ethernet cable. The
   FPGA pin's absolute maximum is 3.85 V (DS925: VCCO + 0.55 V, with VCCO = 3.3 V).
   The 220 ohm and 100 nF don't protect against that: they would pass about 200 mA
   at the moment of connection (48 V / 220 ohm, estimate). Chip wiring may touch
   only two things, the Tenda SG105 and the Pi. Never a wall socket, a router, an
   office switch, or a switch you haven't read the label of.
2. **Pin voltage.** Every J2 signal pin is a 3.3 V pin. Never connect 5 V to one, and
   never a signal that goes below ground or above 3.3 V unless the step has worked
   out why that is acceptable. Only `rx` does, and it says so.
3. **Shared ground.** When a wire carries a logic signal between the Pi and the Kria,
   they need a common ground, and that one wire is the only power-related wire
   between them. Connect it first, disconnect it last. Plug both boards into one
   power strip. Never join their 3.3 V or 5 V rails. Ethernet needs no shared ground,
   and gets none: the Pi's port has a transformer.
4. **Never enable a PMOD pin with unknown wiring.** Before `hd run`, look at what is
   in J2, wire by wire. An enabled pin into the wrong place is how a pin dies. The
   launcher prints the wiring and waits for a typed phrase; the phrase means you
   looked. Every pin is off unless a step has it on. `hd off` turns them all off.
5. **Never run `pmod_test.py`.** It drives the top-row pins, and with the USB wiring on
   it would drive the USB lines. No exceptions. The launcher never runs it, and refuses
   to start if it sees it running.
6. **Never touch eth0 on the Kria.** No exceptions: no `ip`, `ifconfig`, `ethtool`,
   `tcpdump`, `dhclient` or netplan there, and its cable stays where it is, both ends. It
   is the way in; `tcpdump` belongs on the Pi.
7. **One process on the Kria at a time.** Two programs on the board's registers break
   each other. The launcher takes a lock and looks for the other board scripts, but
   it can't see a hand-typed one-liner.
8. **Never reboot the Kria or power it off,** with one exception: step 0, by you, by
   hand, and only if the board doesn't answer for 10 minutes. The Pi may restart and shut down; check your prompt before you type. If the
   Kria stops answering later, the board work is over until you decide to repeat step 0.
9. **Take the USB wiring off before any Ethernet wiring goes on.** Step 2, in full, and
   again after `usb-pi`: USB plug out first, then the J2 jumpers. D+ and D- are on J2
   silkscreen 1 and 3, the pins Ethernet uses, and there is a 1.5 kohm pull-up to 3.3 V on
   one of them. The launcher takes back `usb-off` when you confirm `usb-pi`'s wiring.
10. **VBUS stays taped.** The USB cable's red wire has 5 V on it whenever its plug is
    in a powered port. It connects to nothing, today or ever.
11. **Wire with the pins off, and the Pi off.** The Kria stays on, so you wire it live.
    That is safe because a disabled pin is high impedance. `hd off` first if you are
    unsure. For anything that touches the Pi's header, shut the Pi down and unplug it.
12. **Stop if something is wrong.** Anything warm, any smell, a board going quiet: pull
    the wires from J2, then think. `hd off` after that.
13. **If `xmutil loadapp` fails, stop. Run nothing.** Any error line, `Load Error` in
    particular, or a table that doesn't show the app active, means the next command is
    not a tool. No tool may run on a board with no active app of ours: on 2026-11-03 a
    tool read the fabric while the default app held the slot, and the Kria froze;
    probably only a power cycle brings it back from that. Go to step 0's diagnosis,
    read-only. Never chain a tool after a load.

## What you need

| On the order | Used for |
|---|---|
| RJ45 breakout (or a cut cable) | Ethernet. A labelled breakout is much safer than guessing at wire colours |
| 4 x 220 ohm | Everything. See the note under `rx` |
| 2 x 100 nF | Transmit, one per line |
| I2C EEPROM or sensor | The I2C steps |

From the USB breadboard: 3 x 1 kohm (only `rx` borrows them), 6 x 10 ohm, jumpers.

**Not on your list, and needed.**

* Female-to-male jumper wires, at least 8: the Pi's header has pins, and J2 and the
  breadboard want male ends. `uart`, `spi`, the I2C steps need them.
* An Ethernet patch cable from the breakout to the Pi or the switch, and one from the Pi to
  the switch.

**Worth adding if you can.**

* A multimeter. Nothing here assumes one, and several `CHECK BEFORE WIRING` items go
  away with it.
* Four more 220 ohm. `rx` and `ping` need 4 if `tx-pi` passes with one resistor per line,
  and 6 if `tx-pi` needs two per line.

## Decide before the day

* **Whether to run `rx`, `cut` and `ping` at all.** They are the only steps where an FPGA
  pin sits near its limit on numbers nobody has measured.
* **Whether to install the `eth-stack` files.** `budget` and `ping` need them; they are not on
  this branch. Setting up has the command. Merging that branch here would conflict in
  `README.md` and `test/mutants.py`; copying the five files doesn't.
* **Whether `ping` is worth a new bitstream.** A 50 MHz app with 12 mA pins doesn't exist;
  `PMOD_DRIVE=12 fpga/kv260/build.sh 24` would make it, as a Vivado run on runner1.
* **How to reach the Pi on the isolated cable.** Wi-Fi if it has it, otherwise the
  file-writing watch this runbook uses.
* **Which I2C part.** A 24C02-style EEPROM or an 8-bit-register sensor. A 24C32 or larger
  captures, but can't be cloned.

## Setting up

Once, from your checkout of this branch. This puts one copy of the software in
`~/hwday` on the Kria, away from the app directories, and adds the alias `hd`.

```
ssh "$WL_KRIA" mkdir -p hwday/fpga/kv260
rsync -a --exclude __pycache__ tools firmware "$WL_KRIA":hwday/
rsync -a --exclude __pycache__ fpga/kv260/host "$WL_KRIA":hwday/fpga/kv260/
ssh "$WL_KRIA" "echo \"alias hd='sudo python3 -B ~/hwday/fpga/kv260/host/hw_day.py'\" >> ~/.bashrc"
git archive eth-stack tools/pe/eth.py tools/pe/ethhost.py tools/pe/ethsim.py firmware/eth10_tx_cs.pasm \
    fpga/kv260/host/eth_tap.py | ssh "$WL_KRIA" 'tar -x -C hwday'    # only for budget and ping
```

Then, on the Kria:

| Command | What it does |
|---|---|
| `hd list` | The steps in order, and which are confirmed |
| `hd show NAME` | A step's wiring and checks. Touches no hardware |
| `hd off` | Disables every PMOD pin. Use it freely |
| `hd forget NAME` | Takes back a confirmation, when wiring changed. Every step that depends on it then refuses |

`hd run NAME` prints the wiring, asks for a typed phrase before enabling any pin,
runs, turns the pins off on any exit, then asks whether you saw success. It refuses a
step unless everything it depends on, all the way back, is confirmed. A confirmation
lasts 12 hours. Confirmations are in `/var/tmp/hw_day_state.json`. If a step asks for another app, it prints the line to
run; run that with no other program going, read what it printed, then run the step
again. Rule 13 applies to every load.

### The fabric guard

`fpga/kv260/host/fabric_guard.py` runs before any program here opens `/dev/mem`:
`pe_kria.py`, `pe_spim.py` and `pe_lab.py` call it, so every demo and test script run from
`~/hwday` does too. It fails closed. Every check must pass:

* `xmutil listapps` printed exactly its table, in the format read on the board on
  2026-11-03, with one active row, named `protoemu`, `protoemu40` or `protoemu48`;
* `/proc/device-tree/fpga-region/firmware-name` names that app's bitstream;
* `/sys/class/fpga_manager/fpga0/state` says `operating`;
* `/lib/firmware/xilinx/<app>/<app>.bit.bin` exists.

None of those reads a fabric address; its docstring says what each read is and what is
from memory. A missing file refuses and names its path, so the first real run shows
whether the paths are right on this board. There is no override. If it refuses, stop and
report what it printed: never edit it to get past.

## Reference

### J2

```
   J2 (a socket) seen from above, board edge to the right. Silkscreen numbers:

      11    9    7    5    3    1        <- top row
      12   10    8    6    4    2        <- bottom row
```

Numbers in this document are always the silkscreen, not the Digilent numbering some
scripts print.

| Silkscreen | Is | pmod[] | Package pin | Evidence |
|---|---|---|---|---|
| 1 | signal | 0 | H12 | Real hardware: USB ran on it, column order confirmed by the pull-up |
| 3 | signal | 1 | E10 | The same |
| 5 | signal | 2 | D10 | Loopback pair with 6; order follows from 1 and 3 and the pin table |
| 7 | signal | 3 | C11 | Loopback pair with 8; the same |
| 2, 4, 6, 8 | signal | 4 to 7 | B10, E12, D11, B11 | Loopback pairs. Not used today |
| 9, 10 | GND | - | - | Community table only. Step `rails` checks 9 |
| 11, 12 | 3.3 V | - | - | 11 pulled up USB on real hardware. Rated 100 mA (UG1089) |

The pins are LVCMOS33. `fpga/kv260/kv260.xdc` sets them, with 4 mA drive and slow slew
by default. Disabled pins are inputs with no pull-up, so they float.

### Pi 3 header

From Raspberry Pi's documentation. It says GPIO is 3.3 V, **not 5 V tolerant**, and
gives 16 mA per pin, 50 mA in total. Pin 1 is at the corner away from the USB ports,
in the row farther from the board's edge; `pinout` on the Pi confirms it. Count from
there, every time (`CHECK BEFORE WIRING`).

| Pin | Is | Pin | Is |
|---|---|---|---|
| 1 | 3.3 V | 2 | **5 V** |
| 3 | GPIO2 SDA, fixed 1.8 kohm pull-up | 4 | **5 V** |
| 5 | GPIO3 SCL, fixed 1.8 kohm pull-up | 6 | GND |
| 8 | GPIO14 TXD | 9 | GND |
| 10 | GPIO15 RXD | 19 | GPIO10 MOSI |
| 21 | GPIO9 MISO | 23 | GPIO11 SCLK |
| 24 | GPIO8 CE0 | | |

### Breadboard

The board has 30 columns, rows a to e below the centre gap and f to j above it. In
one column, a to e are joined, and f to j are joined, and the gap separates them.
Parts go lead to lead: "`b2` to `b6`" is a resistor from hole b2 to hole b6.

All new wiring is in the lower half. The USB parts stay in the upper half, and
lower columns 20 and 30 stay free, because the 10 ohm resistors `f20`-`e20` and
`f30`-`e30` come down into them. A column holds one net. If a table doesn't mention
a column, it should be empty.

### RJ45

Pins are the breakout's labels. A station such as the Pi sends on 1 and 2 and
listens on 3 and 6; a switch port does the opposite. With a cut cable, colours depend
on whether it was made T568A or T568B, and you can't check continuity without a
meter, so `CHECK BEFORE WIRING`: use the breakout.

## Why this order

Each group adds one new kind of risk:

| Steps | New risk |
|---|---|
| 0 | Recovery: a power cycle, by you, only if the board doesn't answer |
| 1 to 6 | None to the pins. No pin is enabled |
| 7 to 9 | Chip outputs, through resistors and capacitors, into a transformer |
| 10 to 15 | A shared ground and direct logic connections to the Pi, then USB's 5 V nearby |
| 16 to 18 | A signal larger than the pin's supply, on unmeasured estimates |

Receive comes after UART, I2C and USB even though networking is what matters most,
because it is the one step where a pin could be hurt by a signal that is doing what
it should. The cheap, independent steps should be done before that risk is taken.
If time runs short, stop before step 16 and lose nothing that was already proved.

Steps 1 to 18 add up to about 6 hours (estimate), more than the day may have. Steps 1 to
9 are about 2 hours. If networking matters most, steps 10 to 15 can be skipped: `rx` needs
only `tx-pi`, so 1 to 9 then 16 to 18 is about 3.5 hours (estimate). That is a choice to
take the pin risk without the safer results in hand.

### Why the isolated cable first

The direct cable to the Pi (`tx-pi`) comes before the switch (`tx-switch`). I judge it the
safer first option for four reasons:

* **No 48 V can be present.** A Pi's Ethernet port doesn't source PoE. With a switch you
  rely on the label on the box and on picking the right socket.
* **Nothing else is on it.** On the isolated cable, nothing the chip does can reach any
  network. The Kria's own link is on neither path, whichever you run.
* **You can see what happened.** The Pi reports link state, speed, and error counters. A
  switch shows one LED and silently drops a bad frame.
* **A wiring slip costs less.** If the wrong wire lands on the Pi, the worst case is one Pi
  port.

The cost is that the Pi leaves the network while it runs. The file-writing watch
handles that.

## Drive levels

These are estimates: no scope, nothing measured. Two pins push a 100 ohm receiver in
opposite directions through a series resistor each. Peak differential voltage is
3.3 V x 100 / (100 + 2 Rs + 2 Rd), taking the 12 mA pin's own resistance Rd as
30 to 50 ohm (an assumption).

| Series per line | Parts | Peak at the receiver (estimate) | Pin current (estimate) |
|---|---|---|---|
| 220 ohm | 2 x 220 ohm | 0.52 to 0.55 V | 5.2 to 5.5 mA |
| 110 ohm | 4 x 220 ohm, in pairs | 0.79 to 0.87 V | 7.9 to 8.7 mA |

A real 10BASE-T transmitter gives 2.2 to 2.8 V, so both are well under a normal
signal. What matters is the receiver's squelch, the level under which it treats a
signal as noise. PHY datasheets in the same family as the Pi's quote a threshold of
300 mV minimum, 420 mV typical, 585 mV maximum: below 300 mV is rejected and above
585 mV is detected (LAN83C185, LAN9303; datasheets, not the 802.3 text). So 220 ohm
sits in the band where a receiver may or may not hear it, and 110 ohm clears it.
Start with 220 ohm, which uses the fewest parts and the least current. Add the second
pair if there is no link. A gigabit switch's receiver may differ; nothing here says.

The app matters. `protoemu40` was built with 12 mA drive (project notes; the bitstream
can't report this). Both loads are within that; on the 4 mA `protoemu` app they are not,
and the amplitude falls further. At 40 MHz transmit half cells are exactly 50 ns; at
50 MHz they are 40 and 60 ns.

The 100 nF block DC. With the series resistors they make a time constant of tens of
microseconds (estimate), far longer than a 100 ns pulse.

## Receive: what the resistor-only attempt is

`docs/eth10-receiver.md` describes what a receiver needs, and it is not this:

| It says it needs | Here |
|---|---|
| 1:1 magnetics on the chip's side | None. The Pi's own transformer isolates its side |
| 100 ohm termination, 1 % | 2 x 220 ohm in parallel, 110 ohm |
| A bias network, comparators (TLV3502), window detector, envelope, gating | None. The FPGA input's own threshold is the comparator |
| A PCB with short paths | A breadboard, whose parasitics no calculation here includes |
| Idle low on `ui0`, positive differential high | Yes, if the trip point lands between the levels below |
| Scope captures of idle, pulses, packets and overshoot before connecting anything | Not possible today |
| 50 MHz, five clocks per bit | Try `protoemu40` first (its pins carry the link), `protoemu` second. 40 MHz receive still fails the full jitter tests |

That document's circuit is for the chip, on a board, and it stays the real answer. Do
not read a pass here as a qualification of anything; it says this one link worked.

The circuit. RJ45 pin 2 (the Pi's TD-) ties to GND, which is safe because the Pi's side of
its transformer floats. Pin 1 feeds a three-resistor divider. All values below are
estimates: a nominal +-2.5 V source, nothing measured.

```
   RJ45 1 ---+--- 1 kohm ---+--- J2 silkscreen 7 (ui0, input)
             |              |
          110 ohm           +--- 1 kohm --- 3.3 V (J2 silkscreen 11)
    (2 x 220 ohm)           |
             |              +--- 1 kohm --- GND
   RJ45 2 ---+---------------------------- GND (J2 silkscreen 9)
```

The pad sits at 1.1 V with no signal, and moves one third of the differential voltage:

| Differential input | Pad low | Pad high |
|---|---|---|
| +-2.2 V | 0.37 V | 1.83 V |
| +-2.5 V | 0.27 V | 1.93 V |
| +-2.8 V | 0.17 V | 2.03 V |
| +-5 V, if the termination falls out | -0.57 V, past the -0.55 V absolute minimum | 2.77 V |

Why not simply a series resistor? A pad taken to -2.5 V through a resistor conducts
through its input diode for half of every bit. DS925's AC limits allow -0.30 V all of
the time and only -0.45 V for 40 % of it, so that design breaks them on every frame,
and the divider doesn't. The load on the pair is 110 ohm in parallel with 1.5 kohm,
about 102 ohm. With 4.75 pF at the die (DS925) and about 10 pF of breadboard (a guess),
the 333 ohm at the pad gives roughly 5 ns.

Two weak points, stated plainly. The idle level, 1.1 V, is above the 0.8 V usually
guaranteed as low, and the high level, 1.93 V, is below the 2.0 V usually guaranteed as
high (standard LVCMOS33 figures, from memory: `CHECK BEFORE WIRING`). It works only if this
pin's real trip point lies between them, which isn't promised. It's also a threshold
above the zero crossing, so rising edges come late and falling edges early, a few ns
each (estimate), eating into the +-13.5 ns the firmware was tested for. The launcher
checks the idle level before it trusts anything else.

Parts: this needs 4 x 220 ohm and 3 x 1 kohm, with the transmit side on one 220 ohm per
line. See What you need.

## The steps

### Step 0: Bring the board back; find out why loadapp failed (`recover`)

Time: 30 min (estimate). App: any. Needs: nothing.

**Risk.** On 2026-11-03 the Kria had rebooted into its default app, `k26-starter-kits`, which held the slot. `xmutil loadapp protoemu` printed `protoemu: Load Error: -1`, the next command ran a host tool anyway, the tool read pe_spim's address inside the wrong design, and the board froze: its journal for that boot ends as the command started. Probably only a power cycle brings a board back from that; nobody here can do it but you. A board with no active app of ours must have no tool run on it: no `hd run` of a board step, no demo, no `bringup_kria.py`, nothing that opens `/dev/mem`. This step itself opens nothing.

**Before you start.** **First, does it answer?** From another machine: `ssh "$WL_KRIA" uptime`. If it answers, do not power it off, whatever app is active: go straight to the diagnosis. A power cycle is only for a board that doesn't answer.

**Only if it doesn't answer for 10 minutes:** take every wire out of J2. Unplug the Kria's power, wait 10 seconds, plug it back in. It boots into `k26-starter-kits`, not ours: expected. After a few minutes (estimate) the `ssh` line above should answer, with an uptime of minutes. If it doesn't within 10 minutes, check the power and link LEDs, and stop there. This is the one power cycle in this runbook; confirming this step lasts, so it never sends you back to it.

**Diagnosis, read-only.** Run the commands below and write down what they print. `-b -1` is the boot that froze, and may be empty if the journal doesn't persist. What to look for:

* Another app active in the table, as on 2026-11-03: a load fails while another app holds the slot. The sequence below unloads it first.
* `ls` shows no `protoemu.bit.bin` or `.dtbo`: the install is incomplete. Run `install.sh` with `LOAD=0` from your checkout.
* `dfx-mgr` not running, or errors about `shell.json`, overlays, or the FPGA manager: note them. Don't work around them.
* The bitstream is rejected in `dmesg`: a corrupt or wrong file. Rebuild or reinstall it.

**The old copies of the host scripts are unguarded.** `install.sh` put `tools`, `firmware` and `host` in `~/protoemu`, `~/protoemu40` and `~/protoemu48`, from before the guard existed; the last `ls` shows which exist. Rename each one so nothing runs from it, for example `mv ~/protoemu ~/protoemu-unguarded`, and use only `~/hwday` (Setting up). Renaming changes no app: those live in `/lib/firmware/xilinx`.

**The load, one command at a time.** Read what each prints before typing the next. Never put them on one line, and never put a tool after them:

    sudo xmutil unloadapp
    sudo xmutil loadapp protoemu
    sudo xmutil listapps

It worked only if no line says error and the table shows `protoemu` as its one active row. If not, stop. Run no tool. A second failure is the day's result until the cause is known. Then tools may run: every program here that maps the fabric goes through `fabric_guard.py` first, and it refuses unless the table, the device tree and the FPGA manager all agree.

**Must stay disconnected:**

* Every wire in J2: out, before anything else

**On the Kria, read-only:**

```
uptime
sudo xmutil listapps
cat /sys/class/fpga_manager/fpga0/state
cat /proc/device-tree/fpga-region/firmware-name; echo
sudo dmesg | grep -iE 'fpga|dfx|overlay|zynqmp' | tail -n 60
sudo journalctl -u dfx-mgr -b --no-pager | tail -n 60
sudo journalctl -u dfx-mgr -b -1 --no-pager | tail -n 60
ls -l /lib/firmware/xilinx/protoemu/
ls -ld ~/protoemu ~/protoemu40 ~/protoemu48
```

**Command, on the Kria:**

```
hd run recover
```

**Success:**

* The Kria answers; what the logs say is written down; the old copies are renamed; the load shows `protoemu` as the one active row. On the Kria, `hd run recover` and type `passed recover`. `hd` only records this; if it isn't installed yet, do Setting up first, which copies files and touches no fabric.

**Failure, and what to try:**

* No answer after a power cycle and 10 minutes: leave it. Nothing in this runbook works without it.
* The load fails again: stop. Keep the logs. No tool runs on this board until our app shows active.

### Step 1: Read the board, change nothing (`preflight`)

Time: 5 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `recover`.

**Risk.** None. No wire moves and no pin is enabled. Like every demo, it resets the chip's cores. The USB wiring may still be on the board: that is fine here, because every PMOD pin is off.

**Before you start.** Open a terminal on the Kria (`ssh "$WL_KRIA"`). This is your terminal for every `hd` command today.

**Must stay disconnected:**

* Nothing changes in this step: leave the wiring as it is

**Command, on the Kria:**

```
hd run preflight
```

**Success:**

* It names the active app, prints the RTL revision and clock, and shows `PMOD0 00000000 PMOD1 00000000`.
* `installed apps checked: all three`. Type `passed preflight`.

**Failure, and what to try:**

* `active app is 'k26-starter-kits'` or similar: run the `xmutil` line it prints, then run the step again. The launcher refuses to read the fabric with no protoemu app active, because reading those addresses with nothing behind them can hang the board.
* `isn't a row of its table`, `didn't print its table`, or a missing file named: the guard couldn't confirm our app, so it opened nothing. Stop, copy what it printed for the author, and run nothing else. There is no way round it, on purpose.
* `MISSING protoemu40`: stop and decide; the transmit steps need that app (it is in the project notes as installed on 2026-10-30, and nothing today has checked that). `MISSING protoemu48` only blocks `usb-pi`.
* PMOD not zero: run `hd off`, run the step again, and find out what left a pin on.

### Step 2: Take the USB wiring off the Kria (`usb-off`)

Time: 5 min (estimate). App: any. Needs: `preflight`.

**Risk.** This step removes one, and comes again after `usb-pi`. The USB wiring puts a 1.5 kohm pull-up to 3.3 V, and a live USB host's drivers, on J2 silkscreen 1 and 3. Those are the pins Ethernet is about to use. With both connected, the chip's outputs would fight the host and the pull-up, and the USB hub would see a device appear and vanish. Take it off first, every time, before any Ethernet wire goes near J2.

**Remove first:**

* USB plug out first, of whichever port holds it, the Kria's or the Pi's (and leave it out)
* Jumper J2 silkscreen 1 to a30: remove both ends
* Jumper J2 silkscreen 3 to a20: remove both ends
* Jumper J2 silkscreen 11 to i3: remove both ends
* After usb-pi: jumper J2 silkscreen 9 to b24, both ends, and the black wire out of a24 and taped

**Before you start.** `hd off` first if anything was running. Do this by hand with the board on; the pins are off, so nothing is driven. Leave every part in the breadboard: the resistors stay in rows f to j, and `usb-pi` needs them. The lower half (rows a to e) is what the next steps use, and columns 20 and 30 of it stay empty because the 10 ohm resistors end there.

**Must stay disconnected:**

* J2 empty: not one wire in it
* USB plug in no port; red VBUS and black GND still taped

**Command, on the Kria:**

```
hd run usb-off
```

**Success:**

* J2 has no wire in it. The USB plug is in no port. Type `passed usb-off`.

**Failure, and what to try:**

* A wire you can't account for in J2: leave it out, and look at the [breadboard drawing](https://claude.ai/artifact/HFuso5WMQag4M3qdM89qnh) of the USB wiring.

### Step 3: Find GND and 3.3 V on J2 without a meter (`rails`)

Time: 10 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `usb-off`.

**Risk.** Low. No pin is enabled: the launcher only reads the pad. The resistor protects the pad if the rails are not where the notes say. This step exists because later steps put a ground wire on J2 silkscreen 9. If that pin were 3.3 V, a ground wire from the Pi would be a short between two power supplies. Silkscreen 11 has been used as the 3.3 V pull-up on real hardware; silkscreen 9 as GND has only a community pin table behind it.

**Before you start.** With a multimeter you can do better: black probe on a USB shell or other known ground, red on silkscreen 11 (expect about 3.3 V), then on silkscreen 9 (expect 0 V). Without one, this step tells you which way each pin pulls. It can't give a voltage.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 7 | a24 |
| 220 ohm | b24 to b28 |
| test jumper, one end | c28 |
| test jumper, other end | J2 silkscreen 11, moved to 9 and back when asked |

**Must stay disconnected:**

* J2: only those two wires
* The Pi
* Every network cable

**Command, on the Kria:**

```
hd run rails
```

**Success:**

* `silkscreen 11, 9, 11: read high 1.00, 0.00, 1.00`. An undriven pad holds its last level, so each pin had to move it: silkscreen 9 pulled it low, silkscreen 11 pulled it back up. So 9 is a low rail and not an open pin. Type `passed rails`.
* Remove these wires and the resistor afterwards.

**Failure, and what to try:**

* Any other reading: stop. Don't put a ground wire on J2. Check that the resistor and jumpers sit in the holes the table says; the columns of a breadboard join only a to e and f to j. If it still reads wrong, get a meter or read the KV260 carrier schematic.

### Step 4: Give the Pi a network; the Kria's cable stays (`pi-net`)

Time: 15 min (estimate). App: any. Needs: `preflight`.

**Risk.** None to the Kria, because this step doesn't touch it. Its cable stays exactly where it is, at both ends, all day: nothing here needs it moved, and moving a board's only link is a risk nobody should take with it. The switch never joins the Kria. No chip wiring goes near the switch in this step.

**Before you start.** Check the box and the label: `SG105`, no PoE marking. Reseller listings give it as non-PoE, 10/100/1000 with auto MDI/MDIX; Tenda's own datasheet was not found, so the label is the check (`CHECK BEFORE WIRING`). The Pi needs a network for the next step. If your router has a free port, connect the switch to it: that is your call, and you make it. If it has none, don't unplug anything to make room: use the Pi's own Wi-Fi, or a keyboard and screen. With a keyboard and screen and no network at all, `tcpdump`, `i2c-tools` and `python3-spidev` must already be on the Pi, and `hw_day.py` goes over on a USB stick.

**Wiring, in this order:**

| From | To |
|---|---|
| Tenda SG105 power | its own adapter |
| A free router port, only if one exists | Tenda SG105 port 1 |
| Pi eth0 | Tenda SG105 port 2 |

**Must stay disconnected:**

* The Kria's network cable, both ends: it stays where it is, all day
* The Kria and the switch: nothing joins them
* J2: no chip wiring near the switch yet
* Every other switch, router or wall network socket: this wiring may only ever touch the Tenda SG105 or the Pi. Any of them could carry 48 V PoE

**Command, on the Kria:**

```
hd run pi-net
```

**Success:**

* The Pi reaches your network, or you are at it with a keyboard and screen. On the Kria, `hd run pi-net` and type `passed pi-net`.

**Failure, and what to try:**

* No link LED on the switch: its power, then the cable. Leave the Kria alone.
* No free router port and no Wi-Fi: a keyboard and screen, as above.

### Step 5: Prepare the Pi while it has a network (`pi-prep`)

Time: 20 min (estimate). App: any. Needs: `pi-net`.

**Risk.** Low. No wire joins the boards yet. Changing the Pi's settings can lose you the Pi, but not the Kria. The restart at the end is the Pi's: look at your prompt before you type it.

**Before you start.** `Raspberry Pi OS` is assumed, and the `/boot/firmware/config.txt` path assumes Bookworm or later; on older releases it is `/boot/config.txt` (`CHECK BEFORE WIRING`). Run the commands below on the Pi. Copy `fpga/kv260/host/hw_day.py` from your checkout to the Pi's home with `scp`. If you want the Pi reachable during the isolated-cable steps, set up Wi-Fi now. If not, the watch program below logs to a file instead.

**Must stay disconnected:**

* J2: nothing to the Pi yet

**On the Pi:**

```
cat /proc/device-tree/model; echo
pinout
sudo apt-get update && sudo apt-get install -y tcpdump i2c-tools python3-spidev python3-gpiozero
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_serial_hw 0; sudo raspi-config nonint do_serial_cons 1
echo 'dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=2,i2c_gpio_scl=3,i2c_gpio_delay_us=100' | sudo tee -a /boot/firmware/config.txt
restart the Pi (the Pi, not the Kria), and copy hw_day.py to it from your checkout with scp
```

**Command, on the Kria:**

```
hd run pi-prep
```

**Success:**

* The model line says Raspberry Pi 3. Note if it says `3 Model B+`: that board has a different Ethernet chip (LAN7515, against the 3 B's LAN9514).
* `pinout` draws the header; pins 1, 2, 3, 4, 5, 6, 8, 9, 10, 19, 21, 23, 24 match the Pi pin table above.
* After the Pi's restart, `ls /dev/i2c-3 /dev/spidev0.0 /dev/serial0` lists all three. On the Kria, `hd run pi-prep` and type `passed pi-prep`.

**Failure, and what to try:**

* No `raspi-config`: not Raspberry Pi OS; find the local way to enable SPI and the UART, and to add the overlay.
* No `/dev/i2c-3`: the overlay line is in the wrong file, or was not read. See `/boot/firmware/overlays/README` on the Pi for the options; `i2c_gpio_delay_us=100` comes from a user report that gives it as about 10 kHz, not from Raspberry Pi's own text.

### Step 6: Is Python fast enough for ping? (`budget`)

Time: 5 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `preflight`.

**Risk.** None to the pins: it enables none. It borrows the chip's host port, and gives it back on any exit (from docs/eth10-network.md on `eth-stack`).

**Before you start.** This needs the `eth-stack` files on the Kria (see Setting up). Without them the launcher refuses, and only this step and `ping` are lost.

**Must stay disconnected:**

* J2: empty or anything: no pin is enabled

**Command, on the Kria:**

```
hd run budget
```

**Success:**

* A number at or under about 18 us per pass. That go/no-go is docs/eth10-network.md's estimate, built on a tested 15.5 us of software time per pass, and the measured pass there reads 4 words where a late host reads 8, so treat a near miss as a miss. Type `passed budget`.

**Failure, and what to try:**

* Over the budget: Python on the Kria can't stream a frame, and `ping` is off today. Nothing else depends on this. `rx` and `cut` take one fabric burst with no Python in it, so they don't need this number.

### Step 7: Check the transmit wiring, no cable yet (`tx-bench`)

Time: 15 min (estimate). App: protoemu40. Needs: `rails`.

**Risk.** Low. These are the first outputs enabled today, into nothing but the read-back pad, through 220 ohm. Nothing is connected to the RJ45 side. This checks that each chip pin reaches the column you think, before any cable exists.

**Before you start.** `hd off`. Place the parts on the lower half of the breadboard. If the app isn't `protoemu40`, the launcher tells you the line to run. Both 220 ohm and 100 nF are not polarised: either way round.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 1 (TD+) | a2 |
| 220 ohm | b2 to b6 |
| 100 nF | c6 to c8 |
| J2 silkscreen 3 (TD-) | a12 |
| 220 ohm | b12 to b16 |
| 100 nF | c16 to c18 |
| test jumper, one end | J2 silkscreen 7 |
| test jumper, other end | e6, moved to e16 when asked |

**Must stay disconnected:**

* RJ45 breakout: no wire from it on the breadboard
* The Pi and the switch

**Command, on the Kria:**

```
hd run tx-bench
```

Type at its prompt: `wired tx-bench`. It enables only: silkscreen 1, silkscreen 3.

**Success:**

* `e6: follows [0, 1, 0, 1, 0, 1], with the other line high instead reads 0`, and the same for `e16`. Type `passed tx-bench`. Take the test jumper out and leave the rest.

**Failure, and what to try:**

* `not wired as the step says`: the launcher has already turned the pins off. Fix the wire: check the resistor really shares a column with its J2 jumper, and that J2 silkscreen 1 and 3 are the two pins at the right end of the top row, board edge to the right.

### Step 8: Transmit to the Pi on an isolated cable (`tx-pi`)

Time: 30 min (estimate). App: protoemu40. Needs: `tx-bench`, `pi-prep`.

**Risk.** Low. This is why it comes before the switch: see Why the isolated cable first. Current is limited by the resistors, to about 6 mA (estimate), and the capacitors block DC. The Pi's Ethernet port is transformer-coupled, so no GND wire joins the boards here, and none should.

**Before you start.** The Pi has one Ethernet port, and this step takes it off the network. So start the watch first; it writes to files and doesn't need you. On the Pi, while you can still reach it, run the command below. Then take any cable out of the Pi's Ethernet port, and connect the breakout to the Pi's Ethernet port with a plain patch cable, last. Then, on the Kria, run the launcher.

The chip sends on RJ45 pins 3 and 6 here because those are the pair a station like the Pi listens on, 802.3's normal wiring for two stations with no crossover. The Pi's Auto-MDIX could swap pairs on its own (LAN9514's listing has it; `CHECK BEFORE WIRING` for a 3 B+). Its transmit pulses could then reach the chip's pins through 220 ohm and 100 nF, and it is likeliest to hunt like that with no link, so with our pins disabled, before and after the launcher runs. Worked out for that case (estimate, unmeasured): the pads float, and the pair can push them only until one input's upper diode and the other's lower diode conduct, about 4.5 V apart. A pulse of up to 5 V open-circuit then drives about 1 mA through 540 ohm for under 100 ns, 2 mA with the second pair of resistors. Those are short, rare pulses, well inside DS925's undershoot allowance, and small against DS925's absolute maximum of 20 mA of output current at a pad. With the pins driven the worst case is about 9 mA for the same time (5 V through 540 ohm).

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 1 (TD+) | a2 |
| 220 ohm | b2 to b6 |
| 100 nF | c6 to c8 |
| J2 silkscreen 3 (TD-) | a12 |
| 220 ohm | b12 to b16 |
| 100 nF | c16 to c18 |
| RJ45 pin 3 | a8 |
| RJ45 pin 6 | a18 |
| Patch cable: breakout to Pi eth0, last | not to the switch |

**Must stay disconnected:**

* RJ45 pins 1, 2, 4, 5, 7, 8
* J2 silkscreen 5, 7, 9, 11
* Any GND wire between the boards: the Pi's Ethernet port is transformer-isolated and needs none
* Every other switch, router or wall network socket: this wiring may only ever touch the Tenda SG105 or the Pi. Any of them could carry 48 V PoE

**On the Pi:**

```
sudo nohup python3 hw_day.py pi-watch --seconds 600 > watch.out 2>&1 &
```

**Command, on the Kria:**

```
hd run tx-pi
```

Type at its prompt: `wired tx-pi`. It enables only: silkscreen 1, silkscreen 3.

**Success:**

* The launcher finishes: `600 frames sent` after two minutes.
* Put the Pi back on its network, log in, and look at `watch.log`: `carrier 1 speed 10 duplex half` while the chip ran. That alone says the Pi's receiver accepted the link pulses.
* `for f in watch.pcap.*; do sudo tcpdump -r $f -e -n -x | head -n 40; done` shows `02:57:4c:00:00:02 > ff:ff:ff:ff:ff:ff, ethertype Unknown (0x88b5), length 60` with `WIRELINGO HW DAY` in the bytes. tcpdump's exact wording may differ. Keep both files. Type `passed tx-pi`.

**Failure, and what to try:**

* `carrier 0` all the way: the link pulses are too weak or on the wrong pins. First add the second 220 ohm on each line, `d2` to `d6` and `d12` to `d16` (the table in Drive levels says why), and run again. Then check RJ45 pins 3 and 6, and that the pair isn't landing on pins 1 and 2.
* `carrier 1` but no frames, `crc` or `errors` counting up: amplitude or timing is marginal. Add the second resistors. A swapped pair is also worth one try: swap the wires at `a8` and `a18`.
* `speed` shows `-` or the link flaps: note it, try the second resistors.
* Pcap empty but `carrier 1`: tcpdump may have stopped when the cable moved. The watch restarts it; look at every `watch.pcap.*` file.

### Step 9: Transmit into the switch, seen by tcpdump (`tx-switch`)

Time: 20 min (estimate). App: protoemu40. Needs: `tx-pi`.

**Risk.** This is the step PoE can hurt. Plugged into a PoE port, 48 V can destroy the FPGA pins, and the resistors and capacitors won't save them. A compliant PoE port applies 48 V only after it detects a powered device, but a passive injector applies it always. You are safe only because this switch has no PoE and this cable goes nowhere else. The launcher asks for a longer phrase here for that reason.

**Remove first:**

* Any cable from a router in Tenda port 1: this test wants no network on the switch

**Before you start.** Take any router cable out of the Tenda first: this test wants only the chip and the Pi on the switch, so no frame leaves it. Start the watch on the Pi while you can still reach it, then put the Pi on Tenda port 2.

Move two wires only: RJ45 pin 3 becomes pin 1 at `a8`, and pin 6 becomes pin 2 at `a18`. A switch port listens on pins 1 and 2. If `tx-pi` needed the second 220 ohm on each line, keep them here (`d2` to `d6`, `d12` to `d16`).

Follow the patch cable with your hand, into the Tenda, and only the Tenda. Don't use a wall socket, whose other end you can't see.

The switch forwards frames that pass its FCS check and drops the rest silently, and an unmanaged switch has no port mirror. So a broadcast is the frame to use, and it reaches the Pi. tcpdump runs on the Pi: the rule against touching the Kria's eth0 covers tcpdump too.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 1 (TD+) | a2 |
| 220 ohm | b2 to b6 |
| 100 nF | c6 to c8 |
| J2 silkscreen 3 (TD-) | a12 |
| 220 ohm | b12 to b16 |
| 100 nF | c16 to c18 |
| RJ45 pin 1 | a8 |
| RJ45 pin 2 | a18 |
| Pi eth0 | Tenda SG105 port 2 |
| Patch cable: breakout to Tenda SG105 port 4, last | the Tenda, no other |

**Must stay disconnected:**

* RJ45 pins 3, 4, 5, 6, 7, 8
* J2 silkscreen 5, 7, 9, 11
* Every other switch, router or wall network socket: this wiring may only ever touch the Tenda SG105 or the Pi. Any of them could carry 48 V PoE

**On the Pi:**

```
sudo nohup python3 hw_day.py pi-watch --seconds 600 --filter > watch.out 2>&1 &
```

**Command, on the Kria:**

```
hd run tx-switch
```

Type at its prompt: `not poe wired tx-switch`. It enables only: silkscreen 1, silkscreen 3.

**Success:**

* After the watch ends, with the Pi reachable again: `for f in watch.pcap.*; do sudo tcpdump -r $f -e -n -x | head -n 40; done` shows the `0x88b5` broadcast from `02:57:4c:00:00:02`.
* A frame through the switch says something a Pi capture can't: another vendor's receiver took the waveform, and its FCS was good. Type `passed tx-switch`.

**Failure, and what to try:**

* Port LED dark: the switch's PHY did not accept the link. Parallel detection on this switch is unverified. Add the second resistors if they aren't in. This step failing blocks nothing: the direct link has already shown the waveform.
* LED on, no frames: bad FCS is dropped silently here, and there is no counter to read. Go back to the isolated cable and look at `crc`.

### Step 10: UART both ways with the Pi (`uart`)

Time: 30 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `rails`, `pi-prep`.

**Risk.** Medium: the first wire that shares ground between boards, and the first time a Pi GPIO meets a chip pin. Two outputs fighting are limited by 220 ohm to about 15 mA (3.3 V / 220 ohm, estimate), inside the Pi's 16 mA per pin. Pins 2 and 4 of the Pi are 5 V, next to pin 6: a wire one pin off puts 5 V on the wrong net, so the Pi is off and unplugged while you wire, and you count pins from pin 1.

**Before you start.** Take the J2 wires, the breakout and the 100 nF off. The 220 ohm at `b2`-`b6` and `b12`-`b16` stay; remove any second pair at `d2`-`d6`, `d12`-`d16`. On the Pi: `sudo shutdown -h now`, wait for the green LED to stop, unplug its power. Plug both boards' supplies into one power strip. Wire the ground first, in the order below. Put the Pi back on its network, plug its power back in, and log in.

The launcher only enables chip TX and RX. If the Pi's console is still on the UART, its login prompt will appear in what the chip hears: run `sudo raspi-config nonint do_serial_cons 1` and restart the Pi.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 9 (GND) | a24 |
| Pi pin 6 (GND), first of the Pi wires | b24 |
| J2 silkscreen 1 (chip TX) | a2 |
| 220 ohm | b2 to b6 |
| Pi pin 10 (GPIO15, RXD) | a6 |
| J2 silkscreen 7 (chip RX) | a12 |
| 220 ohm | b12 to b16 |
| Pi pin 8 (GPIO14, TXD) | a16 |

**Must stay disconnected:**

* No wire between the Kria's 3.3 V (J2 silkscreen 11, 12) and anything on the Pi
* Pi pins 2 and 4 (5 V): nothing on them, ever
* Every Ethernet wire and the RJ45 breakout
* The USB plug

**On the Pi:**

```
stty -F /dev/serial0 115200 raw -echo; timeout 25 cat /dev/serial0 & for i in 1 2 3 4 5; do printf 'HELLO FROM PI\n' > /dev/serial0; sleep 2; done
```

**Command, on the Kria:**

```
hd run uart
```

Type at its prompt: `wired uart`. It enables only: silkscreen 1, silkscreen 7.

**Success:**

* The Pi prints `WLGO 000`, `WLGO 001`, and so on, and the launcher prints `HELLO FROM PI`. Real external UART both ways at 115200. Type `passed uart`.

**Failure, and what to try:**

* Works one way: check that one wire's column. TX on the chip goes to RXD on the Pi (pin 10); RX to TXD (pin 8).
* Nothing either way: the ground wire; then `ls -l /dev/serial0`.
* Garbage: the Pi's mini UART follows its core clock; `enable_uart=1`, which `do_serial_hw 0` should set, fixes it (`CHECK BEFORE WIRING`).

### Step 11: Capture the Pi's SPI master (`spi`)

Time: 20 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `uart`.

**Risk.** Low, once `uart` has passed: all three chip pins are inputs, so nothing on the chip side drives. MISO stays unconnected.

**Before you start.** Pi off for the rewiring, ground first as before. This step is a capture: the Pi's real SPI master drives `la_capture` on core 0. It tests the capture of a real SPI bus, at 20 kHz. It doesn't test the chip's own SPI master firmware against the Pi, because Linux on the Pi was not found to offer an SPI device mode; `firmware/spi_master*.pasm` needs a real SPI part for that, and none is on the order.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 9 (GND) | a24 |
| Pi pin 6 (GND), first of the Pi wires | b24 |
| J2 silkscreen 1 (SCK in) | a2 |
| 220 ohm | b2 to b6 |
| Pi pin 23 (GPIO11, SCLK) | a6 |
| J2 silkscreen 3 (MOSI in) | a12 |
| 220 ohm | b12 to b16 |
| Pi pin 19 (GPIO10, MOSI) | a16 |
| J2 silkscreen 5 (CE0 in) | a22 |
| 220 ohm | b22 to b26 |
| Pi pin 24 (GPIO8, CE0) | a26 |

**Must stay disconnected:**

* No wire between the Kria's 3.3 V (J2 silkscreen 11, 12) and anything on the Pi
* Pi pins 2 and 4 (5 V): nothing on them, ever
* Pi pin 21 (MISO): nothing drives it here
* Every Ethernet wire and the USB plug

**On the Pi:**

```
python3 -c "import spidev,time; s=spidev.SpiDev(0,0); s.max_speed_hz=20000; s.mode=0; [(s.xfer2(list(b'WIRELINGO')), time.sleep(0.5)) for _ in range(20)]"
```

**Command, on the Kria:**

```
hd run spi
```

Type at its prompt: `wired spi`. It enables only: silkscreen 1, silkscreen 3, silkscreen 5.

**Success:**

* `CS frames: ['WIRELINGO', ...]` and `0 gaps`. Type `passed spi`.

**Failure, and what to try:**

* Gaps: Python's drain loop fell behind; lower `max_speed_hz` to 8000. The record rate is the limit, not the wire.
* No edges: the ground wire, then whether `/dev/spidev0.0` exists.

### Step 12: Capture a real I2C part (`i2c-capture`)

Time: 30 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `rails`, `pi-prep`.

**Risk.** Low to medium. The chip only listens here. Check the part: it must run at 3.3 V, and a breakout with a 5 V regulator or 5 V pull-ups is out. The part's power comes from the Pi's 3.3 V (pin 1), and J2's 3.3 V stays empty: the two boards' rails must never join. The pull-ups are the Pi's own 1.8 kohm on GPIO2 and GPIO3, so you add none.

**Before you start.** Pi off for the rewiring, ground first. The bus runs on the Pi's software I2C (`i2c-gpio`, `/dev/i2c-3`), not the hardware block, for two reasons: the BCM283x's hardware clock stretching is broken, and the clone in the next step stretches; and Python drains this capture, so the bus must be slow. Check the part's pin names on its own datasheet (`CHECK BEFORE WIRING`). Replace `0x50` and `0x10` below with what `i2cdetect` shows and a register your part has. A sensor is fine if it has 8-bit registers.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 9 (GND) | a24 |
| Pi pin 6 (GND), first of the Pi wires | b24 |
| J2 silkscreen 1 (SDA) | a2 |
| 220 ohm | b2 to b6 |
| Pi pin 3 (GPIO2, SDA) | a6 |
| Part SDA | c6 |
| J2 silkscreen 3 (SCL) | a12 |
| 220 ohm | b12 to b16 |
| Pi pin 5 (GPIO3, SCL) | a16 |
| Part SCL | c16 |
| Part GND | c24 |
| Pi pin 1 (3.3 V) | a28 |
| Part VCC | b28 |

**Must stay disconnected:**

* Pi pins 2 and 4 (5 V): nothing on them, ever
* J2 silkscreen 11, 12: the part runs from the Pi's 3.3 V alone
* Any added pull-up: the Pi's fixed 1.8 kohm are on these two lines

**On the Pi:**

```
i2cdetect -y 3
i2ctransfer -y 3 w1@0x50 0x10 r8
```

**Command, on the Kria:**

```
hd run i2c-capture
```

Type at its prompt: `wired i2c-capture`. It enables only: silkscreen 1, silkscreen 3.

**Success:**

* The launcher prints `write to 0x50: 10`, `read 8 bytes from 0x50: ...` and the same 8 bytes the Pi printed, with `0 gaps`. Type `passed i2c-capture`.

**Failure, and what to try:**

* `i2cdetect` empty: the part's wiring, power, or the Pi's restart after the overlay.
* Gaps: raise `i2c_gpio_delay_us` in the Pi's config and restart the Pi.
* Bus stuck low: take the chip wires out and see if `i2cdetect` works alone.

### Step 13: Impersonate the part (`i2c-clone`)

Time: 20 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `i2c-capture`.

**Risk.** Low to medium. The chip's pins are open drain from before they are mapped: the launcher sets OD_MASK and releases both lines first. If that went wrong, 220 ohm limits the fight with the Pi's pull-up or the Pi's low.

**Remove first:**

* The I2C part, all four wires: an unpowered part can load the bus

**Before you start.** Take the part off the bus, all four wires. An unpowered part left on the bus can pull the lines low through its protection diodes. The launcher serves the register image saved by the last step. This is the demo in docs/reverse-engineering.md, with a real master on the wires. `i2c_device` stretches SCL while Python answers.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 9 (GND) | a24 |
| Pi pin 6 (GND), first of the Pi wires | b24 |
| J2 silkscreen 1 (SDA) | a2 |
| 220 ohm | b2 to b6 |
| Pi pin 3 (GPIO2, SDA) | a6 |
| J2 silkscreen 3 (SCL) | a12 |
| 220 ohm | b12 to b16 |
| Pi pin 5 (GPIO3, SCL) | a16 |

**Must stay disconnected:**

* No wire between the Kria's 3.3 V (J2 silkscreen 11, 12) and anything on the Pi
* Pi pins 2 and 4 (5 V): nothing on them, ever
* The I2C part: on the bench, wired to nothing

**On the Pi:**

```
i2cdetect -y 3
i2ctransfer -y 3 w1@0x50 0x10 r8
```

**Command, on the Kria:**

```
hd run i2c-clone
```

Type at its prompt: `wired i2c-clone`. It enables only: silkscreen 1, silkscreen 3.

**Success:**

* `i2cdetect` shows the address with the part on the bench, and the Pi reads the same 8 bytes. Type `passed i2c-clone`.

**Failure, and what to try:**

* Same address, wrong bytes: the part may use 2-byte memory addresses (24C32 and larger). `pe.i2cdev` models an 8-bit register pointer, so it can't clone those.
* `i2ctransfer` errors at random: slow the bus further; a Python hiccup holds SCL for as long as it lasts, and that is stretching, which `i2c-gpio` should wait for.

### Step 14: Change a register on its way to the Pi (`i2c-mitm`)

Time: 10 min (estimate). App: protoemu or protoemu40 or protoemu48. Needs: `i2c-clone`.

**Risk.** As `i2c-clone`.

**Before you start.** Same wiring as `i2c-clone`. The launcher inverts the lowest captured register on its way to the Pi. In the sense of docs/reverse-engineering.md, the host changes the value; the real part isn't on the bus, so this is the clone with an edit, not a splice into a live bus.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 9 (GND) | a24 |
| Pi pin 6 (GND), first of the Pi wires | b24 |
| J2 silkscreen 1 (SDA) | a2 |
| 220 ohm | b2 to b6 |
| Pi pin 3 (GPIO2, SDA) | a6 |
| J2 silkscreen 3 (SCL) | a12 |
| 220 ohm | b12 to b16 |
| Pi pin 5 (GPIO3, SCL) | a16 |

**Must stay disconnected:**

* No wire between the Kria's 3.3 V (J2 silkscreen 11, 12) and anything on the Pi
* Pi pins 2 and 4 (5 V): nothing on them, ever
* The I2C part: on the bench, wired to nothing

**On the Pi:**

```
i2ctransfer -y 3 w1@0x50 0x10 r8
```

**Command, on the Kria:**

```
hd run i2c-mitm
```

Type at its prompt: `wired i2c-mitm`. It enables only: silkscreen 1, silkscreen 3.

**Success:**

* The Pi's 8 bytes equal the capture's except one, inverted, the register the launcher named. Type `passed i2c-mitm`.

**Failure, and what to try:**

* As `i2c-clone`.

### Step 15: USB with the Pi as a second host (`usb-pi`)

Time: 30 min (estimate). App: protoemu48. Needs: `rails`, `pi-prep`.

**Risk.** High if done carelessly. The red VBUS wire now sits on a live 5 V port, and 5 V on a J2 signal pin exceeds the FPGA's 3.85 V absolute maximum (3.3 V bank). It stays taped, and you check that before you type the phrase. The black GND wire is the shared ground here: on the Kria's own port the two boards' grounds were one board, so USB needed no ground wire. With the Pi, the data lines have no return without it.

**Before you start.** Pi off and unplugged. Clear the breadboard's lower half except the 10 ohm ends, and J2. Look at the red wire's tape.

Put back the three 1 kohm if an earlier run took them. This is the full-speed wiring as it passed on the Kria's own port on 2026-10-30, with one added wire: the black one. Load `protoemu48` when asked.

**Wiring, in this order:**

| From | To |
|---|---|
| USB cable black wire, untaped (GND) | a24 |
| J2 silkscreen 9 (GND) | b24 |
| J2 silkscreen 1 (D+) | a30 |
| J2 silkscreen 3 (D-) | a20 |
| J2 silkscreen 11 (3.3 V) | i3 |
| USB plug, last, Pi powered off | any USB-A port on the Pi |

**Must stay disconnected:**

* USB red wire (VBUS, 5 V): taped, connected to nothing
* The Kria's USB ports: nothing in them
* J2 silkscreen 5 and 7: empty, they get a low pulse in the wiring check
* Any other wire from the Pi

**On the Pi:**

```
lsusb -d 1209:5634
dmesg | tail -n 20
```

**Command, on the Kria:**

```
hd run usb-pi
```

Type at its prompt: `vbus taped wired usb-pi`. It enables only: silkscreen 1, silkscreen 3, silkscreen 5, silkscreen 7.

**Success:**

* With `hd run usb-pi` waiting at its prompt, on the Pi: `lsusb -d 1209:5634` lists the device and `dmesg` says `new full-speed USB device`. A second host, a second hub, through the Pi's own USB chip. Type Enter at the launcher to detach, then `passed usb-pi`.
* Now, before anything else: USB plug out of the Pi, and `hd run usb-off`. Typing this step's phrase took back `usb-off`, so no later step runs until you have repeated it, because the USB wiring is on J2 again.

**Failure, and what to try:**

* `no pulled-up pin`: the launcher enabled nothing but short low pulses. Check `f11` to `f22` and the 1 kohm legs.
* `device descriptor read/64, error -71` on the Pi: answers garbled: D+ and D- wires, or the black wire.
* `error -32` lines before the chip started are the hub probing a pulled-up line with no firmware, as on the Kria; not a failure.

### Step 16: Receive with resistors alone (`rx`)

Time: 45 min (estimate). App: protoemu40 or protoemu. Needs: `tx-pi`.

**Risk.** The riskiest step on the pins, which is why it is late. A real 10BASE-T signal swings about +-2.5 V, and it goes below ground: an FPGA pin takes -0.3 V at most all the time. The divider below keeps the pad at about 0.27 to 1.93 V (estimate, unmeasured, no scope). If the termination falls out, the swing can double and the pad could reach about -0.57 V (estimate), past the -0.55 V absolute minimum, so the termination goes in before the cable and stays. `docs/eth10-receiver.md` says to scope these conditions first; you can't. That is why this is an attempt the launcher makes you accept in words, and why it may be skipped. It is an FPGA pin at stake here, on a SOM, not a loose part.

**Remove first:**

* USB plug out of the Pi first, before anything else here
* USB cable's black wire out of a24, and taped
* Jumper J2 silkscreen 1 to a30: remove both ends
* Jumper J2 silkscreen 3 to a20: remove both ends
* Jumper J2 silkscreen 11 to i3: remove both ends
* Jumper J2 silkscreen 9 to b24: remove both ends
* Every UART, SPI and I2C wire and part
* The three 1 kohm from the USB wiring (j3-j7, i7-i11, g7-g11): keep them; photograph first

**Before you start.** Read Receive: what the resistor-only attempt is, first. Pi off for the rewiring. Everything under Remove first comes off in that order, USB plug first: transmit uses the pins that were D+ and D-. If `usb-pi` was run, the launcher refuses this step until `hd run usb-off` is repeated. Then build the table in order. All four 220 ohm are used: two on TX, two as termination. That only works if `tx-pi` passed with one 220 ohm per line. If it needed two per line, this step needs two more 220 ohm, which are not on the order. Stop and get them.

The transmit side must stay on: the Pi's Ethernet won't send without link pulses from the chip. Load `protoemu40` first. On the Pi, start `pi-send` after the launcher says the pulses are on; it refuses unless the Pi has link, and refuses on a network. If you have no Wi-Fi on the Pi, run it with a delay before you move the cable: `sudo nohup sh -c 'sleep 90; python3 hw_day.py pi-send --seconds 120 --gap-us 1000' &`.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 1 (TD+) | a2 |
| 220 ohm | b2 to b6 |
| 100 nF | c6 to c8 |
| J2 silkscreen 3 (TD-) | a12 |
| 220 ohm | b12 to b16 |
| 100 nF | c16 to c18 |
| RJ45 pin 3 | a8 |
| RJ45 pin 6 | a18 |
| J2 silkscreen 9 (GND) | a29 |
| 220 ohm | d25 to d29 |
| 220 ohm | e25 to e29 |
| 1 kohm | c21 to c25 |
| 1 kohm | b21 to b29 |
| 1 kohm | d17 to d21 |
| J2 silkscreen 11 (3.3 V) | a17 |
| J2 silkscreen 7 (RX in) | a21 |
| RJ45 pin 2 | c29 |
| RJ45 pin 1 | a25 |
| Patch cable: breakout to Pi eth0, last | not to the switch |

**Must stay disconnected:**

* The switch and every other network port: the 220 ohm termination is sized for the Pi's pair
* RJ45 pins 4, 5, 7, 8
* The USB plug, in any port
* Any GND wire to the Pi: RJ45 pin 2 ties the Pi's isolated pair to the Kria's GND here
* Every other switch, router or wall network socket: this wiring may only ever touch the Tenda SG105 or the Pi. Any of them could carry 48 V PoE

**On the Pi:**

```
sudo python3 hw_day.py pi-send --seconds 120 --gap-us 1000
```

**Command, on the Kria:**

```
hd run rx
```

Type at its prompt: `pin risk accepted wired rx`. It enables only: silkscreen 1, silkscreen 3, silkscreen 7.

**Success:**

* The pad reads high a few percent of the time. `30 bursts: N frames equal with a good FCS`, N at least 10. A real NIC's frames through a real cable into the chip's receive firmware, FPGA level, with no front end.
* Some `wrong` records are expected: a burst restarts core 0 in the middle of a frame. Type `passed rx`.

**Failure, and what to try:**

* The launcher says the pad sits high: the input's trip point is below 1.1 V, or a divider wire is wrong. It stops with the pins off. The levels here are outside the guaranteed input thresholds on purpose (see Receive): this is the first thing that can stop it.
* Only short records: frames aren't reaching ui0 as edges. The high level, 1.93 V (estimate), is under the 2.0 V normally guaranteed. If the trip point is above it, this circuit can't work.
* Long records, FCS never good: try the 50 MHz app `protoemu`, loaded one command at a time as in Setting up, (5 clocks per bit, the production receiver's rate), and check the Pi still has link, since that app's pins are 4 mA. Then swap RJ45 pins 1 and 2: a swapped pair inverts every bit and nothing corrects it.
* Any failure here is a normal result. Write down what it printed and stop. Don't raise the signal level to try again.

### Step 17: Mid-frame reaction on live traffic (`cut`)

Time: 20 min (estimate). App: protoemu or protoemu40. Needs: `rx`.

**Risk.** As `rx`: same wiring and the same pad levels. The HIT pin stays inside the chip; it is sampled, not mapped to a pad.

**Before you start.** Nothing moves: this is step `rx`'s wiring, listed again so you check it. The sampling trick: the chip has two cores, and this needs three jobs (match, link pulses, sampler). So core 1 sends link pulses for 3 s, then becomes the sampler for one 0.8 ms burst, then the launcher reloads it. The Pi's link should hold through that gap: receivers are usually quoted at 50 to 150 ms before dropping link (from memory of 802.3, as docs/eth10-network.md says). On the Pi, frames every 200 us, so whole ones land in the window. Prefer `protoemu` (50 MHz), where the receive margin is, if `rx` passed there.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 1 (TD+) | a2 |
| 220 ohm | b2 to b6 |
| 100 nF | c6 to c8 |
| J2 silkscreen 3 (TD-) | a12 |
| 220 ohm | b12 to b16 |
| 100 nF | c16 to c18 |
| RJ45 pin 3 | a8 |
| RJ45 pin 6 | a18 |
| J2 silkscreen 9 (GND) | a29 |
| 220 ohm | d25 to d29 |
| 220 ohm | e25 to e29 |
| 1 kohm | c21 to c25 |
| 1 kohm | b21 to b29 |
| 1 kohm | d17 to d21 |
| J2 silkscreen 11 (3.3 V) | a17 |
| J2 silkscreen 7 (RX in) | a21 |
| RJ45 pin 2 | c29 |
| RJ45 pin 1 | a25 |
| Patch cable: breakout to Pi eth0, last | not to the switch |

**Must stay disconnected:**

* The switch and every other network port: the 220 ohm termination is sized for the Pi's pair
* RJ45 pins 4, 5, 7, 8
* The USB plug, in any port
* Any GND wire to the Pi: RJ45 pin 2 ties the Pi's isolated pair to the Kria's GND here
* Every other switch, router or wall network socket: this wiring may only ever touch the Tenda SG105 or the Pi. Any of them could carry 48 V PoE

**On the Pi:**

```
sudo python3 hw_day.py pi-send --seconds 120 --gap-us 200
```

**Command, on the Kria:**

```
hd run cut
```

Type at its prompt: `pin risk accepted wired cut`. It enables only: silkscreen 1, silkscreen 3, silkscreen 7.

**Success:**

* Threshold 1000: whole frames with HIT, near 10 clocks after the field's last mid-bit edge, and about 38 us before the frame's last edge, as in docs/eth10-cut-through.md, now on a frame from a real NIC. Threshold 3000: no HIT. Evidence level: RTL in FPGA fabric, resistors, no magnetics of its own. Type `passed cut`.

**Failure, and what to try:**

* No whole frame in the window: raise the rate (`--gap-us 100`).
* `the bit count didn't line up`: the first preamble bits were lost at the pad, so the reaction figure can't be trusted; the rise before the frame's end still can.
* HIT with threshold 3000: a corrupted field, which is possible by design: this firmware decides before the FCS.

### Step 18: Real ping (`ping`)

Time: 30 min (estimate). App: protoemu40 or protoemu. Needs: `rx`, `budget`.

**Risk.** As `rx`. Also: these commands give the Pi's eth0 an address, so run them only on the isolated cable, and take the address off after.

**Before you start.** Nothing moves: this is step `rx`'s wiring, listed again so you check it. This needs `budget` passed, the `eth-stack` files, and a clock: `protoemu40` has exact transmit half cells and the stronger pins, but its receive has no margin against 802.3's jitter (a ping passed at +-12 ns on one model seed), and `protoemu` is the reverse (40/60 ns transmit half cells, 4 mA). docs/eth10-network.md: neither clock gets both ends right. Use whichever `rx` passed on.

Start the launcher, then the Pi's commands. With no Wi-Fi on the Pi, put them in a delayed script as `rx` does, sending ping's output to a file.

**Wiring, in this order:**

| From | To |
|---|---|
| J2 silkscreen 1 (TD+) | a2 |
| 220 ohm | b2 to b6 |
| 100 nF | c6 to c8 |
| J2 silkscreen 3 (TD-) | a12 |
| 220 ohm | b12 to b16 |
| 100 nF | c16 to c18 |
| RJ45 pin 3 | a8 |
| RJ45 pin 6 | a18 |
| J2 silkscreen 9 (GND) | a29 |
| 220 ohm | d25 to d29 |
| 220 ohm | e25 to e29 |
| 1 kohm | c21 to c25 |
| 1 kohm | b21 to b29 |
| 1 kohm | d17 to d21 |
| J2 silkscreen 11 (3.3 V) | a17 |
| J2 silkscreen 7 (RX in) | a21 |
| RJ45 pin 2 | c29 |
| RJ45 pin 1 | a25 |
| Patch cable: breakout to Pi eth0, last | not to the switch |

**Must stay disconnected:**

* The switch and every other network port: the 220 ohm termination is sized for the Pi's pair
* RJ45 pins 4, 5, 7, 8
* The USB plug, in any port
* Any GND wire to the Pi: RJ45 pin 2 ties the Pi's isolated pair to the Kria's GND here
* Every other switch, router or wall network socket: this wiring may only ever touch the Tenda SG105 or the Pi. Any of them could carry 48 V PoE

**On the Pi:**

```
sudo ip addr add 10.77.0.1/24 dev eth0
ping -c 5 -W 2 10.77.0.2
sudo ip addr del 10.77.0.1/24 dev eth0
```

**Command, on the Kria:**

```
hd run ping
```

Type at its prompt: `pin risk accepted wired ping`. It enables only: silkscreen 1, silkscreen 3, silkscreen 7.

**Success:**

* `5 received` on the Pi. Linux's own stack on a Pi, ARP and ping, answered by the chip's cores and Python on the Kria, through resistors. Note the round-trip time; it measures Python on the Kria. Type `passed ping`.
* Take the address off, as the last Pi command does.

**Failure, and what to try:**

* ARP never answered, `rx` count 0: look at `host` stats: `overflow` means Python is too slow, whatever `budget` said.
* Pi shows ARP replies but no ping: the 56-byte ping makes a 98-byte frame, which streams; try `ping -s 18` for a 64-byte one, which fits the FIFOs.
* Carrier sense never lets it send: if RX sits high at idle the core waits; the `20 ms` limit in docs/eth10-network.md applies.
## End of the day

1. The patch cable out of the breakout first: with J2 11 out and J2 7 still in, the bias
   is gone and the pad would follow the pair through 1 kohm.
2. `hd off`. Then the J2 wires, signal pins first, 9 and 11 last.
3. Back to the 50 MHz app `protoemu`, loaded one command at a time as in Setting up: `sudo xmutil unloadapp`, then `sudo xmutil loadapp protoemu`, then `sudo xmutil listapps`, reading each before the next.
4. To restore the USB wiring: the three 1 kohm go back at `j3`-`j7`, `i7`-`i11`, `g7`-`g11`, the
   jumper `f11`-`f22` stays, then J2 silkscreen 1 to `a30`, 3 to `a20`, 11 to `i3`. Tape the black
   wire. The USB plug goes back in the Kria.
5. Keep `watch.log`, the `watch.pcap.*` files and what each `hd run` printed. Results go into
   `fpga/kv260/README.md` with their logs. Say FPGA, say what was and wasn't measured, and
   give the failures too.

## Not verified: CHECK BEFORE WIRING

Every item below was not confirmed by a source or a measurement. Where a step depends
on one, it says so.

| What | What is known | How to settle it |
|---|---|---|
| J2 silkscreen 9 and 10 are GND | One community table, the Digilent convention by position | Step `rails`, or a meter |
| J2's rail is 3.3 V exactly | UG1089 (search excerpt only: its PDF couldn't be read here) says a 3.3 V, 100 mA supply | A meter. Carrier schematic |
| J2's signals reach the FPGA with nothing between | Undriven pins float, so no pull-ups. Series parts or level shifters on the carrier: schematic not read | Carrier schematic |
| LVCMOS33 thresholds 0.8 and 2.0 V, drive resistance | From memory; DS925's table wasn't retrieved | DS925 |
| Clamp current limit per pin on this device | Not on the DS925 page read. DS931 gives 10 mA, for a different family | DS925's full tables |
| `protoemu40` is installed and has 12 mA pins | Project notes of 2026-10-30. Absent from the repo. `preflight` checks the name; nothing can check the drive | Its build log |
| Tenda SG105 has no PoE, and will take a 10 Mbit link | Reseller listings say non-PoE, 10/100/1000, auto MDI/MDIX. Tenda's datasheet not found. Parallel detection unknown | The label; step `tx-switch` |
| Pi 3's PHY: Auto-MDIX, 10 Mbit parallel detection | LAN9514 listing for Auto-MDIX, sibling SMSC parts for parallel detection. Nothing found for a 3 B+'s LAN7515 | The model line; step `tx-pi` |
| The Pi's OS, config path, `/dev/serial0` behaviour | Assumed Raspberry Pi OS, Bookworm or later | Step `pi-prep` |
| Pin 1 of the Pi's header, physically | Official documentation gives pin numbers; its position here is by description | `pinout` on the Pi |
| RJ45 colours on a cut cable | T568A or T568B, unknown | Use the breakout |
| The I2C part's pins, voltage, address width | Not chosen yet | Its datasheet |
| `xmutil listapps` rows' middle columns | The header and each row's last two fields were read on the board on 2026-11-03; a row's type, base and Pid values were not quoted. A row without a Pid value would refuse | Step `recover`, first run |
| `xmutil listapps` doesn't touch the fabric | From dfx-mgr's design; source not read | dfx-mgr's source |
| `/sys/class/fpga_manager/fpga0/state` exists here | Expected, not seen. The guard refuses if absent | Step `recover`, first run |
| An overlay's removal takes `/fpga-region/firmware-name` away | From memory of how overlays work. The path itself is in the board's boot log | Step `recover`, first run |
| Why the Kria froze on 2026-11-03 | Cause from the board: a tool read pe_spim's address while `k26-starter-kits` held the slot. That the read hung the bus is the likely mechanism, not proved | - |
| How long a receiver keeps link with no pulses | 50 to 150 ms, from memory, as in docs/eth10-network.md | 802.3 clause 14 |
| Whether the Pi can be an SPI device for the chip's master firmware | Not found in the documentation read. So `spi` tests capture only | A real SPI part |

## Sources

The AMD, Raspberry Pi and Xilinx pages and the gist were opened. UG1089, the element14
post, the datasheet listings and the Tenda listings were read only as a search tool's
excerpts, because the pages timed out or weren't fetched: treat those as weaker.

* The Kria itself, read-only, 2026-11-03, by the orchestrator: the load error's text, `xmutil listapps`' header and slot fields, the boot log's `/fpga-region/firmware-name`, `/lib/firmware/xilinx/`, and the journal of the boot that froze.
* [Linux sysfs-class-fpga-manager ABI](https://gbmc.googlesource.com/linux/+/7b2e57c26d45d55aa00d9f2904db12633596ffdb/Documentation/ABI/testing/sysfs-class-fpga-manager): the `state` values, from a search excerpt. [dfx-mgr, a mirror](https://gitee.com/mirrors_Xilinx/dfx-mgr).
* [DS925, absolute maximum ratings](https://docs.amd.com/r/en-US/ds925-zynq-ultrascale-plus/Absolute-Maximum-Ratings): HD I/O VIN -0.55 V to VCCO + 0.55 V, VCCO 3.4 V maximum.
* [DS925, overshoot and undershoot](https://docs.amd.com/r/en-US/ds925-zynq-ultrascale-plus/VIN-Maximum-Allowed-AC-Voltage-Overshoot-and-Undershoot): the -0.30 V and -0.45 V figures.
* [DS925, DC characteristics](https://docs.amd.com/r/en-US/ds925-zynq-ultrascale-plus/DC-Characteristics-Over-Recommended-Operating-Conditions): 4.75 pF die input capacitance.
* [UG1089 v1.4, KV260 user guide](https://ee.farnell.com/site/binaries/content/assets/common/product-family-documents/amd-sk-kv260-g/amd-sk-kv260-g-um1.pdf): the Pmod's 3.3 V, 100 mA supply, as quoted by a search result.
* [element14 post with a J2 pin table](https://community.element14.com/technologies/fpga-group/b/blog/posts/blinking-a-led-with-pynq-in-kria-kv260-kr260) and [a KV260 pin gist](https://gist.github.com/tonosaman/988461ecfafe712c9b35667a2adf9c72): community sources for the rails and package pins. The second agrees with `kv260.xdc` pin for pin.
* [KV260 BIST setup](https://xilinx.github.io/kria-apps-docs/kv260/2022.1/build/html/docs/bist/docs/setup_kv260.html): AMD's own loopback pairs on J2.
* [Raspberry Pi hardware documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html): pin numbers, 3.3 V, not 5 V tolerant, 16 mA, 50 mA, fixed 1.8 kohm on GPIO2 and 3.
* [Raspberry Pi 3 B+ hardware notes](https://raspberry-projects.com/pi/category/pi-hardware/raspberry-pi-3-model-b-pi-hardware): LAN7515 in place of the LAN9514. A third-party page.
* [LAN9514 listing](https://html.alldatasheet.com/html-pdf/885223/SMSC/LAN9514/1116/11/LAN9514.html): Auto-MDIX.
* [LAN83C185](https://html.alldatasheet.com/html-pdf/85489/SMSC/LAN83C185/5275/60/LAN83C185.html) and [LAN9303](https://html.alldatasheet.com/html-pdf/312193/SMSC/LAN9303-ABZJ/10159/95/LAN9303-ABZJ.html): squelch 300 / 420 / 585 mV, transmit 2.2 to 2.8 V. [LXT9863](https://html.alldatasheet.com/html-pdf/116836/INTEL/LXT9863/7326/47/LXT9863.html): the same transmit range, on a 100 ohm load.
* [Tenda SG105 at Staples](https://www.staples.ca/products/2761715-en-tenda-sg105-5-port-gigabit-switch-net-td-sg105) and [at Connectec](https://connectec.uk/networking/network-devices/network-switches/tenda-sg105-5-port-gigabit-desktop-switch): PoE no, store and forward, auto MDI/MDIX. Resellers, not Tenda.
* [Adafruit on software I2C](https://learn.adafruit.com/raspberry-pi-i2c-clock-stretching-fixes/software-i2c), [a user report of `i2c_gpio_delay_us`](https://github.com/Ribbit-Network/ribbit-network-frog-hardware/issues/61), and [rppal's I2C notes](https://docs.rs/rppal_w_frontend/latest/rppal_w_frontend/i2c/index.html) on the BCM283x's clock stretching bug.
