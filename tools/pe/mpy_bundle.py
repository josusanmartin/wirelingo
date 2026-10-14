"""Generate tools/micropython/protoemu_fw.py: assembled firmware + register settings
for the demo board, ready for ProtoEmu.load(**PROGRAM).

    python -m pe.mpy_bundle            # from tools/

Default pin map (logical pins, docs/isa.md §2):
    UART:  TX = uo0 (P8), RX = ui0 (P16); *_V24: both on virtual pin 24 (no wiring)
    SPI:   SCK = uio2, MOSI = uio3, MISO = uio4; CS = uio5 for the SPI_MODE*,
           SPI_PACKED_MODE* and SPI_PRELOADED images (divider 12: 4 ticks per
           bit, 1 MHz SCK at 48 MHz; the board helpers can change it)
    UART capture (UART_CAPTURE, UART_PACKED): RX = uio6 (P6), 115200 8N1
    I2C:   SDA = uio0, SCL = uio1 (open drain: set OD_MASK and PIN_OUT first)
    WS2812: DOUT = uo1 (P9)
    10BASE-T TX: TD+ = uo0, TD- = uo1 (40 MHz project clock; pe.eth10_send())
    10BASE-T RX: comparator output = ui0 (P16), 50 MHz, line period 5
    USB_ENUM_LOW / USB_ENUM_FULL: the autonomous USB device (firmware/usb_enum.pasm,
        pe.usbenum) as register-write bursts for bringup.usb_enum(): low speed
        D+ = uio0, D- = uio1 (1.5k pull-up on D-); full speed D- = uio0, D+ = uio1
        (pull-up on D+)
Clock dividers assume 48 MHz except the explicitly named Ethernet images.
"""
import pathlib
import argparse

from . import asm, host, usbdev, usbenum

ROOT = pathlib.Path(__file__).resolve().parents[2]
FW = ROOT / "firmware"
F_CLK = 48e6


def prog(name, core, defines=None, program=None, **cfg):
    text = (FW / f"{name}.pasm").read_text()
    if program is None:
        p = asm.assemble(text, defines or {})
    else:
        p = {q.name: q for q in asm.assemble_all(text, defines or {})}[program]
    base = core * 256
    host.load_program(core, p, base, **cfg)
    c = host.core_config(p, **cfg)
    words = p.relocate(base)
    wrap = (base + p.wrap_bottom) | ((base + p.wrap_top) << 16)
    d = dict(core=core, base=base, words=words, clkdiv=c["clkdiv"], pinctrl0=c["pinctrl0"],
             pinctrl1=c["pinctrl1"], shiftctrl=c["shiftctrl"], wrap=wrap)
    if "filter" in p.labels:
        d["filter_index"] = p.labels["filter"]
    return d


def bursts(writes):
    """(addr, data) writes as [addr, [data...]] runs of consecutive addresses,
    in the original order: one SPI transaction per run on the board."""
    out = []
    for addr, data in writes:
        if out and out[-1][0] + len(out[-1][1]) == addr:
            out[-1][1].append(data)
        else:
            out.append([addr, [data]])
    return out


def usb_enum(speed):
    """Everything up to the start (the board starts core 0 itself)."""
    return dict(speed=speed, writes=bursts(usbenum.load_writes(usbenum.assemble(speed), speed, start=False)))


SPI_SCK, SPI_MOSI, SPI_MISO, SPI_CS, UART_CAPTURE_RX = 2, 3, 4, 5, 6
SPI_DIV = 12


def spi_uart_images():
    """spi_master_modes, spi_master_packed, spi_master_preloaded_once,
    uart_rx_capture and uart_rx_packed on core 0, at the board pins above."""
    spi = dict(out_pins=(SPI_MOSI, 1), side_pins=SPI_SCK, in_pins=SPI_MISO, clkdiv=SPI_DIV << 8)
    uart = dict(in_pins=UART_CAPTURE_RX, clkdiv=host.clkdiv_for(F_CLK, 8 * 115200))
    out = {}
    for mode in range(4):
        out[f"SPI_MODE{mode}"] = prog("spi_master_modes", 0, {"CPOL": mode >> 1},
                                      program=f"spi_master_cpha{mode & 1}", **spi)
    for mode in range(4):
        out[f"SPI_PACKED_MODE{mode}"] = prog(
            "spi_master_packed", 0, {"CPOL": mode >> 1, "WORD_BITS": 32, "CS": SPI_CS},
            program=f"spi_packed_cpha{mode & 1}", **spi)
    out["SPI_PRELOADED"] = prog("spi_master_preloaded_once", 0,
                                {"CPOL": 0, "WORD_BITS": 32, "CS": SPI_CS}, **spi)
    out["UART_CAPTURE"] = prog("uart_rx_capture", 0, {"RX": UART_CAPTURE_RX}, **uart)
    out["UART_PACKED"] = prog("uart_rx_packed", 0, {"RX": UART_CAPTURE_RX}, **uart)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only stale bundle check")
    a = ap.parse_args()
    progs = {
        "UART_TX": prog("uart_tx", 0, out_pins=(8, 1), side_pins=8,
                        clkdiv=host.clkdiv_for(F_CLK, 8 * 115200)),
        "UART_RX": prog("uart_rx", 1, {"RX": 16}, in_pins=16,
                        clkdiv=host.clkdiv_for(F_CLK, 8 * 115200)),
        # internal loopback for bring-up: TX on virtual pin 24 (a wire between cores)
        "UART_TX_V24": prog("uart_tx", 0, out_pins=(24, 1), side_pins=24,
                            clkdiv=host.clkdiv_for(F_CLK, 8 * 115200)),
        "UART_RX_V24": prog("uart_rx", 1, {"RX": 24}, in_pins=24,
                            clkdiv=host.clkdiv_for(F_CLK, 8 * 115200)),
        "SPI_MASTER": prog("spi_master", 0, out_pins=(3, 1), side_pins=2, in_pins=4,
                           clkdiv=host.clkdiv_for(F_CLK, 4 * 1e6)),
        "I2C_MASTER": prog("i2c_master", 0, {"SDA": 0, "SCL": 1}, out_pins=(0, 1), in_pins=0,
                           clkdiv=host.clkdiv_for(F_CLK, 8 * 100e3)),   # 8 ticks per SCL period
        "WS2812": prog("ws2812", 0, side_pins=9, clkdiv=host.clkdiv_for(F_CLK, 8e6)),
        # needs a 40 MHz project clock: 4 clocks per bit; TD+ = uo0, TD- = uo1
        "ETH10_TX": prog("eth10_tx", 0, {"F_GO": 0}, side_pins=8, clkdiv=1 << 8),
        "ETH10_RX": prog("eth10_rx", 0, clkdiv=1 << 8),
        "USB_RX": prog("usb_ls_rx", 0, usbdev.defines(), clkdiv=1 << 8),
        "USB_TX": prog("usb_ls_tx", 1, usbdev.defines(), out_pins=(0, 2), clkdiv=1 << 8),
        "USB_LINE": prog("usb_ls_dev", 0, usbdev.defines(), clkdiv=1 << 8),
        "USB_ENUM_LOW": usb_enum("low"),
        "USB_ENUM_FULL": usb_enum("full"),
    }
    progs.update(spi_uart_images())
    out = ['"""protoemu firmware bundle (generated by tools/pe/mpy_bundle.py; do not edit).',
           "", "    from protoemu import ProtoEmu", "    from protoemu_fw import UART_TX",
           "    pe = ProtoEmu(tt); pe.load(**UART_TX); pe.start(1); pe.push(0, ord('A'))", '"""', ""]
    for name, d in progs.items():
        out.append(f"{name} = {d!r}")
        out.append("")
    dst = ROOT / "tools" / "micropython" / "protoemu_fw.py"
    rendered = "\n".join(out)
    usb_src = (ROOT / "tools/pe/usbdev.py").read_text().replace(
        "from . import sim, usb", "import protoemu as sim\nimport usb_bits as usb")
    outputs = {dst: rendered,
               dst.with_name("usb_device.py"): "# Generated by pe.mpy_bundle; do not edit.\n" + usb_src,
               dst.with_name("usb_bits.py"): "# Generated by pe.mpy_bundle; do not edit.\n" + (ROOT / "tools/pe/usb.py").read_text()}
    if a.check:
        for path, content in outputs.items():
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"{path.name} is stale; run python -m pe.mpy_bundle")
        print("firmware bundle matches validated sources")
        return
    for path, content in outputs.items():
        path.write_text(content)
    print(f"wrote {dst} ({', '.join(progs)})")


if __name__ == "__main__":
    main()
