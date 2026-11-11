VERDICT: PASS

Item 058 `tidy`: commits 9e8553a, bae67a3, 5132d29, e95f0de and bbf8b11 on e449e40, checked out detached at bbf8b11. I read the diff against e449e40 and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

Every removal is dead code, and the tidy is behaviour-neutral by every check I ran:
- the same 2907 collected model test IDs at both commits;
- identical firmware assembly;
- the model suite at bbf8b11 (2878 passed, 29 skipped, the base's counts);
- 61 of 61 affected `mutants.py` mutants killed;
- 34 of 34 blind-test mutants killed.

The privacy finding the builder reports is real, and I found one more line of it than they listed. Nothing else in the tree hides a private string that way. Five notes follow; none blocks.

## Findings

1. **The builder's tailnet-label finding is confirmed, and it is on five lines of `test_private_hosts.py`, not four.**
   - The label is built from two adjacent string literals at `test/model/test_private_hosts.py:22`, `:58`, `:60`, `:61` and **`:62`** (the `d.md` case, "the admin page of tailnet …"), and at `test/mutants.py:519`. The item lists `:22,58,60,61`.
   - Python joins adjacent literals at parse time. I walked the AST of every tracked `.py` file and scanned every string constant after joining: those six constants are the only ones that hold the label.
   - No `.sh`, YAML, Make, Tcl, Yosys, OCaml or HDL file forms it once adjacent quotes are removed.
   - So the fix the builder proposes for a separate item covers everything, provided it includes `:62`.
   - A test that looks for the label needs it at run time, so splitting a literal can't keep it out of the source. Two ways that work: read it from an environment variable and skip the check when it is unset, or compare a hash of each token against a stored hash.
2. **Privacy sweep of the whole tree at bbf8b11: the label above is the only private value.** I swept all 699 tracked files and give counts here, not the strings.
   - **Home paths.** No home path of the author's. Others that occur:
     - `/home/ubuntu` (the Kria's stock user) in `evidence/kv260-usbfs-zlp-20261103/` logs and scripts;
     - `/home/runner1/protoemu-flow/…` in 3 lines of `evidence/fabric-ping-20261103/c108e09-suite/model.log`;
     - the `/home/pi` and `/home/someone` examples;
     - `/home/<other>` in `.devcontainer/Dockerfile`;
     - `/home/<other>` in a test;
     - `/home/<other>/…` in `test/tb.gtkw`, from the upstream Tiny Tapeout template.
   - **Build machine and provider.** No build-machine hostname (`[build host name class redacted]` followed by digits: 0). The provider's *name* appears 8 times in 6 `evidence/verifier/` files, as a word in earlier verifiers' sweep lists. So does the author's mail provider's name. Reports 041/042 kept these on purpose. Whether a public repo should say which hosting provider the build ran on is the author's call; it doesn't identify a machine.
   - **Emails.** All public addresses: the competition address, the T13 forum, `root@example.org`, and `@click.com` decorators misread as addresses in `flow/odb_*.py`.
   - **Tailnet.** The CGNAT range 100.64.0.0/10 appears only as a search class. Apart from the label in finding 1, `.ts.net` also appears only as a search class.
   - **IPv4.** Only 10.77.0.x, the TAP namespace.
   - **MACs.** Five distinct values. All are locally administered or the broadcast address: `02:57:4c:…`, `02:00:de:ad:be:01`, `ff:ff:…`. So the Kria's own (global) MAC is absent.
   - **Wi-Fi and credentials.**
     - SSID, `psk` and WPA appear only as search classes; the `wpar` hits are a host-port signal.
     - "password" appears only in systemd and AppArmor lines of the Kria's dmesg captures.
     - No private-key block or token pattern.
   - **Binaries.** The 3 binary tracked files (two PNGs and an empty `__init__.py`) have no such strings.
   - **Commit messages.** The five messages have 0 hits. Each ends with the required Co-Authored-By line (5 of 5).
   - **Commit identity.** These five commits carry the same non-noreply author and committer address as every commit before them, built from the provider machine's hostname. That is item AI's (see 057 note 10).
   - **Pre-existing tree text.** `/home/runner1` in that one log, and the `runner1` alias generally, are worth a decision before going public.

### Notes (not blocking)

3. **"All 72 `.pasm` files" is 68 files holding 72 `.program` blocks.** `ls firmware/*.pasm | wc -l` gives 68; `grep -c '^\.program'` summed gives 72. The claim's substance holds (claim 7 below).
4. **Rerunning `remote_test.sh` under load.** The model suite took 29:09 under runner1's shared load (load average about 36 on 20 CPUs from other agents' jobs), just inside the wrapper's 30-minute limit. The builder's combined run hit that limit. A rerun at a busy moment can time out with no failure. That is an infrastructure margin, not this branch.
5. **One kind of mutant applies to this item in a way the item doesn't state.** The builder counts the 234 entries of `test/mutants.py`. `test/blind_mutants.py` has 34 more. Its 9 `hd-*` mutants are killed by `test/model/test_hd44780.py`, which this item changed (two imports removed). I reran all 34 (below).
6. **Line citations.**
   - In docs, the only line citation into a changed file is `docs/explain-wirelingo.md:403`'s `timing.py:25`. Lines 38-41 were rewritten with the same line count, so nothing shifts.
   - Evidence's `ethhost.py:36` is also before a same-length edit (`:62`).
   - The ten `hw_day.py:N` and `test_hw_day.py:N` cites are all in verifier reports 016, 019, 020 and 022, which are kept verbatim. The builder says they were already stale.
   - None of the other 13 changed `.py` files is cited by line anywhere.
7. **`test/gl.log`** held 75 lines with a home path (I counted `/home/` and printed nothing else). Nothing in the tree reads it: the only `gl.log` names in code are a receipt archive's `logs/gl.log` in `test_flow_tools.py:408,438`. `.gitignore:24` is `test/*.log`. It stays in history until item AI.

## The builder's claims

1. **`test/gl.log` removed: holds** (note 7).
2. **`tools/` imports: hold.**
   - Within each changed file, every removed name has 0 remaining `ast.Name` uses: `ethhost.sim`, `eth10_host.sim`, `modelhost.host`, `postfreeze3.Player`, and `timing.OP_SET`, `OP_PIN` and `OP_FLAG`.
   - Across the tree, nothing imports them through those modules. I grepped `.py`, `.md`, `.sh`, YAML and Makefiles for `modelhost.host`, `p3.Player` / `postfreeze3 import … Player`, `timing import … OP_`, `ethhost.sim`, and `from test_… import`.
   - There is no `import *`. The only `getattr` lookups on these modules are `test_micropython_driver.py:215-217`, which compare driver constants with `sim` and `host`, and that file still imports both.
   - `timing.py:1368`'s `importlib` loads define functions, not these names.
   - `python -m pe.mpy_bundle --check`: "firmware bundle matches validated sources", rc 0.
3. **Test imports: hold.**
   - Every removed import name (15 test modules plus `dbg_trace.py`) has 0 remaining uses in its file. None starts with `test_`.
   - Collected test IDs: `pytest --collect-only test/model --ignore=test/model/test_swd.py` lists the same 2907 IDs at e449e40 and at bbf8b11 (diff empty).
   - `@cocotb.test` counts are unchanged in each of the five changed cocotb modules: bench_fw 6, blind 17, eth_reply_rtl 2, rmii_rtl 6, uart_capture_host 1.
   - No side effect is lost. The modules no longer imported here have no top-level code but definitions: `pe.sigrok`, `pe.usbfs`, `pe.postfreeze2`, `pe.sim`, `pe.host`. `pe.asm` only fills its own tables. `dbg_trace.py` still imports `cocotb.triggers`.
4. **Dead locals: hold.**
   - In each enclosing function, nested closures included, the removed name has 0 remaining uses: `out` in `test_line.run`, `t_out` in the NEC test, `t0` in `bench_pmbus`, `level`, `bits` and `t_edge` in `test_driver_uart_tx_bundle`, and `active_row` in `test_capture_rearm`.
   - No function uses `global` or `nonlocal`.
   - The other `t0` and `bits` names in those files belong to other functions.
5. **`hw_day.py os` and `spi_uart_demo.py math`: hold.** 0 uses remain.
6. **Guards: hold.**
   - `uartcapture_peer.py:9` still re-exports `decode_capture`, and `test_usb_uart_coexist.py:498` asserts `is`.
   - All 234 `mutants.py` sites have count exactly 1 at bbf8b11. I checked by loading `MUTANTS`.
   - Citations: note 6.
7. **Firmware: holds.**
   - `git diff e449e40..bbf8b11` touches nothing in `firmware/`, `tools/pe/asm.py`, `tools/pe/isa.py`, `tools/micropython/protoemu_fw.py`, `src/`, `flow/`, `docs/`, `evidence/`, `README.md` or `ocaml/` (empty stat).
   - My own hasher assembles every `.program` of each file with `asm.assemble_all`, giving each unknown define a placeholder value, and hashes the words, origin, wrap, side-set, config and relocations.
   - Placeholder 0 gives 55 files and 58 programs, total `eb629489df62ef2c…`, identical at both commits.
   - Placeholder 8 gives 49 files and 49 programs, total `a1120e286937396c…`, identical at both commits.
   - Together they cover 60 of the 68 files. The other 8 need particular define values, and their sources and the assembler are unchanged.
   - `protoemu_fw.py` sha256 starts `708bb344978744b6` at both commits, the builder's prefix.
8. **Regression: the model part holds.** 2878 passed and 29 skipped at bbf8b11, the builder's figure. I did not rerun the cocotb modules. None of the five changed cocotb files changes what runs, by the checks in claims 3 and 4.
9. **Mutants: hold for every mutant this item could affect.** I reran each mutant whose mutated file or killing test changed: 61 of 61 killed, no stale, survived or infrastructure result. I did not rerun the other 173. Their files and tests are unchanged, and every site still matches once (claim 6).
10. **Left alone: consistent.** `flow/` and `ocaml/` are untouched by the diff, and so are `librelane_plugin_protoemu.py`, `formal/` and the board-demo preloads. I didn't judge the reasons further.

## What I ran

- **Model suite.** `remote_test.sh model` at bbf8b11 (runner1; `--ignore=test/model/test_swd.py` is built in): rc=0, "2878 passed, 29 skipped in 1749.38s (0:29:09)", with "SIGROK CHECKS SKIPPED: 28 tests".
- **Collection.** In my runner1 clone and a `git archive e449e40`: `pytest --collect-only -q test/model --ignore=test/model/test_swd.py` gives 2907 IDs at both, with an empty diff.
- **Firmware.** `fwhash.py` with `FWDEF=0` and `FWDEF=8` at both commits gives the totals above; `diff` reports IDENTICAL for each.
- **`pe.mpy_bundle --check`** at bbf8b11: rc 0.
- **Mutants.** `test/mutants.py` with the 61 names whose mutated file or killing test changed: 7 `eth-*` (`ethhost.py`), 53 `hw-*` (`hw_day.py`) and `driver-call-target-unchecked` (`test_host_validation.py`). Then `test/blind_mutants.py` (all 34). Both from the clone, with the test venv on PATH:
  - `mutants.py`: "61 of 61 mutants killed", rc 0, every other line `killed: <test>`;
  - `blind_mutants.py`: baselines "test_hd44780.py: 11 passed" and "test_ata_pio.py: 19 passed", then "34/34 killed", rc 0.
- **Static checks.** AST name-use counts for each removed name, per file and per function; scans of joined string constants across every tracked `.py`; grep for re-exports, dynamic lookups and line citations.
- **Privacy sweeps** over all 699 tracked files, the binaries, the commit messages and the commit identities. They printed counts and file names; I read the hits and copy none here.
