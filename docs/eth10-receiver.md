# Ethernet receive bench circuit and bounded capture

This is a proposed, **unbuilt** receive front end for an isolated bench
experiment. Its digital interface is `ui0` (logical input pin 16), with
positive differential input high, negative input low and idle low. The
production receiver runs at **50 MHz** with five project clocks per bit.
The 40-MHz receive prototypes still fail the full jitter tests; this circuit
does not resolve that limitation or the external TX half-cell timing.

[Receiver schematic](eth10-receiver.svg) shows the signal path and named
nets. The pin table below is the wiring reference. Use a PCB with short
differential/comparator paths; solderless breadboard parasitics are not
represented by the calculations.

## Circuit and pin reference

Use a manually selected 10BASE-T source and the receive pair, normally RJ45
pins 3 and 6. Pair assignment must match the chosen source/crossover wiring.
The circuit has no auto-MDI-X, negotiation, PoE or transmit PHY. The other
transformer channel is unused. Cable shield/EMC termination is outside this
bench drawing.

| Reference | Part / value | Connections |
|---|---|---|
| T1 | Würth 749013011A, 1:1 10/100 magnetics | Cable RX+ to 11, RX− to 9; cable centre tap 10 unconnected. Board RX+ `RP` from 6, RX− `RN` from 8; board centre tap 7 to `VMID`. Other channel 1/2/3 and 14/15/16 unconnected; 4/5/12/13 are NC. |
| R1 | 100 Ω, 1% | Across `RP` and `RN`, close to T1. |
| R2, R3 | 100 Ω each, 1% | 3.3 V–R2–`VMID`–R3–GND. |
| C1, C2 | 100 nF, 1 µF | `VMID` to GND, close to centre tap. |
| R4, R5 | 1 kΩ, 4.99 kΩ, 1% | `RN`–R4–`THN`–R5–3.3 V. |
| R6, R7 | 1 kΩ, 4.99 kΩ, 1% | `RP`–R6–`THP`–R7–3.3 V. |
| U1 | TLV3502D, SOIC-8 | 1 (+INA) `RP`, 2 (−INA) `RN`, 7 (OUTA) `SIGN`; 3 (+INB) `RP`, 4 (−INB) `THN`, 6 (OUTB) `AMP_POS`; 8 VCC, 5 GND. |
| U2 | TLV3502D, SOIC-8 | 1 (+INA) `RN`, 2 (−INA) `THP`, 7 (OUTA) `AMP_NEG`; unused B: 3 GND, 4 `VMID`, 6 unconnected; 8 VCC, 5 GND. |
| U3 | SN74LVC1G32DBV, OR | 1 `AMP_POS`, 2 `AMP_NEG`, 3 GND, 4 `AMP`, 5 VCC. |
| D1, R8 | BAT54H, 220 Ω | `AMP` to diode anode; cathode through R8 to `ENV`. Verify the selected footprint's anode/cathode orientation. |
| R9, C3 | 33 kΩ, 47 pF C0G | Each from `ENV` to GND. |
| U4 | SN74LVC1G17DBV, Schmitt buffer | **1 NC, 2 `ENV`**, 3 GND, 4 `ACTIVE`, 5 VCC. |
| U5 | SN74LVC1G08DBV, AND | 1 `SIGN`, 2 `ACTIVE`, 3 GND, 4 `RX_LOGIC`, 5 VCC. |
| R10, R11 | 47 Ω, 100 kΩ | `RX_LOGIC`–R10–`ui0`; R11 from `ui0` to GND. |
| C4–C8, C9 | 100 nF per U1–U5, 1 µF bulk | VCC–GND bypass at each IC; local bulk at receiver supply entry. All VCC nets are 3.3 V. |

The transformer mapping is from its
[Rev. 006.000 drawing](https://www.we-online.com/components/products/datasheet/749013011A.pdf).
Comparator pins and limits come from the
[TLV3501/TLV3502 datasheet](https://www.ti.com/lit/ds/symlink/tlv3501.pdf).
The logic pin maps are specific to **DBV** packages:
[OR](https://www.ti.com/lit/ds/symlink/sn74lvc1g32.pdf),
[Schmitt buffer](https://www.ti.com/lit/ds/symlink/sn74lvc1g17.pdf),
[AND](https://www.ti.com/lit/ds/symlink/sn74lvc1g08.pdf).
The buffer's input is pin 2, unlike the two-input gates.

The sign comparator follows the differential waveform. The other two
comparators detect magnitude in either polarity; their OR output charges
the envelope. The Schmitt buffer holds `ACTIVE` through zero crossings,
then discharges during idle. U5 gates `SIGN` with `ACTIVE`, providing idle
low even if the sign comparator chatters near zero. An isolated link pulse
can assert the envelope; software ignores short records and requires a
preamble, valid frame length and FCS for a returned frame.

## Calculated starting values

These are nominal circuit estimates, not measured reception limits.
At idle the two window branches load the centre-tap bias. Including those
branches gives `VMID ≈ 1.677 V`, rather than 1.650 V. R3 draws about 16.77 mA;
the low-impedance bias intentionally consumes power to avoid a large DC
shift from the window resistors. Two dual comparators add roughly 12.8 mA
using their typical per-channel current. Budget at least this approximately
30-mA load plus logic/dynamic current, and measure supply drop and heating.

For `β = 1000 / (1000 + 4990)`, the ideal symmetric differential trip
amplitude is

`Vtrip = 2 β (VCC − VMID) / (2 − β) ≈ 0.296 V`.

The window branches give an approximate differential termination of
`100 Ω || (2 × 5.99 kΩ) ≈ 99.17 Ω`. A simplified unloaded-bias calculation
with 3.3-V ±5% supply and 1% resistor corners gives roughly 0.278–0.325 V
before comparator offset, hysteresis, loading and cable effects. It is not
a compliance bound. The comparator's hysteresis is typically 6 mV; its
25°C input offset can reach ±6.5 mV. The window input resistance is about
833 Ω; 2-pF typical input capacitance alone gives a roughly 1.7-ns pole.
Layout, transformer and source impedances also matter.

The envelope discharge time from its fitted 47 pF is 1.55 µs. Adding a
4.5-pF typical buffer input, up to 10-pF diode capacitance and an illustrative
3-pF layout estimate gives about 64.5 pF total: a 14-ns charging RC and
2.13-µs discharge RC. The
[BAT54H datasheet](https://assets.nexperia.com/documents/data-sheet/BAT54H.pdf)
lists voltage drop and capacitance; output resistance and diode conduction
make this RC estimate approximate. The Schmitt datasheet's 3-V thresholds
are not guaranteed thresholds at 3.3 V. Measure the actual assertion and
release times; require acquisition within the surviving preamble and release
well before the 9.6-µs inter-frame gap.

At a ±2.8-V differential bench input the nominal individual inputs are
about 0.277–3.077 V. This calculation does not cover transformer ringing,
source common mode, power-off hot-plug or fault protection. Scope those
conditions before connecting the ASIC. Input protection, EMC and production
power sequencing need a separate hardware design review. Constant sign-path
delay can shift an entire packet; delay variation and pulse-width distortion
reduce the receiver's jitter margin and must be measured at `ui0`.

## Finite capture on the board

Copy `protoemu.py`, `protoemu_fw.py`, `bringup.py` and `eth10_rx.py`, plus
the optional PIO/DMA transport files if used. Use an actual 50-MHz project
clock, verified SDK version and one configuration writer. Reserve core 0,
its instruction bank/FIFOs, the line unit and `ui0`. Capture does not stop
core 1, but the caller must prevent another task from configuring the chip
or changing its clock. The high-rate quad transport reserves its additional
pins and resources as described in [board-transport.md](board-transport.md).

```python
import bringup
from eth10_rx import Capture

pe = bringup.setup(clock_hz=50_000_000)
# Attach the separately reserved/validated quad backend here when used.
rx = Capture(pe)
frames = rx.capture(timeout_us=100000, max_frames=1)
print(len(frames[0]), frames[0].hex())  # Destination through validated FCS.
```

Coordinate the external source to transmit after RX is armed. This example
does not signal readiness to another host or generate traffic. A burst of
64- and 1518-byte frames needs about 1.29 ms on the wire, before service and
arming overhead; choose a deadline accordingly. `bringup.eth10_receive()`
provides the same bounded one-shot operation without retained diagnostics.

Capture drains raw 24-bit records while receiving, then stops core 0 and
the line unit **before** byte conversion and CRC work. Returned frames must
have sufficient alternating preamble, SFD, a 64..1518-byte destination-through-
FCS length and valid FCS. VLAN-sized extensions are not supported. Storage
is bounded to 16 completed frames and one 509-word pending record. Invalid
markers/counts are discarded; short link-pulse records are counted. Missing
trailers, overflow, stopped/faulted core, clock drift or timeout raise instead
of returning a partial frame. `rx.raw_frames` and `rx.records` retain diagnostic
records; they are not validated frames. Cleanup failure preserves the original
error and requires transport/reset recovery before another capture.

The eight-entry FIFO represents about 19.2 µs of line data; the line queue
adds limited buffering. Raw data alone needs 13.33 Mbit/s of host payload,
plus status/headers and software overhead. Bit banging has no measured
ability to keep up. PIO/DMA implements a faster transport, but real board
throughput and worst service latency remain unmeasured. Finite capture
avoids doing CRC work between packets; it does not establish continuous
network reception or simultaneous TX/RX performance.

## Digital tests and hardware qualification

```sh
PYTHONPATH=tools python -m pytest -q test/model/test_eth10_board.py
make -C test COCOTB_TEST_MODULES=test_eth_service \
  SIM_BUILD=sim_build/my-eth-service COCOTB_RESULTS_FILE=my-eth-service.xml
python test/check_results.py test/my-eth-service.xml 2
```

The model tests independently generate record bits/FCS and check decode,
padding, bounds, clock/timeout and cleanup behavior. The RTL suite runs the
actual capture driver over quad SPI while independently driving 64/1518-byte
frames with ±13.5-ns transition jitter and −200-ppm source clock offset. It
also checks bad FCS rejection after stopping and deliberate overflow cleanup.
These simulations replace the analog circuit with a binary waveform and
replace the board transport with a cocotb adapter; they provide no analog,
RP2350 DMA or physical throughput measurement.

The hardware owner should retain one measurement record containing:

1. PCB/BOM/package revisions, board/SDK/firmware/source hashes, supply and
   actual project/SCK clock measurements; reserved pins/PIO/DMA resources.
2. Differential `RP−RN`, `SIGN`, `ENV`, `ACTIVE` and `ui0` captures for idle,
   link pulses, acquisition, weak/strong packets and the minimum gap. Record
   trip amplitudes, delay dispersion, pulse distortion, envelope release and
   rail excursions. Resolve protection/termination questions before a broader
   link test.
3. Source/frame identities and complete FCS-checked 64/128/1518-byte bursts,
   bounded service stalls, forced overflow and reset/recovery. Retain raw
   records and errors, including failed attempts.
4. Measured host payload rate and worst poll/service latency; only then test
   simultaneous external TX/RX at a validated common clock. Digital loopback
   and this one-shot receiver do not establish that final requirement.

No connected board or built receiver has been qualified in this workspace.
