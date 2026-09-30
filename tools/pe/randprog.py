"""Constrained-random instruction and configuration generators for verification."""
from . import isa, sim


def _target(rng, bank, bank_words, owners):
    """A jump target: usually inside a bank owned by this bank's owner."""
    owner = owners[bank]
    mine = [b for b in range(4) if owners[b] == owner]
    if rng.random() < 0.03:
        return rng.randrange(1024)  # anywhere (may fault)
    b = bank if rng.random() < 0.8 else rng.choice(mine)
    return (b << 8) | rng.randrange(bank_words)


def _sd(rng):
    """Side/delay field. The split depends on the core's side-set config, so keep
    the upper bits mostly clear (otherwise they can become huge delays)."""
    r = rng.random()
    if r < 0.6:
        return rng.randrange(4) if rng.random() < 0.3 else 0
    if r < 0.9:
        return rng.getrandbits(8) & 0xE3  # side bits for configs with SIDE_COUNT<=3
    return rng.getrandbits(8)


def random_instr(rng, bank, bank_words, owners):
    op = rng.choices(
        [isa.OP_JMP, isa.OP_WAIT, isa.OP_IN, isa.OP_OUT, isa.OP_PUSH, isa.OP_PULL, isa.OP_MOV,
         isa.OP_SET, isa.OP_LDI, isa.OP_ALU, isa.OP_PIN, isa.OP_FLAG, isa.OP_HALT, 13 + rng.randrange(19)],
        weights=[14, 8, 10, 10, 5, 5, 10, 6, 6, 12, 6, 4, 0.5, 1])[0]
    sd = _sd(rng)
    if op == isa.OP_JMP:
        x = (rng.randrange(16) << 15) | (rng.randrange(32) << 10) | _target(rng, bank, bank_words, owners)
    elif op == isa.OP_WAIT:
        x = rng.getrandbits(19)
        if rng.random() < 0.7:
            x |= 1 << 10  # usually with timeout so programs keep moving
    elif op == isa.OP_OUT and rng.random() < 0.1:
        # computed jump: keep it mostly in range by using small counts
        x = (isa.PC << 15) | rng.randrange(32)
    elif op in (isa.OP_IN, isa.OP_OUT) and rng.random() < 0.4:
        x = (rng.randrange(13) << 15) | 1  # 1-bit shifts feed the CRC tap
    elif op == isa.OP_MOV and rng.random() < 0.2:
        x = rng.getrandbits(19)
        x = (x & ~(0xF << 15)) | (isa.CRC << 15) if rng.random() < 0.5 else (x & ~(0xF << 9)) | (isa.CRC << 9)
    else:
        x = rng.getrandbits(19)
    if op == isa.OP_WAIT and rng.random() < 0.5:
        x &= ~(1 << 6)  # half without qualifier
    return isa.enc(op, sd, x)


def random_program(rng, bank, bank_words, owners):
    if rng.random() < 0.02:
        return rng.getrandbits(32)  # completely random word
    return random_instr(rng, bank, bank_words, owners)


def random_config_writes(rng, bank_words, owners):
    """(addr, data) register writes for a random but sane configuration."""
    w = []
    for c in range(4):
        base = lambda o: sim.core_reg(c, o)
        if rng.random() < 0.6:
            div = (1 << 8)
        else:
            div = (rng.randrange(1, 4) << 8) | (rng.getrandbits(8) if rng.random() < 0.5 else 0)
        w.append((base(sim.C_CLKDIV), div))
        w.append((base(sim.C_PINCTRL0), rng.getrandbits(32)))
        w.append((base(sim.C_PINCTRL1), rng.getrandbits(32)))
        w.append((base(sim.C_SHIFTCTRL), rng.getrandbits(32)))
        mine = [b for b in range(4) if owners[b] == c] or [rng.randrange(4)]
        eb = rng.choice(mine)
        w.append((base(sim.C_ENTRY), (eb << 8) | rng.randrange(bank_words)))
        wb = rng.choice(mine)
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
