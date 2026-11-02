# Explaining Wirelingo

This page is for the author. It assumes no hardware background. It defines each
term the first time it appears. The goal is that you can stand in front of an
engineer and trace this chip with your finger.

It has five parts: one instruction, one protocol, one bug, why two
implementations help, and 25 likely questions. Appendix A reproduces the clock
counts. Appendix B lists stale lines a judge could catch.

Line references are for `main` at `61af9b2`. The code still uses the old project
name: `protoemu`, and the file prefix `pe_`.

## Evidence levels

Every result in this repository sits at one level. Say the level out loud.

| Level | What actually ran | What it cannot show |
|---|---|---|
| Model | The Python simulator `tools/pe/sim.py` | That the hardware does the same |
| RTL simulation | The chip's source code in a simulator, with no delays | Real timing |
| Formal | A mathematical proof about named properties | Anything outside those properties |
| Gate level | The final list of logic cells, in a simulator | Real timing, unless SDF is added |
| Gate level with SDF | The same, with calculated delays | Paths the test does not exercise |
| RTL in FPGA fabric | The source code on a reprogrammable chip | The real chip's pads, memory and timing |
| Silicon | Nothing yet | The chip has not been fabricated |

RTL means register-transfer level. It is the source code that describes the
chip, in `src/`, written in the Verilog language. A netlist is the list of logic cells that the tools build from
the RTL. SDF is a file of calculated delays for that netlist. An FPGA is a
chip whose logic can be reprogrammed. The board here is a Kria KV260.

**FPGA is not silicon.** An FPGA run shows that the logic works at speed on
other hardware. It says little about this chip's pads or memory.

## 0. The chip in a few sentences

A clock is a signal that ticks at a fixed rate. Here it is 50 MHz, so one clock
period lasts 20 ns. A clock edge is the instant the clock rises. Nearly
everything in the chip changes on a clock edge.

A flip-flop, or flop, stores one bit and updates on a clock edge. A register is
a group of flops. Logic between flops settles during the clock period.

The chip has two cores. A core is a tiny processor that runs one instruction
per tick. A tick is one step of a core. A divider sets how many clocks make a
tick, and after reset it is one. Each core has an SRAM bank. SRAM is on-chip memory, here 256 words
of 32 bits each. A word is one 32-bit value.

Firmware is the program in that memory. A protocol is an agreed way to send data
over wires. UART is a simple serial one (section 2). Here a protocol is firmware,
not a fixed circuit. A host is the outside computer that loads firmware. Tiny Tapeout
is a shared chip-fabrication service. On its demo board the host is an RP2350
microcontroller.

A pad is the metal connection from the chip to the outside. A pin is the same
idea, seen from the firmware.

## 1. One instruction, from the host to the pad

We follow `pin HIT, hi`, at `firmware/eth10_match.pasm:53`. That firmware watches
Ethernet frames, and Ethernet is the standard wired computer network. The
instruction raises one output pin. `HIT` is defined as 8 at `firmware/eth10_match.pasm:18`. Pin 8 is the pad
`uo[0]` (`docs/isa.md:55`).

**Evidence level: RTL simulation** (no delays, a behavioural SRAM model).
Behavioural means a stand-in that acts like the real part but has no real circuit
inside. The
clock counts come from a trace you can rerun (Appendix A). They agree with the
spec at `docs/isa.md:88-89` and `docs/isa.md:616-620`.

The trace uses a two-word program: this instruction, then `halt`. A full
firmware only adds more words. It runs at reset settings, so the core ticks on
every clock.

### 1.1 The assembler turns text into a word

An assembler turns readable instructions into numbers. `_pin` at
`tools/pe/asm.py:411-415` builds the operand. In `eth10_match` this word sits
at address 31.

| Bits | Value | Meaning | Spec |
|---|---|---|---|
| 31:27 | 01010 | opcode 10, `PIN` (an opcode names the operation) | `docs/isa.md:415` |
| 26:19 | 0 | no side-set, no delay (defined in section 2) | `docs/isa.md:185-197` |
| 18:17 | 01 | action 1, `hi` | `docs/isa.md:594-599` |
| 4:0 | 01000 | pin 8 | `docs/isa.md:415` |

The whole word is `0x50020008`, confirmed by assembling the file.

### 1.2 The host sends it over SPI

SPI is a simple serial link with a clock wire. SCK is that clock. MOSI carries
data from the host to the chip. CS_n selects the chip when it is low.

The bytes are built at `tools/pe/host.py:15`. One write is a command byte `0x02`,
a 16-bit address, then 32 data bits. That is 56 SCK rises, one bit per rise.

SCK does not drive the rest of the chip. The host port has its own small state
machine on SCK. A state machine is logic that steps through named phases. This
one is at `src/pe_hostport.sv:70-115`.

| Phase | What happens | Where |
|---|---|---|
| Command | 8 bits shift in, then the command is checked | `src/pe_hostport.sv:83-92`, `:58-59` |
| Address | 16 bits shift in and go to a hold register | `src/pe_hostport.sv:93-99`, `:126-130` |
| Data | 32 bits shift in and go to a hold register | `src/pe_hostport.sv:105-111`, `:134` |

A hold register keeps a value steady until the other side has taken it.

### 1.3 The word crosses into the chip's clock

SCK and the chip's clock are unrelated. A signal crossing between them can
arrive just as a flop samples it. The flop can then settle slowly to a random
value. That is called metastability. The standard defence is a synchroniser:
two or more flops in a row, which give the value time to settle.

The data itself does not cross bit by bit. It waits in `w_hold`. Only one bit
crosses, `w_tog`, which flips when a word is ready (`src/pe_hostport.sv:134-135`).

In the table, edge S is the last clock edge before the word's final SCK rise. In
the trace, S is edge 467 (Appendix A). The bus is the set of internal wires that
carries an address and data to registers and memory.

| Clock edge | What changed | Where |
|---|---|---|
| S + 1 | `w_s[0]` takes the new toggle | `src/pe_hostport.sv:170` |
| S + 2 | `w_s[1]` takes it, so `w_ev` goes high: a word has arrived | `src/pe_hostport.sv:176` |
| S + 3 | `w_pend` goes high: a write is queued | `src/pe_hostport.sv:217` |
| S + 4 | `bus_we` goes high with the address and data on the bus | `src/pe_hostport.sv:232-237` |
| S + 5 | The SRAM stores the word | `src/pe_imem.sv:62-70`, `test/sram_model.v:27` |

The address picks the memory at `src/pe_chip.sv:58` and `:79`. The top address
bits pick the bank at `src/pe_imem.sv:29`.

The SRAM has one port. A write borrows the port from fetching
(`src/pe_imem.sv:60-61`, `:67`). So the host may write a bank only while its
core is stopped (`docs/isa.md:122`).

### 1.4 The host starts the core

The host writes `0x101` to `CTRL`, address `0x0801` (`tools/pe/host.py:111`).
Bit 0 enables core 0. Bit 8 restarts it. That write crosses the same way. Call
the edge where `bus_we` goes high T.

### 1.5 Fetch, decode, execute, in one clock

The PC is the program counter. It holds the address of the instruction being
run. The core offers its next address to the SRAM on every clock
(`src/pe_core.sv:661-663`). The SRAM read is registered, which means the data
appears after the next clock edge. So the PC and the instruction change on the
same edge (`src/pe_core.sv:753`, `src/pe_chip.sv:110`).

There is no pipeline. A pipeline splits an instruction across several clocks.
Here the fetched word is decoded and executed within one clock period.

| Clock edge | What changed | Where |
|---|---|---|
| T | `bus_we` is high for address `0x0801` | `src/pe_chip.sv:63` |
| T + 1 | `ctrl_pend` is high, so `restart` is high and the fetch address is forced to the entry address | `src/pe_chip.sv:69`, `:263`; `src/pe_core.sv:661` |
| T + 2 | `enable` is high and the instruction is on the wires. `exec` goes high. The instruction is decoded and the pin write is computed | `src/pe_chip.sv:265`; `src/pe_core.sv:213`, `:607-613` |
| T + 3 | `pin_out[8]` is 1 and `uo_out[0]` is 1. PC is 1, and `halt` is already on the wires | `src/pe_chip.sv:259`, `:201`; `src/project.v:100` |

During T + 2 these steps happen as settling logic, with no clock between them:

1. `op`, `sd` and `x` slice the word (`src/pe_core.sv:169-171`).
2. `tick` is high because the divider is at 0 (`src/pe_core.sv:188`). A restart
   clears it, so the first enabled clock is a tick (`docs/isa.md:156`).
3. `exec` needs `tick`, an enabled core, no delay pending, and a PC in the
   core's own bank (`src/pe_core.sv:210-213`).
4. The `OP_PIN` case picks one pin and its value (`src/pe_core.sv:607-613`).
5. A mask and value are built by rotation (`src/pe_core.sv:631-633`). A mask is a
   32-bit value with a 1 at each bit to change. Rotation moves bits around to the
   chosen pin's position.
6. They leave the core as write requests (`src/pe_core.sv:637-642`).
7. The chip keeps a request only if that core owns the pin
   (`src/pe_chip.sv:196`). After reset core 0 owns every pin (`docs/isa.md:90-92`).
8. The flop `pin_out[8]` takes the value at the next edge (`src/pe_chip.sv:259`).

From the last SCK rise to the pad value is 7 clock edges. At 50 MHz that is
120 to 140 ns, depending on where SCK falls within a clock (Appendix A).

A running program has no restart step. The word before this one is `jmp c`, at
address 30. It finishes and offers address 31. Next clock, `pin 8, hi` runs. One
clock later the pin is high. So at full speed each instruction takes one clock
(`docs/isa.md:118-120`).

### 1.6 From the flop to the pad

`pin_out[8]` reaches `uo_out[0]` through wires and one merge with the line unit
(`src/pe_chip.sv:53`, `:201`). The line unit is a small hardware helper for
serial lines. The top level passes the bit out at `src/project.v:100`.

With SDF at the typical corner, `uo_out[0]` rises 2.445 ns after its clock edge
(`docs/post-freeze-protocols.md:94-95`). A corner is one assumed set of silicon
speed, voltage and temperature. Tiny Tapeout's output path and the board come
after that port. Those delays have not been measured.

The path from SRAM through decode to `pin_out` matters for speed.
`test_fetch_to_pin` (`test/test.py:1430`) exists for this path. Its docstring
calls it the slow-corner critical path. A critical path is the slowest one, and
it sets the speed limit. The test must pass with SDF at 50 MHz. It must fail at
the slow corner at 100 MHz (`docs/verification.md:331`). That failure shows the
delays are really applied.

### 1.7 What this section does not show

The SRAM here is a behavioural stand-in. Clock skew is absent. Skew means the
clock reaches different flops at different times. The crossing is two-flop by
design, but metastability is not simulated. Static timing analysis (STA)
calculates worst-case delays and covers real timing separately.

## 2. One protocol: `uart_tx`

UART is a serial format with no clock wire. The line idles high. A byte is a low
start bit, then 8 data bits, lowest bit first, then a high stop bit. This format
is called 8N1.

The whole program is `firmware/uart_tx.pasm`, four instructions. Lines 1 to 13
are comments. Some of them are `@deadline` lines for the timing checker.

### 2.1 Terms for this program

| Term | Meaning |
|---|---|
| Tick | One step of the core. A divider sets clocks per tick (`docs/isa.md:144-157`) |
| FIFO | A small queue. The host pushes words in, the core pulls them out |
| OSR | Output shift register. It holds the word being sent |
| Side-set | A pin change bolted onto any instruction, from bits 26:19 |
| Delay | Extra idle ticks after an instruction completes, from the same bits |
| Stall | The instruction cannot finish, so it retries next tick |

### 2.2 Line by line

| Line | Text | What it does |
|---|---|---|
| 14 | `.program uart_tx` | Names the program |
| 15 | `.side_set 1 opt` | One side-set pin, optional per instruction. Bit 7 of the field is enable, bit 6 is the value, and 6 bits remain for delay (`src/pe_core.sv:216-221`) |
| 16 | `.shift_out right` | OUT takes the lowest bit first, which is UART's order |
| 17 | `.wrap_target` | The address to return to. No jump instruction is needed |
| 18 | `pull block side 1 [7]` | Sets the pin high, then waits for a word. Then idles 7 ticks |
| 19 | `ldi r0, 7 side 0 [7]` | Loads the bit counter, drives the pin low for the start bit, idles 7 ticks |
| 20 | `bitloop:` | A label |
| 21 | `out pins, 1` | Moves one bit from the OSR to the pin. 1 tick |
| 22 | `jmp r0--, bitloop [6]` | Jumps back if r0 was not 0, and decrements r0. Then idles 6 ticks |
| 23 | `.wrap` | After line 22, go back to line 18 |

The four assembled words are `0x2E3A0000`, `0x44380007`, `0x18020001`,
`0x00318002`. Take the first one. Its opcode is 5, `PULL`. Bits 26:19 are `0xC7`:
enable 1, value 1, delay 7. Bit 17 is `block`.

Where each one lands in the RTL:

| Instruction | RTL |
|---|---|
| `pull` | `src/pe_core.sv:532-547` |
| `ldi` | `src/pe_core.sv:592-596` |
| `out pins` | `src/pe_core.sv:475-494` |
| `jmp r0--` | `src/pe_core.sv:417-424` |
| delay count | `src/pe_core.sv:797`, `:782-783` |
| wrap | `src/pe_core.sv:354` |

Two details matter.

**The stop bit comes from a stalled instruction.** Side-set acts on every
attempt, even a stall (`docs/isa.md:164-169`). `side_act` needs `exec`, not
`complete` (`src/pe_core.sv:222`). So line 18 drives the line high while it
waits. That is both the stop bit and the idle level.

**Each bit lasts 8 ticks.** The loop runs 8 times, for r0 from 7 to 0.

| Part | Ticks |
|---|---|
| Start bit, line 19 | 1 + 7 = 8 |
| Each data bit, lines 21 and 22 | 1 + (1 + 6) = 8 |
| Eight data bits | 64 |
| Stop bit, line 18, when a word is waiting | 1 + 7 = 8 |
| One byte | 80 |

### 2.3 Setting the speed

CLKDIV is a divider register with a fractional part. It sets clocks per tick
(`docs/isa.md:146-155`). Line 8 uses 52.083 for 115200 baud at 48 MHz. That is
stored as 52 and 21/256. Baud is bits per second here.

The fraction means ticks are not all equal in length. A tick is 52 or 53
clocks, and the average comes out right. So 72 ticks measure 3749 to 3750
clocks.

### 2.4 How it is checked

| Check | Level | Reference |
|---|---|---|
| Timing checker: all 5 deadlines PASS | Static, on the program | `python -m pe.timing firmware/uart_tx.pasm`, `docs/timing-check.md` |
| `test_uart_tx`, through the real SPI port at 16 clocks per bit | RTL simulation, gate level | `test/test.py:221`, `docs/verification.md:160` |
| The same run decoded by sigrok | RTL simulation | `test/test_sigrok.py:121` |
| Loopback and real PMOD pins | RTL in FPGA fabric | `README.md:164` |

A logic analyzer records digital signals over time. Sigrok is open-source
logic-analyzer software, written by other people. A PMOD
is a small connector on the FPGA board. The far end in these tests is a model or
the other core, not a third-party UART device.

### 2.5 One thing a plain UART block could not do

A fixed UART block sends frames. DMX512 stage lighting needs more than frames.
It needs a low BREAK of at least 92 us, then a high MARK of at least 12 us, then
frames, then padding to a 1204 us minimum period (`firmware/dmx512_tx.pasm:8-15`).

Some UART blocks have a "send break" bit. Host software must still time it. Here
one instruction times it, in `firmware/dmx512_tx.pasm` (20 words).

| | `uart_tx` | `dmx512_tx` |
|---|---|---|
| Ticks per bit | 8 | 4, with 1 tick = 1 us, so 250 kbit/s |
| Start bit | `ldi r0, 7 side 0 [7]` | `pin TX, lo` then `ldi r0, 7 [2]` (`:52-53`) |
| Data loop | `out pins, 1` then `jmp r0--, bitloop [6]` | `out pins, 1` then `jmp r0--, bit [2]` (`:55-56`) |
| Stop | 1 bit, by side-set | 2 bits, `pin TX, hi` plus counted ticks (`:57-59`) |
| BREAK | none | `pin TX, lo [175]`, 1 + 175 = 176 us (`:48`) |
| Mark after BREAK | none | `pin TX, hi [10]`, 1 + 10 + the next `pull`, 12 us (`:49`, `:51`) |
| Frame count | none | A header word carries the slot count (`:44`) |
| Late host | Line stays high | Line stays high (`:51`), and DMX512 allows that up to 1 s (`:14`, `:21`) |

The bit loop is the same idea. Only the delay changes, from 6 to 2. The new
behaviour is a few more instructions, with no change to the chip. This firmware
was written after the RTL was frozen (`firmware/dmx512_tx.pasm:1-2`).

**Evidence:** RTL simulation at 50 MHz measures BREAK 176.000 us and MAB
12.000 us (`docs/post-freeze-protocols.md:28`). Sigrok's `dmx512` decoder agrees
(`docs/post-freeze-protocols.md:231-232`). The FPGA run is listed at
`README.md:183`.

With SDF at the typical corner, BREAK measures 175999.83 ns. The test first
failed on exact equality. It now allows 1 ns (commit `8dd249a`,
`docs/post-freeze-protocols.md:91-108`). Say this before anyone asks.

This is not special to this chip. PIO is the programmable I/O block in the
RP2040 microcontroller, and it inspired this instruction set. A PIO program could
produce a BREAK too. The comparison here is with a fixed UART block.

## 3. One bug: the assembler's `/`

This is a real bug, fixed in commit `2673606` on 2026-10-13.

### What it was

`firmware/dshot_bidir.pasm:65-66` splits a delay into two halves:

```
out r3, 1   side 0 [(T0 - 2) / 2]
jmp !r3, zero   side 0 [T0 - 2 - (T0 - 2) / 2]
```

The two delays should add up to `T0 - 2`. The assembler computed `/` with
exact division, then cut each result to a whole number.

At 48 MHz, `T0` is 30. Then `T0 - 2` is 28, and the halves are 14 and 14.

At 50 MHz, `T0` is 31. Then `T0 - 2` is 29. Each half is 14.5, which becomes 14.
The sum is 28, one clock short. Every bit of this protocol lost 20 ns.

DShot is a digital protocol for motor controllers. This firmware is the
bidirectional version. An ESC is the motor controller at the far end.

### How it was found

Evidence level: RTL in FPGA fabric. The first KV260 run of this firmware used
`la_capture`, the chip's logic-analyzer firmware, to read the pin. It showed
lows of 600 and 1220 ns and a bit of 1640 ns. The defines ask for 620, 1240 and
1660 ns (`fpga/kv260/README.md:329-330`).

The frames were still correct. Only the time was wrong. The cycle model then
showed the same numbers.

### Why the earlier checks missed it

| Check | Why it stayed quiet |
|---|---|
| `test_bidir_no_reply_and_timing`, exact timing on the model | It ran only at 48 MHz, where nothing is lost. See `git show 2673606` |
| `test_bidir_50mhz` | It ran at 50 MHz, but only asked whether the ESC model decoded the frames. It did |
| RTL test `pf_dshot_bidir` | It ran at 48 MHz (`test/test_post_freeze.py:395`, `:403`) |
| Static timing checker | It ran with `T0` 30 (`test/model/test_post_freeze_timing.py:25`, `firmware/dshot_bidir.pasm:34-38`) |
| Model against RTL | Both ran the same wrong words. They agreed perfectly |
| Sigrok | There is no DShot decoder, only `pwm`, which reports pulse widths (`README.md:188`) |

The ESC model tolerated one clock of error.

So the bug lived in the one tool both implementations share, at a clock where
no test compared time against the define.

### What changed

* `tools/pe/asm.py`: `/` became integer division, so the halves are 14 and 15.
  The commit message says no other firmware expression changes value.
* `test/model/test_dshot_bidir.py`: the timing test now runs at 48 and 50 MHz
  and compares against the defines. The commit message says it fails on the old
  assembler with the KV260's numbers.
* Commit `1369790`: the board reran on the same bitstream, which is the FPGA's
  configuration file. It measured 1240, 620 and 1660 ns, exactly 62, 31 and 83
  clocks.

### The lesson

The model could have shown this all along. Nobody asked it the right question.
The board found it because it measured time rather than checking agreement.

## 4. Why two implementations help, and where they stop

### What is compared

`docs/isa.md` is the spec, and it says it wins over everything else
(`docs/isa.md:3-6`). Two things implement it: the Python model
`tools/pe/sim.py`, and the RTL.

Lockstep testing runs both side by side on the same inputs, and compares them
every clock. `test/test_lockstep.py:116` is the comparison. It covers registers,
FIFO levels and heads, pins, flags, and the state of WAIT, the instruction that
pauses until a condition holds. The list is at `docs/verification.md:58-67`.

Inputs are random: programs, pin changes and host traffic
(`docs/verification.md:42-57`). The default run is 40 seeds of 1000 cycles
(`test/mutants.py:20`). A seed picks one random run.

### Why it catches things

A bug must be made twice, identically, to the exact clock, to stay hidden. A
missed case in one implementation shows up as a mismatch.

Here is a real case. The spec says a host write in cycle t takes effect in
cycle t + 1 (`docs/isa.md:611-612`). A FIFO join chains two queues into one. The
model applied the join bit a clock too early. Lockstep stopped on the first seed with such a write
(`docs/verification.md:392`). The RTL was right and the model was wrong. The fix
followed the spec, rather than copying either side.

Deliberate bugs show it works. An ALU mutation was caught in about 1,000 cycles.
A CRC mutation took about 3,900 cycles. That delay exposed a rare case, so a
directed test was added (`docs/verification.md:94-99`). An ALU is the
arithmetic unit. A CRC is a checksum.

### The word "independent"

Do not call them independent. The same agents wrote both, from one spec.
`docs/methodology.md:63-67` says "two languages don't make two minds". The claims
audit removed the word from the README (`docs/claims-audit.md`, findings 6 and 7).

### What lockstep cannot catch

A pins-only device model is Python code that plays the far-end device. It sees
only the chip's pins, not its insides.

| Limit | Example | What covers it |
|---|---|---|
| A misreading of the spec, shared by both | Both follow the same wrong idea | Sigrok decoders, pins-only device models, a real USB host |
| A spec that asks for the wrong thing | `docs/methodology.md:142-143` | Nothing automatic. Review, real far ends |
| Shared inputs | The `/` bug: both ran the same wrong words | Measuring against the protocol's numbers |
| Firmware logic | `ws2812` and `can_rx` bugs: both ran them faithfully | Device models, directed tests |
| Parts outside the comparison | `test/tb_chip.v:41` wraps `pe_chip`, with no SPI port | Host-port proofs, top-level tests |
| Delay and analog behaviour | Both are zero-delay and digital | STA, SDF, FPGA, one day silicon |
| Metastability | Neither simulates it | Design rules, and STA crossing bounds |
| Cases never generated | Two model bugs appeared only in other seed windows (`docs/verification.md:372-373`) | More seeds, formal proofs of pieces |

Lockstep shows agreement on the traces it ran. It proves nothing about other
traces (`docs/verification.md:67`). Whole-core equivalence is not proved
(`docs/verification.md:147-148`).

## 5. Twenty-five likely questions

`AUTHOR TO ANSWER` marks what only you can say. Answers here are facts from the
repository. Do not memorise them. Be able to find them.

### The design

**1. What is Wirelingo, in a minute?**
Two small cores, each with 256 instructions, built for driving pins on exact
clocks. A host loads a protocol as firmware. It was hardened for IHP SG13CMOS5L,
a 130 nm manufacturing process, through Tiny Tapeout. Hardening means turning RTL
into a layout. It has not been fabricated.

**2. Why firmware on small cores, rather than fixed protocol blocks?**
A new protocol then costs no silicon. After the RTL froze, 14 protocols were
added in firmware (`docs/guide.md:84`). One assembler fix was needed
(`README.md:82-86`). The cost is speed and host effort compared with a fixed
block. Timing is your program's job.

**3. Why this architecture over RP2040 PIO?**
Not for speed or code size. PIO is faster and smaller
(`docs/pio-comparison.md:9-12`). The case is less host work and better fault
handling: WAIT timeouts, an I2C master that cannot hang, and more program space
(`docs/pio-comparison.md:388-405`). I2C is a two-wire bus. All of that comparison
is simulation. RP2040 silicon was not used.

**4. Where is PIO better?**
Clock, code density, I2C bus time, the host link, and engine count. It also
ships in silicon. The list is at `docs/pio-comparison.md:364-386`. Item 9 there
is stale (Appendix B).

**5. Why two cores?**
Three did not route on this tile: Metal3 reached 99.8 % with 24.1K overflow
(`docs/physical.md:364-376`). A tile is Tiny Tapeout's unit of chip area, and this
design uses 6 by 4. Metal3 is the one horizontal routing layer here. Overflow
counts where routing asks for more wires than exist.
RTL builds of up to 4 cores exist, for simulation.

**6. If protocols are firmware, why is there a line unit?**
With it, a USB low-speed device fits on one core, and 10BASE-T receive becomes
possible (`docs/isa.md:265-267`). USB low speed is USB's 1.5 Mbit/s mode. 10BASE-T
is Ethernet at 10 Mbit/s over twisted-pair cable. Without it, USB low speed took two cores
(`README.md:168-169`). It does NRZI, bit stuffing, Manchester decoding and clock
recovery. NRZI and Manchester send bits as transitions, not levels. Stuffing
adds bits to keep transitions coming. So "protocols are firmware" is not fully
pure, and you should say so.

**7. Walk me through one pin write.**
Use section 1. At full speed: one clock from fetch to output flop. From the host:
56 SCK, then 7 clock edges to the pin.

**8. What sets the speed limit?**
The clock period of 20 ns, and routing on one horizontal layer. Run fo6, the
release-candidate layout, meets 50 MHz at all three corners (`README.md:54`).
Edges fall on a 20 ns grid (`docs/pio-comparison.md:106`). The fetch-to-pin path
is the one `test_fetch_to_pin` guards (`test/test.py:1435-1437`).

### Verification

**9. How do you know the RTL matches the spec?**
Lockstep against the model, on random traces, with coverage goals. A coverage
goal is an event the random run must reach. Formal proofs cover the FIFOs, host port, core invariants and line unit. That is strong but not
complete (`docs/verification.md:147-148`).

**10. Are the model and RTL independent?**
No. The same agents wrote both. See section 4.

**11. How do you know your tests are not empty?**
Mutation testing: plant a bug and require a named test to fail. The recorded
result is 34 of 34 (`docs/verification.md:375`). It does not prove every number
has a test. On 2026-11-02 the verifier found a timing number with no test behind
it (`docs/verification-story.md`).

**12. Show me an input that should fail.**

* SCL, the I2C clock wire, stuck low at each of 19 releases must time out
  (`docs/verification.md:220-221`).
* A corrupt packet ID, a bad CRC or bad stuffing on USB must get no answer
  (`docs/guide.md:75`).
* 125 cases of USB ACK, the acknowledge packet, including malformed ones
  (`docs/review.md:35`).
* `test_fetch_to_pin` at the slow corner and 100 MHz must fail
  (`docs/verification.md:331`).
* Five planted netlist bugs must fail equivalence (`docs/equivalence.md:9-13`).
* Eight planted host-port bugs must fail the proof (`docs/formal-hostport.md:186`).
* An `eth10_match` field below the threshold must raise nothing
  (`docs/eth10-cut-through.md:28-29`).

**13. What was not verified?**

* Silicon, and this chip's pads.
* Analog levels, and USB or Ethernet electrical compliance
  (`docs/verification.md:297-300`).
* Any real network. The 10BASE-T receive circuit is unbuilt
  (`docs/guide.md:81`).
* The RP2350 transport on a board (`docs/guide.md:80`).
* The two-core USB firmware on the FPGA (`README.md:31`). SWD is parked.
* On `main`, post-freeze tests with SDF at slow and fast corners
  (`docs/post-freeze-protocols.md:104`). Commit `5d34fc6` on `improve-2026-11`
  records 16/16 at each corner, with no verifier report yet.
* On `main`, host-workflow tests at gate level (`README.md:157`). Commit `c0176e6`
  on `improve-2026-11` records 6/6, functional with no SDF, and no verifier report
  yet.
* Firmware changed after `7685aba`, the certified software (`README.md:45`),
  including `eth10_match`, under release receipts (`README.md:50-53`).
* Host-port behaviour outside its timing contract, and metastability
  (`docs/review.md:124-127`).

**14. What is the weakest part?**
Facts to choose from:

* Fast-corner hold passes by 0.095 ns, the thinnest margin (`docs/guide.md:90-92`).
  Hold time is a timing rule at a flop: data must stay stable just after the edge.
* At full SCK speed the host gets half a clock period to read the chip's data.
  That margin is not established on a board (`docs/isa.md:712-717`).
* The latch FIFOs did not run on the FPGA, which uses flops
  (`fpga/kv260/README.md:1454-1455`). A latch stores a bit like a flop, but it
  follows its input while open, rather than updating on an edge.
* The same agents wrote the spec, the model and the RTL.
* The USB stall in 5 of 116 attaches has no proven cause (`fpga/kv260/README.md:1323-1332`).

`AUTHOR TO ANSWER`: Which of these do you rank as the weakest, and why?

**15. Is the FPGA evidence silicon evidence?**
No. The FPGA uses inferred RAM, flop FIFOs and clock enables. It has no IHP
pads (`fpga/kv260/README.md:1452-1469`). Inferred RAM means the FPGA tools build
the memory from their own blocks. A clock enable makes a flop skip clock edges.
The chip instead uses clock gates, which switch off the clock to a group of flops. It shows the logic works at speed
against a real USB host and on the bench.

**16. Does the netlist match the RTL?**
Yes, by proof, for run fo6: 7429 points by two engines, with 1647 flops, 1024
latches, 54 clock gates and both SRAMs matched (`docs/equivalence.md:9-13`,
`:17-41`). It is zero-delay. It does not cover the inside of cells. LVS links
the layout to the netlist. LVS means layout versus schematic.

### AI

**17. What did the AI get wrong?**
Real examples:

* It stated a timing number with no test behind it (`eth10_match`, 2026-11-02).
* Its fix for that stated another false number, and the next fix added an exact
  check that could not fail.
* It wrote "exactly" and "at full speed" before measuring
  (`docs/methodology.md:110`).
* A signoff gate demanded something Tiny Tapeout does not, so no run could pass
  (`docs/methodology.md:107`). Signoff is the final set of checks before
  fabrication.
* Signoff claims pointed at older RTL (`docs/review.md:11`).
* A failed mutation command could count as a kill (`docs/review.md:22`).

The audit logged 46 findings, 32 of them wrong, overclaiming or stale
(`docs/claims-audit.md:97-105`).

**18. How do you check an agent's claim?**
A separate agent takes the claim, the commit and the base. It reruns the tests,
then tries to break them. The steps are in `docs/verification-story.md`.

**19. What did you personally decide?**
`AUTHOR TO ANSWER`: In your own words, which decisions were yours, and which one
was hardest? The record lists goals and scope, two cores, cutting cross-bank
fetch, and when to stop exploring (`docs/methodology.md:45`, `:120-134`). Name one
where you overruled an agent, if there was one.

**20. Can you explain the RTL without an agent?**
`AUTHOR TO ANSWER`: How much of `src/` have you read yourself? Trace section 1
out loud, without notes, and say honestly where you get stuck.

**21. What would you do differently?**
`AUTHOR TO ANSWER`: With what you know now, what would you change in the
architecture or the process? Give one concrete thing you regret.

### Results

**22. Why is this not trading hardware?**
It runs at 50 MHz on 130 nm. The Ethernet here is 10 Mbit/s. There is no CRC-32
check in the match firmware, so a corrupted frame can raise the pin
(`docs/eth10-cut-through.md:36-37`). The receive circuit is unbuilt. The match
reaction is 10 to 11 clocks on the model, and pad and comparator delays are
unmeasured. A comparator turns the cable's analog signal into a clean digital
level. The doc itself says it shows repeatable timing, not bandwidth
(`docs/eth10-cut-through.md:8-9`).

`AUTHOR TO ANSWER`: Why did you add a network match at all, and what do you want
a judge to take from it?

**23. Is the USB result real?**
On the FPGA, yes. Enumeration is the USB start-up exchange. Linux enumerated and
configured the chip's RTL at both speeds:
32 of 32 attaches, 66,000 of 66,000 control transfers, 188 of 188 resets
(`README.md:37-43`). A control transfer is a short USB request and reply. Limits:
one host and hub, no oscilloscope to view the real signals, 3.3 V FPGA pins. With
`usb_fs_dev`, 5 of 116 attaches stalled at `SET_CONFIGURATION`, the request that
activates a device. The cause is not proved (`fpga/kv260/README.md:1323-1332`). The 17.6 kB/s is the Python loop's
rate, not the chip's.

**24. What happens if the silicon fails?**
Nothing here proves it will work. What survives: the RTL results, the FPGA runs,
and the proofs. What falls: every physical claim. Bring-up starts by reading the
ID register, then an internal UART loopback with no extra wiring
(`docs/info.md:71-73`). So a failure can be narrowed to the host port, the cores
or the protocol pins. No fallback plan is written in the repository.

`AUTHOR TO ANSWER`: If the chip comes back dead, or partly works, what will you
do, and what would count as success anyway?

**25. What did this cost, and what did you learn?**
The first commit is 2026-09-30 (`docs/claims-audit.md:44`).

`AUTHOR TO ANSWER`: How many hours of yours, and how much money? What changed in
how you think about verification?

## Appendix A: reproducing the clock counts

This is a reading aid, not a regression test. Icarus Verilog is a free Verilog
simulator (`iverilog`, `vvp`). This ran on it at
`61af9b2`, in the tapeout configuration, with `test/sram_model.v`. It takes
seconds. Save the text below as `/tmp/trace/tb.v`, then run from the repo root:

```
iverilog -g2012 -I src -Ptb.SCK_HALF=4 -Ptb.PH=0.1 -o /tmp/trace/t.vvp \
  /tmp/trace/tb.v src/project.v src/pe_*.sv test/sram_model.v
vvp /tmp/trace/t.vvp
```

```verilog
// Trace of one pin write. Word 0 = `pin 8, hi`, word 1 = `halt`.
`timescale 1ns/1ps
`default_nettype none
module tb;
  parameter SCK_HALF = 4;     // SCK half period in clocks (test/test.py uses 4)
  parameter real PH = 0.1;    // host pins change this many ns after a clock edge
  reg clk = 0, rst_n = 0;
  reg [7:0] ui = 0;           // ui[7] CS_n, ui[6] SCK, ui[5] MOSI
  wire [7:0] uo, uio_out, uio_oe;
  tt_um_josusanmartin_protoemu d(.ui_in(ui), .uo_out(uo), .uio_in(8'd0), .uio_out(uio_out),
                                 .uio_oe(uio_oe), .ena(1'b1), .clk(clk), .rst_n(rst_n));
  always #10 clk = ~clk;      // 50 MHz
  integer n = 0;
  always @(posedge clk) n = n + 1;

  task clks(input integer k); repeat (k) @(posedge clk); #PH; endtask
  task bitout(input v); begin ui[5] = v; clks(SCK_HALF); ui[6] = 1; clks(SCK_HALF); ui[6] = 0; end endtask
  task send(input [31:0] v, input integer bits);
    integer i; for (i = bits - 1; i >= 0; i = i - 1) bitout(v[i]);
  endtask
  task write(input [15:0] a, input [31:0] w0, input [31:0] w1, input two);
    begin
      clks(SCK_HALF); ui[7] = 0; clks(SCK_HALF);
      send(8'h02, 8); send(a, 16); send(w0, 32); if (two) send(w1, 32);
      clks(SCK_HALF); ui[7] = 1; clks(SCK_HALF);
    end
  endtask

  wire [31:0] mem0 = d.u_chip.u_imem.g_bank[0].g_on.u_bank.sram.mem[0];
  wire [31:0] instr = d.u_chip.g_core[0].g_on.u_core.instr;
  wire [9:0]  pc = d.u_chip.g_core[0].g_on.u_core.pc;
  wire exec = d.u_chip.g_core[0].g_on.u_core.exec;
  wire tog = d.u_host.w_tog, ev = d.u_host.w_ev, pend = d.u_host.w_pend, we = d.u_host.bus_we;
  wire [2:0] s = d.u_host.w_s;
  wire cp = d.u_chip.ctrl_pend, en = d.u_chip.enable[0], rs = d.u_chip.restart[0];
  wire pin8 = d.u_chip.pin_out[8];
  wire [31:0] v = d.u_host.bus_wdata;
  wire [15:0] a = d.u_host.bus_addr;
  reg  [121:0] last = 0;
  wire [121:0] now = {tog, s, ev, pend, we, cp, en, rs, exec, pin8, uo[0], pc, instr, mem0, ui[6]};
  always @(posedge clk) begin
    #1;
    if ((now >> 1) !== (last >> 1))
      $display("edge %0d: w_tog=%b w_s=%b w_ev=%b w_pend=%b bus_we=%b addr=%h data=%h mem0=%h | ctrl_pend=%b restart=%b en=%b pc=%h instr=%h exec=%b pin_out8=%b uo0=%b",
               n, tog, s, ev, pend, we, a, v, mem0, cp, rs, en, pc, instr, exec, pin8, uo[0]);
    last = now;
  end

  initial begin
    repeat (2) @(posedge clk); ui[7] = 1;    // a CS rise resets the SCK side
    repeat (3) @(posedge clk); rst_n = 1;
    clks(10);
    write(16'h0000, 32'h50020008, 32'h60000000, 1);   // IMEM[0..1]
    clks(20);
    $display("--- CTRL");
    write(16'h0801, 32'h00000101, 0, 0);              // restart + enable core 0
    clks(10);
    $finish;
  end
endmodule
```

Each line is sampled 1 ns after the edge. With `SCK_HALF=4`, `PH=0.1`:

| Edge | Change |
|---|---|
| 467 | `w_tog` 1. The last SCK rise of word 0 came 0.1 ns after this edge |
| 468 | `w_s` 001 |
| 469 | `w_s` 011, `w_ev` 1 |
| 470 | `w_s` 111, `w_pend` 1 |
| 471 | `bus_we` 1, addr 0000, data 50020008 |
| 472 | `mem0` 50020008 |
| 473 | `instr` 50020008 (core still disabled) |
| 1207 | `w_tog` 1. The last SCK rise of the CTRL word |
| 1211 | `bus_we` 1, addr 0801, data 00000101 |
| 1212 | `ctrl_pend` 1, `restart` 1 |
| 1213 | `en` 1, `restart` 0, `exec` 1, instr 50020008, `pin_out8` 0 |
| 1214 | `pin_out8` 1, `uo0` 1, pc 001, instr 60000000 |

The SCK rise came 7 edges before the pin, so nearly 7 clocks, or 140 ns. With
`SCK_HALF=1`, `PH=19.9`, the SCK rise falls just before an edge. `bus_we` is at
edge 332 and the pin at edge 335. That SCK rise landed 0.1 ns before edge 329,
so it is 6 clocks, or 120 ns.

## Appendix B: stale lines a judge could catch

These were found while writing this page. They are outside its scope, so they
were not changed.

* `docs/methodology.md:144` says nothing formally proves synthesis preserved the
  RTL. `docs/equivalence.md` now proves the netlist equal to the RTL.
* `docs/pio-comparison.md:75` gives "about 44 MHz slow corner". Run fo6 meets
  50 MHz at all three.
* `docs/pio-comparison.md:386` says "low-speed only". Full-speed USB firmware
  exists (`README.md:170`).
* `docs/eth10-cut-through.md:18-21` on `main` still shows 10.0-10.9 clocks. The
  fixes are on the local branch `improve-2026-11`. See
  `docs/verification-story.md`.
* `README.md:174` on `main` still says a flat 200 ns.

`AUTHOR TO ANSWER`: `docs/methodology.md:37` says "Claude Opus 5.5 and Astra",
while the table below it and `docs/review.md` name Codex as the reviewer. Are
these the same? The claims audit left this for you
(`docs/claims-audit.md:130-132`).
