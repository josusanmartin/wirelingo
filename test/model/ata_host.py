"""TX words for firmware/ata_pio.pasm: what a host driver would send. Pin image:
{CS1-, CS0-, DA2:0, DD7:0}. Shared by the model and RTL tests."""
CMD_BLK, CTL_BLK = 0x1000, 0x0800
OP_WR, OP_RD, OP_WAIT, OP_BREAD, OP_BWRITE, OP_DELAY = range(6)
R_DATA, R_ERR, R_SC, R_SN, R_CL, R_CH, R_DH, R_CMD = range(8)
R_DEVCTL = 6
TIMED_OUT, REFUSED = 0x100, 0x200


def word(op, image=0, arg=0):
    return (op << 29) | (image << 16) | arg


def reg(r, v=0):
    return CMD_BLK | (r << 8) | v


def ctl(r, v=0):
    return CTL_BLK | (r << 8) | v


def wr(r, v):
    return word(OP_WR, reg(r, v))


def rd(r):
    return word(OP_RD, reg(r))


def wait(units=20):
    return word(OP_WAIT, 0, units)


def bread(words):
    return word(OP_BREAD, 0, words - 1)


def bwrite(words):
    return word(OP_BWRITE, 0, words - 1)


def delay(n):
    return word(OP_DELAY, 0, n - 1)


def be_words(data):
    """4 bytes per FIFO word, first byte in [31:24]."""
    return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]


def lba(n, count=1):
    return [wr(R_DH, 0xE0 | (n >> 24)), wr(R_SC, count), wr(R_SN, n & 255), wr(R_CL, (n >> 8) & 255),
            wr(R_CH, (n >> 16) & 255)]


def pio_defines(mode):
    """Assembler defines for firmware/ata_pio.pasm at a PIO mode. Low time is
    TS + 4 ticks. Modes 1 and 2 share a build, as do 3 and 4: docs/blind-test.md."""
    ts, rrec, rgap, wrec, wgap = {0: (12, 3, 8, 3, 5), 1: (13, 0, 0, 0, 0), 2: (13, 0, 0, 0, 0),
                                  3: (3, 0, 0, 0, 0), 4: (3, 0, 0, 0, 0)}[mode]
    return {"TS": ts, "RREC": rrec, "RGAP": rgap, "WREC": wrec, "WGAP": wgap}


def set_mode(mode):
    """SET FEATURES 03h, PIO flow control mode (ATA-3 Table 16: 00001nnn)."""
    return [wr(R_ERR, 0x03), wr(R_SC, 0x08 | mode), wr(R_CMD, 0xEF)]


# SRST for 5.76 us, then 2 ms (the wait after clearing it: from memory, see ata_device.py)
SRST = [word(OP_WR, ctl(R_DEVCTL, 0x06)), delay(9), word(OP_WR, ctl(R_DEVCTL, 0x02)), delay(3126)]
