# protoemu tests

* `test.py`: top-level cocotb tests through the real SPI host port (UART, SPI,
  I2C, USB low-speed device, 10BASE-T TX, FIFO read race), on the SRAM build.
* `test_lockstep.py`: RTL vs the golden model (`tools/pe/sim.py`) on random
  programs, pins and host traffic, compared every cycle (`TB=chip`).
* `model/`: firmware on the cycle-accurate model, assembler and driver tests (pytest).

```sh
make                                   # top-level tests (2 cores, 2 SRAM banks, 8-deep FIFOs)
LOCKSTEP_SEEDS=200 make TB=chip PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3   # tapeout config
make TB=chip IMEM=flop                 # 4-core flop build (faster)
make GATES=yes                         # gate level: copy runs/wokwi/final/nl/*.nl.v to
                                       # gate_level_netlist.v first (flow/gl_test.sh does this)
cd .. && PYTHONPATH=tools python -m pytest test/model
```

Waveforms: `tb.fst` (`gtkwave tb.fst` or `surfer tb.fst`).
