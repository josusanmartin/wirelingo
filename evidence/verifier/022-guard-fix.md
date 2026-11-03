Commit: 851a373  Base: 8a6dd8f  Branch: hw-kit
Item: fixes for inbox/019-fabric-guard.report.md and the should-fix in 020. Do not touch the Kria.
Real `xmutil listapps` rows read from the board on 2026-11-03 (spacing as captured; the earlier capture of protoemu40 was cut at 110 columns but shows the row shape):
                     Accelerator  Accel_type                    Base    Pid   Base_type  #slots(RPU+PL+AIE)    slot->handle
                    protoemu40    XRT_FLAT              protoemu40  id_ok    XRT_FLAT             (0+0+0)              -1
and, from another read, the active row of k26-starter-kits ending `(0+0+0)  0->0,`. Feed these exact lines to the guard and confirm protoemu inactive refuses, and that a protoemu row in the active shape passes. The builder says its middle columns were invented: if real rows would be refused, say so. Refusing is safe; a false pass is not.
Claims:
1. The guard requires the exact table: the real header, 7 columns, (n+n+n), -1 or n->n, exactly one active row and a known name; any other line refuses, `protoemu: Load Error: -1` included.
2. /fpga-region/firmware-name, fpga0/state and the installed bitstream are all required, and a missing one refuses. Firmware-name removal on unload is from memory: judge it.
3. PE_ACTIVE_APP and --active-app are removed.
4. Mutants in test/mutants.py, `python3 test/mutants.py 'hw-*'` 71/71; model 1910 passed.
5. A board that answers is never power cycled; recover never expires; the load is one command at a time; 20 mA cites DS925.
