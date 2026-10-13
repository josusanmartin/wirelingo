# One retained four-byte UART report over USB EP0

This optional preparation uses two existing cores/banks and one retained
four-byte report. It has no batch model/native result yet. The accepted
one-byte workload is a separate mode and remains unchanged. No RTL, larger
bank, FIFO relay, bulk endpoint, third core or generated bundle is added.

| Resource | Reservation |
|---|---|
| Core0/bank0 |256 words/1024B, no headroom; autonomous low-speed EP0 |
| Core1/bank1 |59 words/236B; partial group in ISR/OSR, retained halves R1/R2 |
| Physical pins |USB0/1 owned by core0; RX16, READY8, ERROR9 owned by core1 |
| Virtual pins |24..31 owned by core1; core0 IN_BASE24 |
| Flags |F0..5 USB; F6 byte-ready; F7 retained report-valid |
| FIFO geometry |Two cores/two banks/256 words/depth8, no runtime FIFO service |
| Cadence |Core0 encoded256; core1 encoded3333 =3333/256 clocks/tick |
| External source |48MHz clock, nominal115200 8N1, exact1250/3 clock bit period |
| USB line |LS32 clocks/bit, TURN63, synchronized inputs/SYNC_BYPASS0 |

ERROR9 costs one additional output relative to the one-byte program. Core1's
32-tick UART schedule executes more frequently; power/area/Fmax are unmeasured.
The complete cold loader plans339 normal host writes and starts both cores
once. No active loading-host API, FIFO service, interrupt or host rearm is
part of the proposed workload. External USB-host transactions remain a paid
service cost. Single buffering pauses UART acquisition until the DATA ACK;
four-byte batching does not establish sustained115200-baud forwarding.

## Ownership and lifetime

`pe.usb_uart_batch4.assemble()` and `load_writes()` prepare only this cold,
disabled mode, with profile validation and explicit owners. `vendor_request()`
defaults to `c0 01 00 00 00 00 04 00`; every other wLength is refused, not
clamped. No partial-report timeout flush exists. Descriptors remain18/18bytes
with zero noncontrol endpoints; this is not HID/CDC/interrupt/bulk USB.

READY is active high and grants one complete UART frame. The source must
finish an admitted frame even if a new grant arrives during its STOP. The
frozen first group starts each frame at max(new READY edge, previous frame
completion)+8 clocks. The first idle frame is READY+8; later frames finish
the prior STOP before reacting. Later groups may also wait for planned USB
work before observing an already granted high level. Only actual physical
READY controls UART admission; internal flags/registers never drive source
decisions or expected data.

Good STOP advances the partial group; bad STOP discards only the suspect
byte, restores prior good bits and sets sticky physical ERROR9. ERROR is not
a byte in the USB payload and is cleared only at cold restart. Four good
bytes are retained in R1/R2. F7 is set once, after the first F6 publication.
Core0 clears F6 with WAIT before IN8; core1 separately observes that clear
before changing virtual pins. This relies on uninterrupted core0 divider1
and no other flag/pin writer, not an atomic hardware mailbox instruction.

Only the existing valid ACK/F_SENT check at exact cursor4133 clears F7.
Lost/malformed ACK, retries, descriptor SETUP, deconfiguration, non4 lengths
and USB detector reset retain the report/error. Every started report reads
two complete16-bit words; cyclic supplier state returns to byte zero. The
producer checks F7 before F6 to escape a speculative next byte on consumption,
and retries never re-set F7. Release is at DATA ACK before status completion;
application exactly-once delivery is not promised.

These reset/abandonment rules assume fixed configuration, uninterrupted
cores and eventual completion of all four internal reads. Global reset,
reload, host gating, faults, preemption/abort after1..3 supplier reads and
arbitrary configuration changes are excluded. Shared line end/error causes,
USB electrical/reset qualification and all malformed endings remain outside
the inherited byte/partial-tail ACK guard.

## Proposed finite witness and phase coverage

The new single ordinary-pytest node proposes four good00 bytes, nominal
address5 enumeration, three all-zero no-ACK IN attempts, a malformed ACK,
descriptor/non4-length/config0/1 retention, detector reset/re-enumeration,
and one exact4 consuming dribble ACK. A second report is39,d3,81,5e with bad
STOPa6 inserted after39,d3; descriptor and reset must retain report and ERROR,
then an ordinary ACK must consume it. Two final good bytes12,34 followed by
5000 idle clocks must remain unavailable. No repeated response repair or
automatic retries is permitted. All no-ACK handoffs use fixed64-clock gaps.

No runtime model result is asserted. The future proposal is one serial node,
120s wall/2GiB, five-second process-group grace and at most1000000 actual
Chip.step calls including loading/failures. A separate lead slot is required.

The source divider calendar is T(n)=340+13n+floor(5n/256), based on339 cold
writes and normal restart. The proposed no-stuff path has header OUT H at
IN START+1155, last word-one payload OUT C=H+930, and third-byte F6 clear
S=C+5. Three fixed external IN START clocks are118185,132206,146230. They
target producer poll clocks120275,134297,148319 and fractional states
236/245/254; offsets0/−1/+1 discriminate the source edge convention without
a pilot, deposit, runtime search or retuning. These are source predictions,
not observed clocks. The frozen source-calendar receipt lists derivation
and all initial UART grants/windows.

The passive trace retains actual word boundaries, F6 WAIT/IN, producer poll,
virtual-pin OUT and FLAG publication, queue occupancy and next payload OUT.
At least one declared attempt must observe a same-edge WAIT clear and
producer poll seeing old-high, with53 clocks to FLAG publication. Otherwise
timing coverage is incomplete, even if all-zero bytes happen to pass. The
oracle must also see S=C+5 and bit17 OUT no later than C+63, before earliest
no-stuff empty decision C+95. The director's all256 fractional-state source
arithmetic remains distinct from this proposed trace coverage.

Raw resolved USB pads decide SYNC/PID, exact four-byte payload, CRC, EOP and
packet count, so underrun/split packets cannot pass by eventual equality.
Raw READY/RX/ERROR histories decide admission, good/bad frames, sticky error,
retention and release. PC/flag/queue observations corroborate timing only.
Loading API guards and actual bus-strobe observations must both remain zero;
all line RX overflow and ownTX/external-overlap observations are retained.
Overlap fails the oracle. No measured throughput/latency/maxima or PPA/native
claim follows from assembly, collection or source arithmetic.

The test uses pytest's tmp_path by default. A nonempty WIRELINGO_EVIDENCE can
select a fresh explicit output directory. After an authorized model slot:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools /usr/bin/python3 -B -m pytest \
      -q -x -p no:cacheprovider test/model/test_usb_uart_batch4.py

Preparation may collect this node with Chip/Core construction/step forbidden;
collection is not a functional result. No source change or dynamic retry is
authorized by this document.
