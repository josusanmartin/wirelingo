# v3 study: small ISA additions against a respin

Branch `v3-study` (from `main` 9fb4f57). The question: would a final hardware
revision with a few small ISA/RTL additions improve Wirelingo enough to
justify a full re-signoff before 2027-01-18? Each candidate was implemented in
the golden model, assembler and timing checker of this branch only, the
affected firmware was rewritten to use it, and the model tests were run.
Hardware cost comes from a yosys prototype of `pe_core` (in
`syn/v3-study/`, never in `src/`). No RTL design change, no harden, no
`core_route.sh` run.

> On `main` this document stands alone: the model, assembler, timing-checker
> and firmware prototypes it measures, and `syn/v3-study/`, stay on the
> unmerged `v3-study` branch, so `main`'s ISA is unchanged. The firmware
> race fix it recommends ("Act now", below) is on `main` separately, with
> `test_bus_reset_at_any_phase`.

## Bottom line

**No respin for these additions.** The measured gain is small: 37 of the
library's 1269 words (2.9 %), one or two clocks on full-speed USB turnarounds
that already pass with at least 5 clocks to spare, and two new capabilities.
One of them, an autonomous USB device with a data endpoint, is a real step;
the other, an in-engine Ethernet FCS check, duplicates a host-side check
that already works. Against that stands re-closing timing at this congestion:
v2c to t48ba took 25–30 harden runs, and a formally equivalent RTL change
(`t48-rtl`) has already turned a clean global route into 400 overflows
(docs/physical.md).

**If a respin happens for another reason**, include the cheap bundle and
nothing else: table read into the OSR, bank-relative PC writes, a 16-bit
compare (CMPW), `jmp !t` and a core-local `flag tog`. It is about +1 % of
core area and 1 flop, it is all inside the core, and it adds no chip-level
wires. Leave out CRC-32, the multi-bit CRC tap, a second link register and
every line-unit change.

**Act now, firmware only:** usb_enum on `main` can miss a USB bus reset, and
usb_fs_dev and usb_ls_dev share the same idle code (below,
[a v2 firmware race](#a-v2-firmware-race-found-on-the-way)). The fix is free:
one WAIT changes kind.

### Ranked

Benefit is what the model measured; cost is the yosys area of the prototype
(`syn/v3-study/results.txt`; the synthesis noise is about ±0.4K µm²; baseline
core 103.2K µm²) plus a routing judgement. Wire was not measured: that needs
`core_route.sh`, which was not run on the shared machine.

| Rank | Addition | Words saved (library) | Critical-path clocks | Capability unlocked | Area / flops per core | Routing risk | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | **Table read** `mov osr, [rN]` (+ bank-relative PC) | 24 (usb_enum 237→213) | — | USB device with a data endpoint on one core (with it alone: 250/256 words; v2: 280) | +0.5–1.3K (≈ +1 %), 1 | low–moderate: local, but 32 more loads on the SRAM's DOUT, where the slow-corner critical path starts | bundle |
| 2 | **CMPW** `cmp rN, #imm16` | 8 (4 USB programs) | −1 to −2 on USB FS turnarounds (not binding) | 6-instruction SFD search in eth10_rx_fcs (7 without) | ≈ 0 (noise), 0 | low: shares the ALU's register selector | bundle |
| 3 | **`jmp !t`** | 5 (+1 in post-freeze lin_responder) | −1 on the token→IN paths (not binding) | — | ≈ 0, 0 | negligible | bundle |
| 4 | **`flag tog k`** (core-local) | 3 (only in usb_enum_ep1) | — | per-endpoint DATA toggle in one word | ≈ 0, 0 | negligible: a set or clear, no new wires | bundle |
| 5 | Bank-relative MOV/OUT PC | 0 | — | computed jumps and tables on core 1 | ≈ 0 (−2 mux bits) | negligible | bundle (makes the table read a plain jump) |
| 6 | CRC-32 (CRCCTRL W32) | 0 (new program +17) | — | 802.3 FCS checked in the engine (eth10_rx_fcs) | +2.1K (+2 %), 17 | moderate: +1.5 % core wire measured in physical.md; the CRC source and host read paths widen | leave out |
| 7 | Second link register | 0: no program nests | — | — | +0.5K, 10 | low | leave out |
| 8 | Multi-bit CRC tap (≤ 8 bits per shift) | 0 | USB loops 24→3 per byte, not binding | — | +9.0K (+8.7 %), 1 | high: ~1000 cells in the congested core | leave out |
| — | Line unit: frame markers / flush, a line unit for core 1, autopush at a frame end, data RAM, more registers, general 16-bit ALU immediates | ≤ 1 each | — | robustness only, or nothing measured | not prototyped; see [Rejected](#rejected-without-a-prototype) | moderate–high | leave out |

Across the whole library (22 programs, plus the 9 post-freeze programs on
`protocols2`), only the USB programs are close to the 256-word bank: usb_enum
used 241, everything else has room.

## Candidates (isa.md style)

Every addition takes encoding space that v2 reserves or ignores, so every v2
program runs unchanged. The whole model suite passes on this branch: 484
tests (main: 439), 2026-10-13.

| Addition | Encoding | Semantics |
|---|---|---|
| CMPW | LDI (op 8) with `[18]` = 1: `[17:16]` imm[15:14], `[15:14]` rd, `[13:0]` imm[13:0] | `Z = (R[rd] == imm16)`, `C = (R[rd] < imm16)`; no register write. The assembler emits it for `cmp rN, K` with K ≥ 0x2000, or for `cmpw` |
| `jmp !t` / `call !t` | JMP/CALL cond 0 (always) with `sel` = 1 | taken when T == 0 (other `sel` values: always, as in v2) |
| Table read | MOV with op 3 (reserved, "behaves as none"): `mov osr, [src]` | `OSR = IMEM[{PC[9:8], src[7:0]}]`, `OSR_CNT = 0`. The core's own bank only. The word arrives on the next clock, which the core spends on it: one more tick when that clock is a tick (always at CLKDIV 1) |
| Flag toggle | FLAG with `[17]` = 1: `flag tog k` | a set of `F[k]` if this core sees it 0, else a clear (normal set/clear precedence) |
| Bank-relative PC | MOV/OUT to PC | `PC = {PC[9:8], value[7:0]}` (v2: `value[9:0]`; a core faults outside its bank anyway) |
| CRC-32 | CRCCTRL `[23]` W32 | 32-bit CRC register, `POLY = 0x04C1 << 16 \| POLY[15:0]`; CRC reads and writes are 32 bits wide |
| Multi-bit tap | CRCCTRL `[22]` MULTI | IN/OUT of 2–8 bits (and `in line, n ≤ 8`) feed all their bits, in time order (shifting right: bit 0 first) |

Why these layouts:

* **CMPW** keeps rd in the ALU's `[15:14]`, so it uses the same register
  selector as ALU and WAIT kind 7. With LDI's `[17:16]` it would need a second
  selector, and that alone cost about 1 % of core wire for kind 7
  (physical.md). The host's token-filter patch writes the scattered immediate
  (`isa.with_imm16`). Only a compare is needed: across all 31 programs a
  16-bit immediate was needed once for anything else (`and` in
  lin_commander).
* **The table read** reuses MOV PC's jump path for the address (identical
  with bank-relative PC writes) and makes the PC register continue at
  `pc + 1`, so the SRAM address mux stays v2's. The cost is one flop and a
  32-bit mux at the OSR's D input from the SRAM output. OSR only: allowing
  R0–R3 and ISR as well costs +2.9K µm² instead of +1.3K, and one word per
  register load is enough for every use found (`mov osr, [r]` then
  `mov r, osr`).
* **Flag toggle** inside the core needs no new wires to the chip's flag
  register (a chip-level toggle vector would add 8 per core). It is not
  atomic against another core's write in the same cycle, which no program
  does.
* **CRC-32** fixes the top half of the polynomial (0x04C1), as the earlier
  physical.md prototype did.

The model (`tools/pe/sim.py`), assembler (`tools/pe/asm.py`, plus a `.data`
directive for table words), timing checker (`tools/pe/timing.py`) and
`test/model/test_v3_isa.py` (14 tests) implement exactly the table above.

## What each one buys, measured

Word counts are from assembling `firmware/` on `main` and on this branch;
turnarounds are the static checker's bounds at the pads (`adjust` +3..4) for
the annotated programs, at full speed (48 MHz, 4 clocks per bit; the limit
is 26 clocks).

| Program | main | v3 | Additions used |
|---|---|---|---|
| usb_enum | 241 | **213** | table read −24, CMPW −2, `!t` −2 |
| usb_fs_dev | 131 | 128 | CMPW −2, `!t` −1 |
| usb_ls_dev | 119 | 116 | CMPW −2, `!t` −1 |
| usb_ls_rx | 143 | 141 | CMPW −2 |
| can_tx | 122 | 121 | `!t` −1 |
| the other 17 | 513 | 513 | — |
| **library** | **1269** | **1232** | −37 (2.9 %) |
| usb_enum_ep1 (new) | 280 in v2 instructions: does not fit | **243** | table read, CMPW, `!t`, `flag tog` |
| eth10_rx_fcs (new) | not possible (16-bit CRC tap) | 27 | CRC-32, CMPW |

| Deadline (clocks, pads) | main | v3 |
|---|---|---|
| usb_enum `in_ta` (IN → DATA/STALL) | 10–15 | 9–14 |
| usb_enum `in_ta_any` (incl. the lost-ACK commit) | 10–21 | 9–20 |
| usb_enum `data_ta` (DATA → ACK) | 11–20 | 9–18 |
| usb_fs_dev `ack_ta` | 8–16 | 8–15 |
| usb_fs_dev `in_nak_ta` / `in_data_ta` | 10–15 / 12–17 | 9–13 / 10–15 |
| usb_enum_ep1 `ep1_ta` (IN to endpoint 1) | — | 16–22 (measured 3.1–5.4 bit times) |
| uart_tx, i2c_master | unchanged | unchanged |

(These are the study's counts, from `main` when it was written. The
programs have changed since: with the got_in guard, branch `usb-guard`
2026-11-04, `usb_fs_dev` is 142 words and `usb_ls_dev` 130, and usb_fs_dev's
`in_nak_ta` / `in_data_ta` are 10–16 / 13–18 clocks; `pe.timing`.)

None of the deadlines changes status; every one already had margin. The
cycle gains are real but buy nothing today.

**Post-freeze programs** (branch `protocols2`, written in parallel with this
study: 653 words in the 9 programs of its first commit). They have one
`jmp t` over a `jmp` (lin_responder, −1 word with `!t`) and one `ldi` + `and`
with a 16-bit mask (lin_commander, which CMPW doesn't cover). Their draft
`docs/post-freeze-protocols.md` lists ten ISA limitations; against this
study:

| post-freeze limitation | v3 candidate | effect |
|---|---|---|
| 1. no `jmp !t` (nec_rx was first written with it) | `jmp !t` | fixed |
| 2. 13-bit ALU immediates (LIN mask 0xF300, record tag 0x2000) | CMPW | compares only. A second 16-bit op (`and`/`or`) fits in opcode 14 with CMPW's layout, for 2 words in the whole library |
| 3. four registers (LIN keeps its bit time in the CRC register) | — | not small |
| 4. no table lookup (LIN PID parity ~25 ALU instructions; DShot GCR decoding left to the host) | table read | a lookup becomes `mov osr, [r]` plus 1 word per entry (or 4 entries per word plus extraction): makes in-engine GCR decoding practical; LIN parity about breaks even |
| 5–9. immediate shift counts, 16-bit registers, one tick rate, fractional steps, blocking PUSH | — | not addressed |
| 10. one link register: "no problem in practice" | two-entry link stack | agrees with rejecting it |

### Capability 1: an autonomous USB device with a data endpoint

docs/usb-enumeration.md left data endpoints open: "The descriptor fits in the
15 spare words; the token dispatch and data path need room made first."
`firmware/usb_enum_ep1.pasm` is usb_enum plus an interrupt IN endpoint 1
(wMaxPacketSize 8, bInterval 10 ms). Every IN to it, once configured, gets a
2-byte DATA0/DATA1 packet with P16–P31: the five `ui` inputs and the eight
virtual pins, which core 1 can write. So the chip is a USB input device with
no host CPU at all. It is 243 of 256 words.

* The descriptors are 4 bytes per table word (`mov osr, [r1]`) instead of 2
  per two-word `ldi`/`call` entry: 36 bytes take 10 words, not 36.
* Endpoint 1's token filter is r3 XOR 0x10D: the CRC5 is linear, so one
  constant converts endpoint 0's filter to endpoint 1's at any address. No
  register is needed.
* Its DATA toggle is a flag (`flag tog` on the host's ACK); SET_CONFIGURATION
  resets it to DATA0, and a lost ACK repeats the toggle.
* The same firmware written with v2 instructions
  (`syn/v3-study/usb_enum_ep1_v2isa.pasm`) is **280 words**. Dropping the
  removable requests listed in usb-enumeration.md (11 words) still leaves
  269. With the table read alone (no CMPW, `!t` or `flag tog`) it is 250.
* Tests (`test/model/test_v3_usb_ep1.py`, 14): reports of random inputs at
  low and full speed with jitter and clock offset, a lost host ACK, damaged
  packets and lost handshakes mixed over both endpoints, SET_CONFIGURATION
  0/1, endpoints 1 and 2 ignored when not configured or absent, and the host
  transport idle throughout. The static checker bounds endpoint 1's turnaround
  at 16–22 clocks (limit 26). The model measured 17–18 ticks inside that bound
  and 3.1–5.4 bit times on the bus.

v2 has other routes to the same device. Core 1 could hold the descriptors
and feed core 0 over virtual pins (usb-enumeration.md: words on both sides
and a handshake on the turnaround path), or the host could stage reports, as
usb_fs_dev already does. v3 makes it one core with no host.

### Capability 2: the Ethernet FCS checked in the engine

`firmware/eth10_rx_fcs.pasm` (27 words) finds the SFD itself: the 16 newest
bits, newest first, are 0xAAAB only at the SFD's end. It then presets the
CRC-32, queues only DA..FCS and reports FCS good or bad in the trailer; link
pulses leave no record. At 50 MHz (5 clocks per bit) the 6-instruction
preamble loop falls behind the line by at most 13 of the line unit's 32 queued
bits, and the 3-instruction frame loop catches up. Without CMPW the loop
would be 7 instructions (about 25 bits queued). At 40 MHz it would fill the
queue, so this program is for 50 MHz. Tests (`test/model/test_v3_eth_fcs.py`,
11): an external sender with 802.3's ±13.5 ns jitter and ±200 ppm, single
flipped bits anywhere in the frame (FCS bad), a 1500-byte frame, and core 1's
eth10_tx to core 0 with link pulses. This answers physical.md's open point
("the firmware half doesn't fit"): the SFD search fits. It still only
replaces a host-side CRC that works today, and costs about 1.5 % of core wire.

## Hardware cost

`syn/v3-study/synth.sh [V3_…]` synthesizes `syn/v3-study/pe_core_v3.sv`
(`src/pe_core.sv` with each addition under `ifdef`) as core 0 with 8-deep
FIFOs, with yosys and abc to the IHP SG13 typical library. Five seconds per
run. Raw output is in `syn/v3-study/results.txt`:

| Variant | Cells | Flops | Area (µm²) | Δ |
|---|---|---|---|---|
| v2 baseline | 7407 | 430 | 103,243 | — |
| CMPW (shared-selector layout) | 7349 | 430 | 103,496 | +0.25K |
| `jmp !t` | 7352 | 430 | 102,811 | −0.43K (noise) |
| table read, OSR only | 7549 | 431 | 104,585 | +1.34K |
| table read, R0–R3/ISR/OSR | 7581 | 434 | 106,121 | +2.88K |
| bank-relative PC | 7321 | 430 | 102,916 | −0.33K (noise) |
| `flag tog` (core-local) | 7403 | 430 | 102,985 | −0.26K (noise) |
| two-entry link stack | 7320 | 440 | 103,777 | +0.53K |
| CRC-32 | 7495 | 447 | 105,306 | +2.06K |
| multi-bit tap | 8404 | 431 | 112,286 | +9.04K |
| **bundle** (CMPW, `!t`, table read, bank-relative, tog) | 7576 | 431 | 104,202 | **+0.96K (+0.9 %)** |
| bundle + CRC-32 | 7687 | 448 | 106,316 | +3.07K (+3.0 %) |

Chip-level: nothing. The bundle needs no new port on `pe_core`: the table
word comes from the core's own `instr` input, and the toggle is a set or a
clear.

**Routing.** The design is limited by its one horizontal layer (t48ba:
Metal3 63.9 %, 12 overflows after global routing). Earlier measurements of
blocks inside the core (physical.md, `core_route.sh`) set the scale: v2's
WAIT kind 7 added 1.6 % of core wire and needed the full harden; a second
16-bit register selector costs about 1 %; the 32-bit MOV bit-reverse mux is
worth 1.6 %; CRC-32 measured +1.5 %. By that yardstick the bundle should be
about +1.5–2.5 % of core wire, mostly the table read's 32-bit OSR input.
That is the size of the kind-7 change, which routed after retuning. CRC-32
would add another 1.5 %, and the multi-bit tap's ~1000 cells probably several
percent. These are estimates by analogy; no wire was measured.

**Timing.** The slow-corner critical paths start at the SRAM outputs (decode
→ pin registers and R0–R3/CRC/PC). The table read gives every DOUT bit one
more load (the OSR mux), and DOUT load limits were already an issue in final4.
CMPW and `!t` add a gate in front of the ALU immediate and the condition mux.
Neither is on the SRAM address path, but t48ba's slow-corner margin was won
with clock skew and 25–30 placements. Any of these changes reopens that.

## Rejected without a prototype

* **Line-unit frame markers / an RX flush.** The firmware rule ("consume a
  frame's end before the next begins") holds on every path, and no program
  would shrink. This is robustness only, and it is a line-unit change, near
  the I/O pins where congestion was before.
* **A line unit (or LINE access) for core 1.** No program needs two line
  units, and core 0 already does USB alone. Its ~80 signals would cross the
  die on Metal3, the earlier `BANK_OWNER` problem in small.
* **Autopush at a frame end.** It saves at most 1 word per receiver
  (eth10_rx, usb_fs_dev, usb_ls_dev) and changes the record formats' "empty
  last word" rule.
* **Writable data RAM.** 16×16 bits is ~256 flops plus muxes, about +17K µm²
  (+17 %) per core. The table read covers every read-only use found
  (descriptors, a cloned device's register image), and nothing in the library
  needs writable tables.
* **More registers (8).** 64 flops plus wider selectors on five register
  ports, over +10K µm² per core and core-wide wires. It is the limit the
  post-freeze LIN responder hit, but it is not a small change.
* **General 16-bit ALU immediates.** One use in 31 programs (lin_commander's
  `and`); CMPW covers the rest.
* **Core-writable CRC configuration** (CRC5 for usb_enum's address filter
  instead of software): it would save about 7 words, but needs a core write
  path into the host-only configuration registers.
* **I2C command decode / program size in bytes** (pio-comparison.md): the
  decode is firmware structure. A bank-relative `out pc, 2` dispatch saves at
  most 3 ticks per command, and PIO's 16-bit encoding is not a small change.

## Risk if a respin is attempted anyway

* **Re-signoff:** each harden plus signoff is about a day, and the receipts in
  release-evidence.md (GL suite, SDF standard/v2 sets at three corners,
  precheck, autonomous FS USB with delays) are regenerated per netlist. t48ba
  needed 25–30 runs over the setting space after v2c; a new netlist starts
  that search again.
* **Placement variance:** slow-corner setup moves ±0.4 ns between placements
  of the same netlist, and global routing at ~64 % Metal3 has flipped from
  ~10 to 400+ overflows on a formally equivalent change (`t48-rtl`). The
  outcome is a gamble per run, not a function of the netlist size.
* **The strict gate:** t48ba itself fails `release_check.py` on one fanout
  item (antenna diodes on net3483). A new netlist must meet the same
  zero-exception bar from scratch.
* **Verification:** new RTL needs RTL lockstep with new coverage goals (table
  read, CMPW, `!t`, toggle), RTL tests per feature, the formal properties in
  `pe_core` (a table read writes the OSR on the clock after its tick, which
  the "a disabled core's state is frozen" assertion must allow or the RTL must
  gate), mutation definitions, and GL/SDF runs.
* **Fallback:** t48ba stays the release candidate, so a failed attempt costs
  time and runner capacity, not the tapeout.

## A v2 firmware race found on the way

usb_enum's idle loop, and usb_fs_dev's and usb_ls_dev's, wait for J with
`wait 1 pin DM timeout`, then for the packet with `wait fall DM`. An edge WAIT
counts only edges after its first attempt, and the pad reaches the core two
clocks late. So an SE0 that begins up to two clocks before the `wait fall`
is never seen. That SE0 is a bus reset, and the core stays in the `wait fall`
until the next packet, at its old address. `test_bus_reset_mid_enumeration
[configured-full]` started failing here when CMPW moved the timing by two
clocks; on `main` it passes only because its reset misses the window.
`test_bus_reset_at_any_phase` (new) starts the reset 0–5 clocks later; run
against `main`'s usb_enum it fails at offsets 1 and 2 (the device keeps
answering at address 5). A real host would see a failed enumeration and
usually reset again. `wait 0 pin DM` fixes it at no cost: it is a level, so
an SE0 already under way completes it, and `jmp !pin DP` sends it to the reset
check. The fix is in this branch for all three programs. usb_fs_dev and
usb_ls_dev share the idle code, but their reset was not separately reproduced.
This needs no respin and can go to `main` as a firmware change.

## Reproduce

```sh
cd ~/dev/protoemu-v3
PYTHONPATH=tools python -m pytest -q test/model                    # everything (~10 min)
PYTHONPATH=tools python -m pytest -q test/model/test_v3_isa.py test/model/test_v3_usb_ep1.py test/model/test_v3_eth_fcs.py
PYTHONPATH=tools python -m pe.timing firmware/usb_enum_ep1.pasm --timing firmware/usb_enum_ep1.timing -D DP=0 -D DM=1 -D TURN=4
PATH=$HOME/tools/oss-cad-suite/bin:$PATH syn/v3-study/synth.sh V3_CMPW V3_CMPW_SPLIT V3_NT V3_TBL V3_TBL_OSR V3_BANKREL V3_TOG
```

Commits, in order: the model/assembler/checker support; CMPW and `jmp !t` in
the firmware with the reset-race fix; eth10_rx_fcs; usb_enum's table and
usb_enum_ep1; this page with the synthesis prototype. Per-addition word counts
above come from those steps.
