# Cooperative fixed4 USB/UART overlap: preparation only

This optional source pair prepares a finite witness: serve one retained four-byte
vendor report while capturing the next report through a maximum sampling gap,
bad STOP and ACK crossings. It has **not been executed**. Source arithmetic,
assembly, cold-write validation and pytest collection cannot establish model,
RTL, electrical, energy, sustained-rate or PPA results.

The two immutable director images are copied with only program names/comments
changed. Ordinary bank loaders accept core0's256-word consumer and core1's
93-word producer:1396B,0/163 spare words. Both cores/banks, all eight flags and
virtual pins24..31 are consumed. USB owns pads0/1 and the line unit; core1 owns
RX16 (ui0), physical READY8 (uo0), sticky ERROR9 (uo1), and the virtual byte bus.
The producer contains no FIFO, LINE, blocking WAIT or HALT instruction. There
is no FIFO relay, rearm or loading-host runtime service API.

`pe.usb_uart_overlap4.load_writes()` proposes374 ordinary cold writes on the
2core/2bank/256word/depth8 profile. Both cores are stopped/empty before loading.
Ownership, outputs/flags and SYNC_BYPASS0 are paid once; both dividers are256
(one instruction tick/system clock), clock48MHz. UART external source is nominal
115200 baud,8N1; USB is low speed32clocks/bit with TURN63. Core0 CRC remains
USB's existing configuration. **Core1 CRCCTRL0x000f0000 is mandatory**:16-bit
scratch, both taps off. Default width1 truncates saved R0. SHIFTCTRL3 disables
autopush/autopull and uses right shifts. MOV-to-ISR resets its hardware count;
this program deliberately uses R3 fields instead, so that reset is not FIFO
publication or lost-length metadata.

R1/R2 retain the old32bits; ISR holds0..4 next good bytes. R3 combines remaining
bytes/bits and the cyclic byte cursor. CRC saves R0 during a24-clock service leaf;
OSR is disposable, LR has one leaf return and no nested CALL. The service never
sets F7. F6 advertises a virtual byte and the consumer WAIT clears it on capture;
producer cold/inactive/promotion paths also clear speculative F6. After cold
initialization, F7 clears only on the exact valid fixed4 ACK and sets only after
complete promotion and initial F6 publication. UART bad STOP raises ERROR and
removes only the suspect byte. USB reset preserves producer state; this witness
does not add general reset or USB conformance coverage.

Compared with the serial59-word supplier, this source costs34 more words and one
CRC configuration write, and increases instruction cadence from divider3333 to
256. CRC scratch is unavailable for simultaneous producer CRC. Power, physical
effects and sustained throughput are unknown. At most one retained report plus
one pending32-bit report is represented; READY throttles when both are full.
External USB-host polling/ACK service is still required. This is not a claim of
continuous115200 forwarding, an arbitrary queue, or a CPU-free system end to end.

## Frozen proposed workload and coverage

One maintained node is prepared:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_uart_overlap4.py::test_overlap4_retained_and_next
```

This command is a future execution proposal, **not authorized or run by C30**.
It uses tmp_path by default; a nonempty WIRELINGO_EVIDENCE chooses a fresh output
directory. It refuses to overwrite `overlap4-raw.json`. A test-local timetable
merges only RX16's external bit before every Chip.step, including all normal
USB peer _drive/_response/idle calls and cold writes. No blocking UART frame call
serializes the workload. No ui_mid/uio_mid, DUT-state deposits, FIFO drain or
internal-state feedback drives the source. Physical READY can qualify a fixed
START with a new observed grant; a missed grant fails without moving time/data.
Each full frame lasts4167clocks with bit edges ceil(k*1250/3), preserving STOP
regardless of an early READY rise.

| Report | Fixed physical UART START clocks | Expected report |
| --- | --- | --- |
| A |5000,10000,15000,20000 |00000000 |
| B |150009,155000,165000,170000 |39d3815e |
| C |190000,195000,200000badSTOP,205000,210000 |12345678 (a6 excluded) |
| D |230000,235000,240000,245000 |9abcdef0 |

Enumeration starts30000 (device18/address5/config18/configure/GET_CONFIGURATION),
and must finish before140000. A SETUP140000, IN148502, zero-tail ACK152000,
retry154335, valid ACK157700, status159000. B SETUP180000, IN187000, retry200115,
ACK203448, status204500. C SETUP220000, IN232000, retry256667, ACK260000,
status261000. D SETUP270000, IN277667, ACK281000, status282000. Empty exact4
request290000 must STALL without resurrection. Final fixed deadline310000.
All action clocks are before-action; a late previous action fails. Nominal
retries0/first-response and independent raw NRZI/stuff/PID/CRC/EOP oracles apply.
Resolved USB pads and both driver enables reject external/device overlap.

Mandatory future passive observations, separate from the report-byte oracle:

- A's continuous device DATA overlaps B's actual IN1 samples. D150011, consumer
  third F6 clear and producer old-high poll150592, FLAG6 publication150658:
  exactly66clocks. Next consumer OUT<=150663, before earliest empty150682.
- A's FLAG CLR7 instruction158241 (visible158242) lies between B samples6/7
  at158130/158547. Prefix and pending byte survive; B promotes only after its
  fourth good STOP while A survives malformed ACK/retry.
- B's FLAG CLR7 instruction203989 coincides with the middle bad-byte rollback
  instruction203989 (whole sequence203988..203990). ERROR rises203988; prefix
 12/34 survives, with no inserted/dropped a6 in the later exact C report.
- D is fully pending while C remains retained/retried. READY stays low after
  its final admission fall245016 until promotion. ACK clear260541 is observed
  at guard260554; both ISR halves copy before new F7 instruction260566. Initial
  byte/F6, ISR cleanup/cursor reset and READY visible260594 must be in order.
- Across valid old-report captures, F6 publication<=66clocks and no pin rewrite
  before consumer IN. No active host API/strobes or FIFO service. All RX-overflow
  events retain own/external TX phase observations, without a causal or
  robustness claim.

## Source checks and future slot gates

The calendar is reconciled using finite syntactic CFG costs and static codec
arithmetic, not a register interpreter or model pilot.374 writes predict first
core1 instruction375, READY visible407, START poll431 then25-clock polls.
Samples are D+625+417k, STOP D+3961. Default synchronized half-sampling of a
normal d2 ACK predicts last PID sample A+498, SE0 A+530, IN4 completion A+531,
FLAG CLR7 A+541 and visible low A+542. Release does not wait for full EOP; in
the full-pending crossing READY260594 precedes ACK EOP completion260608.
The original director proposal is preserved. Revision2 incorporates the reviewed
source-only ACK-gap supplement: A retry154000->154335, B retry198000->200115,
C retry254000->256667, D IN277000->277667. All ACKs, UART frames and critical
first A IN148502 stay fixed. Original first-J-to-ACK gaps399/2179/2731/731
were too long for the declared nominal spacing. Prepared valid ACKs are exactly
64clocks after DATA's **first EOP J**,32clocks after that J cell completes.
The saved raw audit separately requires both coordinates. Revision1's
full-cell-end>=64 arithmetic omitted the upper spacing check; its successful
source/collection receipt is preserved and does not validate that contract.
Observed overlap coverage remains **none**.

Preparation permits at most two serial60s/2GiB preflights and two source revisions,
with Chip/Core constructors/step methods blocked, ordinary collection only. A
future proposal is one serial node, one cold scenario,120s/2GiB/400000 aggregate
Chip.step including all setup/helpers,5s TERM/KILL grace. Its launcher refuses a
missing/wrong separate accepted model-slot receipt, input hashes, caps or pause.
No launcher execution occurs during preparation. Qualified C26 runner/count/
process-group mechanics are reused with only this node/root/400000-step cap and
fresh outputs; Atlas's frozen files and model slot are untouched.

A future saved-only audit requires the unique regular expected-node raw file,
records its exact path/hash, preserves failure logs, checks all raw payloads and
mandatory timing, and compares the external counter. After one complete pass it
also rejects three disclosed saved-only mutants: a changed report byte with a
valid recomputed CRC, removed actual overlap/rollback timing observations, and
publication delayed beyond66. Mutants are not additional DUT results. No old raw
packet is imported. Lead owns commits, later model authorization and integration.
Preparation check results and frozen hashes are supplied in the team-root packet
`evidence/sol-kepler/C30-KEPLER-OVERLAP4-PREP/` and Kepler's live report.
