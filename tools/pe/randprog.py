"""Constrained-random instruction and configuration generators for verification."""
from . import isa, sim


def _target(rng, bank, bank_words):
    """A jump target: usually inside this bank (its core's own)."""
    if rng.random() < 0.03:
        return rng.randrange(1024)  # anywhere (another bank faults)
    return (bank << 8) | rng.randrange(bank_words)


def _live_pin(rng):
    """A pin the lockstep stimulus toggles: uio 0-7 or ui 16-20."""
    return rng.choice([*range(8), *range(16, 21)])


def _sd(rng):
    """Side/delay field. The split depends on the core's side-set config, so keep
    the upper bits mostly clear (otherwise they can become huge delays)."""
    r = rng.random()
    if r < 0.6:
        return rng.randrange(4) if rng.random() < 0.3 else 0
    if r < 0.9:
        return rng.getrandbits(8) & 0xE3  # side bits for configs with SIDE_COUNT<=3
    return rng.getrandbits(8)


# Opcode weights per program profile. "fifo" keeps the FIFOs moving (pulls and
# pushes racing the host); "wait" exercises edge WAITs, qualifiers and timeouts.
PROFILES = {
    #         JMP WAIT IN OUT PUSH PULL MOV SET LDI ALU PIN FLAG HALT rsvd
    "mixed": [14, 8, 10, 10, 5, 5, 10, 6, 6, 12, 6, 4, 0.5, 1],
    "fifo":  [10, 3, 12, 14, 14, 14, 6, 3, 4, 6, 3, 2, 0.2, 0.5],
    "wait":  [12, 30, 6, 6, 3, 3, 6, 4, 6, 8, 6, 4, 0.2, 0.5],
}


def random_profile(rng):
    return rng.choices(list(PROFILES), weights=[6, 2, 2])[0]


def random_instr(rng, bank, bank_words, profile="mixed"):
    op = rng.choices(
        [isa.OP_JMP, isa.OP_WAIT, isa.OP_IN, isa.OP_OUT, isa.OP_PUSH, isa.OP_PULL, isa.OP_MOV,
         isa.OP_SET, isa.OP_LDI, isa.OP_ALU, isa.OP_PIN, isa.OP_FLAG, isa.OP_HALT, 13 + rng.randrange(19)],
        weights=PROFILES[profile])[0]
    sd = _sd(rng)
    if op == isa.OP_JMP:
        x = (rng.randrange(16) << 15) | (rng.randrange(32) << 10) | _target(rng, bank, bank_words)
    elif op == isa.OP_WAIT:
        x = rng.getrandbits(19)
        if rng.random() < 0.7:
            x |= 1 << 10  # usually with timeout so programs keep moving
        if rng.random() < 0.7:  # mostly pins the stimulus toggles (uio 0-7, ui 16-20)
            x = (x & ~(31 << 11)) | (_live_pin(rng) << 11)
        if rng.random() < 0.7:
            x = (x & ~31) | _live_pin(rng)
    elif op == isa.OP_OUT and rng.random() < 0.1:
        # computed jump: keep it mostly in range by using small counts
        x = (isa.PC << 15) | rng.randrange(32)
    elif op in (isa.OP_IN, isa.OP_OUT) and rng.random() < 0.15:
        x = (isa.LINE << 15) | rng.randrange(1, 9)   # the line unit (core 0), small counts
    elif op in (isa.OP_IN, isa.OP_OUT) and rng.random() < 0.4:
        x = (rng.randrange(13) << 15) | 1  # 1-bit shifts feed the CRC tap
    elif op == isa.OP_MOV and rng.random() < 0.2:
        x = rng.getrandbits(19)
        x = (x & ~(0xF << 15)) | (isa.CRC << 15) if rng.random() < 0.5 else (x & ~(0xF << 9)) | (isa.CRC << 9)
    else:
        x = rng.getrandbits(19)
    if op == isa.OP_WAIT and rng.random() < 0.5:
        x &= ~(1 << 6)  # half without qualifier
    if op == isa.OP_WAIT and rng.random() < 0.25:
        # kind 7: masked pins != R[idx[1:0]]; a mask of a few pins, so it often waits
        to = int(rng.random() < 0.7)
        x = ((isa.W_DIFF << 16) | (rng.randrange(4) << 11) | (to << 10) | (rng.randrange(4) << 8)
             | (rng.getrandbits(8) & rng.getrandbits(8)))
    return isa.enc(op, sd, x)


def random_program(rng, bank, bank_words, profile="mixed"):
    """One random instruction word for `bank` (call once per word)."""
    if rng.random() < 0.02:
        return rng.getrandbits(32)  # completely random word
    return random_instr(rng, bank, bank_words, profile)


def random_config_writes(rng, bank_words):
    """(addr, data) register writes for a random but sane configuration."""
    w = []
    for c in range(4):
        base = lambda o: sim.core_reg(c, o)
        if rng.random() < 0.4:                      # slower ticks: WAIT edges between ticks
            div = (1 << 8)
        else:
            div = (rng.randrange(1, 4) << 8) | (rng.getrandbits(8) if rng.random() < 0.5 else 0)
        w.append((base(sim.C_CLKDIV), div))
        w.append((base(sim.C_PINCTRL0), rng.getrandbits(32)))
        w.append((base(sim.C_PINCTRL1), rng.getrandbits(32)))
        w.append((base(sim.C_SHIFTCTRL), rng.getrandbits(32)))
        eb = c                                      # its own bank
        w.append((base(sim.C_ENTRY), (eb << 8) | rng.randrange(bank_words)))
        wb = c
        lo, hi = sorted(rng.randrange(bank_words) for _ in range(2))
        if rng.random() < 0.5:
            w.append((base(sim.C_WRAP), ((wb << 8) | lo) | (((wb << 8) | hi) << 16)))
        if rng.random() < 0.7:
            w.append((base(sim.C_CRCCTRL), rng.getrandbits(22)))
            w.append((base(sim.C_CRC), rng.getrandbits(16)))
        for r in range(4):
            w.append((base(sim.C_R0 + r), rng.getrandbits(16) if rng.random() < 0.2 else rng.randrange(12)))
    w.append((sim.A_POWN_LO, rng.getrandbits(32)))
    w.append((sim.A_POWN_HI, rng.getrandbits(32)))
    w.append((sim.A_OD, rng.getrandbits(8)))
    return w


def random_bypass_writes(rng):
    """SYNC_BYPASS: half the time some pad inputs one synchroniser flop fresher.
    (Its own generator call, so the other configuration streams stay as they were.)"""
    return [(sim.A_SYNCBYP, rng.getrandbits(32))] if rng.random() < 0.5 else []
