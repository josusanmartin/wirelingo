# Enumeration sweep on the model, 2026-11-04

Evidence level: **model** (the cycle model, `tools/pe/sim.py`). Not RTL, not
the board.

`test/model/usb_fs_enum_sweep.py` (committed at c335316; what it does and
its parameters are in its docstring) run on runner1 against `git archive`
copies of three commits, with the script from c335316:

    PYTHONPATH=<tree>/tools ~/protoemu-flow/test-venv/bin/python \
        test/model/usb_fs_enum_sweep.py --tree <tree> --runs 40 --procs 8

(default `--combos`: `2400:0:256:96000,4800:0:16:48000,960:0:8:24000,2400:2400:4:9600,9600:0:64:192000`.)
Each output starts with the sha256 of the software it ran.

| file | software | 20-100 us per SPI transaction (4 combinations) | 200 us per transaction |
|---|---|---|---|
| `out-c184dd6.txt` | before the `pe.usbdev` fixes | 160 of 160 good, 0 re-staged | 6 of 40 bad, all at SET_ADDRESS |
| `out-8c71177.txt` | GET_DESCRIPTOR ZLP fix, first re-stage check | 160 of 160, 0 re-staged | 6 of 40, the same runs |
| `out-64b59c2.txt` | the re-stage checks around FSET | 160 of 160, 0 re-staged | 6 of 40, the same runs |

The 6 failures (runs 4, 15, 22, 28, 31, 37) are `SETUP not acknowledged` for
SET_ADDRESS: the model's host sends it 0.1-3 ms after the port reset, and
software that slow has not restarted the core by then (usbmon shows Linux
taking 64 ms there). They say nothing about SET_CONFIGURATION.

At c335316 and later `tools/pe/usbdev.py` and `firmware/usb_fs_dev.pasm` are
byte-identical to d14f094's (the hashes in `out-64b59c2.txt`);
`tools/pe/usbfsdata.py` differs only in its docstring (dbbe0e8).

## sha256

```
0449b19c1d54ad66dc486ad4b2bbe140d72c056aa103e6013ce21764f891ddcf  out-c184dd6.txt
fa826302b24c9f841be3f0c366cfbe6d752d2a9e095f633af14105615b049f07  out-8c71177.txt
fa0fb999147ce03ab212d6d8957f154166890e757f02ea436c099c2ed7f8cd47  out-64b59c2.txt
```
