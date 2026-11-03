# Wirelingo: building an ASIC with AI agents, without chip-design experience

## In the author's words

I'm not a chip designer.

My background is in software and entrepreneurship, and before starting Wirelingo I had no experience designing an ASIC. I had never taken a chip through synthesis, place and route, timing closure, DRC, LVS, antenna checks, or any of the other steps between RTL and something that can actually be manufactured.

I started the project on September 30, 2026.

The unusual part is that I did not first spend months learning semiconductor design and then build the chip. I started building immediately, using frontier AI models and autonomous coding agents as the domain experts.

In practice, I let the agents lead. They propose architectures, write RTL, build verification infrastructure, investigate timing failures, work through physical-design problems, and review each other's work. I supervise the project, make the higher-level decisions, question assumptions, and decide what evidence I need before accepting that something works. Much of the time I am working on other projects while the agents continue pushing the design forward.

That makes Wirelingo partly an ASIC project and partly an experiment in a new way of doing engineering.

The interesting question for me is not whether an AI can generate Verilog. It obviously can.

The question is what happens when AI allows someone to operate far outside their existing field of expertise.

I cannot personally inspect thousands of gates and determine that the design is correct. I cannot rely on reading the RTL and saying that it looks reasonable. And asking another AI whether the first AI did a good job does not solve the problem either.

So the project quickly became about evidence.

Instead of trusting the implementation, I try to construct independent ways for it to fail: two implementations of the specification compared cycle by cycle, formal proofs, mutation testing, independent protocol decoders, FPGA execution, gate-level simulation, timing analysis, physical verification, and a separate AI agent whose job is to attack the assumptions made by the others.

That distinction has become the most interesting part of the project for me.

AI gives me access to expertise I do not personally have. But access to expertise is not the same thing as knowing that the answer is correct.

Wirelingo is my attempt to find out whether sufficiently strong verification can bridge that gap.

If it can, the larger implication is much more interesting than this particular chip: the limiting factor in highly technical engineering work may increasingly become not whether a person knows how to produce the implementation themselves, but whether they know how to specify the problem, direct capable agents, and construct enough independent evidence to know when the result can be trusted.

## How it was built

The author worked with Claude Opus 5.5 and Astra. The rest of this page is the record behind the
statement above: what the agents did, what the author did, and the evidence
that stands between an agent's claim and the GDS.

## Who did what

| Role | Who | What it did here |
|---|---|---|
| Direction and judgment | the author | goals and scope (protocols, tile size, two cores), what to cut, when to stop exploring, every spec change, hardware and accounts (FPGA board, licenses, build machines) |
| Engineering | Claude (Claude Code) | ISA spec, golden model, assembler, RTL, firmware, cocotb/formal/mutation tests, LibreLane flow, physical experiments, FPGA bring-up |
| Independent review | Codex | read the repo cold, reproduced bugs with tests and proposed fixes ([review.md](review.md)) |
| Parallel work | Claude sub-agents | self-contained tasks in their own git worktrees (FPGA bring-up, the reverse-engineering demo), reporting measured results back |

At commit 19ebe98, 164 of the repository's 184 commits are co-authored by Claude.

## The rule: no claim without independent evidence

Agents write fluent, confident text, and they make mistakes. The ones that
matter here are confident: "the fix is done", "this meets timing", "exactly
equivalent". So nothing in this repository rests on an agent's word. Each
claim has to be checked by something that doesn't share the agent's
assumptions:

* **Two implementations of one spec.** [isa.md](isa.md) is normative. A
  cycle-accurate Python model and the RTL implement it separately, and
  constrained-random programs, pins and host traffic run on both, compared
  every cycle ([verification.md](verification.md) §2). This catches an
  implementation slip on either side, but it is weaker against a
  misunderstanding of the spec itself: the same agents wrote both, and two
  languages don't make two minds. The checks below that don't share the
  spec's assumptions are what cover that.
* **Formal proofs** for the parts where a random test can miss a corner: the
  FIFOs against reference queues, host-port bus safety, core invariants, the
  line unit's phase tracking.
* **Mutation testing** checks the checks. Injected bugs in the model, RTL,
  firmware and assembler must each make a named test fail
  (`test/mutants.py`, 34 of 34 killed). Tests that pass whatever the code
  does are worthless, and agents write those too.
* **Third-party oracles.** sigrok's protocol decoders, written by other
  people for logic analyzers, decode the waveforms of the UART, SPI, I2C,
  USB (low and full speed) and CAN tests, and of the post-freeze protocols
  where a decoder exists, and must agree with what each test expected. They share no code or assumptions with our device models, so a
  bug common to the firmware and its model still shows up.
* **Device models that see only pins.** Firmware is tested against
  independent models of a UART, an I2C EEPROM, a USB host and so on, not
  against the code that drives them.
* **Physical signoff by script.** `flow/signoff.py` applies Tiny Tapeout's
  criteria to the run's own reports, and Tiny Tapeout's precheck runs on the
  GDS. The gate-level netlist reruns the top-level tests.
* **Hardware.** The RTL runs on a Kria KV260 FPGA at the chip's 50 MHz:
  firmware loads, UART crosses real pins, and the quad host port moves
  193.6 Mbit/s, the figure simulation predicted ([fpga/kv260](../fpga/kv260/README.md)).
  Most firmware, the post-freeze protocols among it, and USB enumeration
  against a replayed host waveform also ran there, against the other core or
  the FPGA's pattern generator. This is the RTL in FPGA fabric, not the IHP
  netlist or its pads.
* **A second agent as adversary.** Codex reviewed the work cold, and Claude
  reviewed Codex's fixes. Neither was trusted to grade its own work.

## What this caught

Each of these was written or approved by an agent, and caught by one of the
mechanisms above rather than by the agent that made it:

| What went wrong | Caught by |
|---|---|
| The assembler encoded `nop` with an old field layout | a firmware test on the model (USB bit count off) |
| A global reset during an SPI transaction left the host port mid-command (R3) | Codex's review, reproduced as a test |
| The timing constraints cut every SCK/clk path, so the crossings were never timed (R2) | Codex's review |
| A USB device accepted a DATA packet with a corrupt PID (R4) | Codex's review |
| Codex's own signoff gate demanded slow-corner timing Tiny Tapeout doesn't require, so no run could ever pass | Claude reviewing the review |
| Codex's reset fix was correct, but its netlists congested routing | physical experiments: a dozen place-and-route runs, then a different global-routing setting |
| The two-core USB firmware took its own ACK for the host's | the concurrent RTL service test, once an unrelated cleanup made the host software faster |
| Claude described the FPGA as running "exactly" and "at full speed" before either was measured | a second review of that explanation; the claims were then measured on the board |
| The assembler computed `/` exactly and truncated each half of a split delay, so bidirectional DShot ran one clock short per level at 50 MHz | the KV260 board: `la_capture` measured lows of 600 and 1220 ns and a 1640 ns bit; the cycle model then showed the same, and `/` became integer division |
| `ws2812` held a 0 bit high for 2 ticks, not the header's 3, so at 50 MHz some 0 bits were 240 ns, outside WS2812B's T0H window | the cycle-model pass of the KV260 run script at 50 MHz, before the board run |
| `can_rx` missed the stuff bit after a CRC ending in five equal bits and drove its ACK a bit early (about one random frame in 31; the model tests' frames avoided it) | the same cycle-model pass, on a generator frame, before the board run |

The full lists are in [verification.md](verification.md) (bugs found by the
flow) and [review.md](review.md) (the independent review).

## What the human had to do

* **Set direction and cut scope.** Agents will happily keep optimizing.
  Deciding that two cores, not three, is the tapeout configuration, that a
  clean repository and signoff come before new features, and when an
  exploration had run long enough were the author's calls.
* **Own the spec changes.** Dropping cross-bank instruction fetch made the
  chip routable but changed what programs can do; that was the author's
  decision, not the agent's.
* **Provide the real world.** Accounts, licenses, build machines and the FPGA
  board, and the decision of what to publish and when.
* **Ask "how do you know?"** Most of the value came from refusing a
  summary until it pointed at a test, a report or a measurement.
* **Push for comparisons and further improvement.** The author asked which
  reference designs should be used to judge novelty, then asked for better
  efficiency and speed instead of a justification that the current design was
  already good enough.
* **Bring the hardware you have.** The FPGA validation put to use a Kria
  KV260 board the author had bought some time before; the agents set it up,
  built for it and ran the chip's RTL on it over the network.

## Limits

* The agents made every engineering decision below the level of goals and
  scope. The verification is broad, but it checks the spec as the agents
  wrote it; it can't catch a spec that asks for the wrong thing.
* Synthesis preserving the RTL's behaviour is proved for run fo6's netlist
  ([equivalence.md](equivalence.md)): functionally, at zero delay, with the
  PDK's cell models, under the assumptions listed there. The inside of the
  cells is not checked; timing is left to STA and the timing-annotated
  gate-level tests.
* The FPGA validates the logic at speed, not the IHP silicon: pads, real
  timing and the latch FIFOs are only covered by static timing analysis,
  timing-annotated gate-level simulation of the exercised paths and,
  eventually, the chip itself.
