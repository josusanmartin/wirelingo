"""docs/guide.md's program-size table ("Program sizes below come from the
generated board bundle") against that bundle, tools/micropython/protoemu_fw.py:
each row's numbers, in order, are the word counts of the programs it names.
(Verifier 044: the USB, UART and I2C rows had drifted.)"""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROWS = {   # the row's first cell -> the bundle programs its numbers are, in order
    "UART over virtual pin 24": ("UART_TX_V24", "UART_RX_V24"),
    "USB without line unit": ("USB_RX", "USB_TX"),
    "USB with line unit": ("USB_LINE",),
    "I2C master": ("I2C_MASTER",),
}


def bundle():
    spec = importlib.util.spec_from_file_location("protoemu_fw", ROOT / "tools/micropython/protoemu_fw.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)            # data only: dicts of words and settings
    return module


def test_guide_sizes_match_the_bundle():
    fw = bundle()
    text = (ROOT / "docs/guide.md").read_text()
    for row, programs in ROWS.items():
        m = re.search(rf"^\| {re.escape(row)} \| ([^|]*) / ", text, re.M)
        assert m, f"guide.md has no size row {row!r}"
        numbers = [int(n) for n in re.findall(r"\d+", m.group(1))]
        want = [len(getattr(fw, p)["words"]) for p in programs]
        assert numbers == want, f"guide.md {row!r}: {numbers}, the bundle {dict(zip(programs, want))}"
