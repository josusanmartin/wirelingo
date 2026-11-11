VERDICT: PASS

Item 060 `privacy-needles`: commit 6b78b89 on e449e40, checked out detached. I read the diff (2 files, +73 −24: `test/model/test_private_hosts.py` and `test/mutants.py`) and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

The old tailnet label is gone from the tree in every form I could think of:
- 41 encodings;
- the contents of compressed files (the tree has none);
- each file with separators removed;
- Python string literals joined.

The new class scan catches the real label in every plain context I planted. Its only allowlist entry is minimal and needed. All its weakening mutants die, the merge onto f8eed52 stays clean, and the four `privacy-*` mutants are killed. One note: the scan can't see the split-literal form the old test used (note 1).

## Findings

None blocking.

### Notes

1. **The scan is line-based on raw text, so it can't see a split Python literal: the very form 058 finding 1 was about.**
   - I planted the real old label as `NEEDLE = b"<4>" b"<6>"` in a throwaway repo, and `hits()` did not report it.
   - It is also blind, by design, to these forms, which I planted one by one:
     - the label split across two lines;
     - UTF-16;
     - gzip;
     - base64;
     - percent-encoding;
     - a zero-width space inside the label;
     - a zlib-compressed PNG `zTXt` chunk.
   - Only the first matters in practice. Copying the old test or mutant back from history would bring the label back in exactly that form, unflagged.
   - Cheap fix: for each tracked `.py` file, also scan its string constants joined, through `ast.walk` over `ast.Constant` (adjacent literals arrive as one constant). That is about 10 lines. The new test's own patterns are written as adjacent literals too (`".ts" + ".net"` and the like), so they would then match themselves. The joined pass would need one exception for `test_private_hosts.py` and `mutants.py`, or patterns built at run time from pieces a joiner can't fuse (e.g. `"." + "ts"`).
2. **The made-up MagicDNS example `wren-otter` looks like a real tailnet name.** Tailscale's randomly assigned names have the same two-word shape. It is planted only in scratch copies and split in the source, and a tailnet name grants no access, so this is cosmetic. A name that can't be a real one (e.g. `example-example`) would avoid any look of a leak.
3. **Two mutants of the scan survive, and both only widen it:**
   - dropping the trailing `(?![0-9])`, so `100.64.1.1234` would also match;
   - `tail` + 3 hex digits instead of 4.

   Every weakening mutant is killed (below). No action needed.
4. **The IPv6 branch flags Tailscale's public prefix written as a class.** The IPv4 base is allowlisted, but the IPv6 prefix is not: any line giving the IPv6 range in the usual `…::/48` form, or the prefix with a trailing colon, is a hit.
   - Today's tree has none, so it passes.
   - My first draft of this report did contain one, quoting the regex branch. I checked the report with the new pattern before finishing and reworded it.
   - Verifier reports are copied into `evidence/verifier/`, which the scan reads. So whoever writes about the IPv6 class must describe it rather than quote it. The alternative is a second `CLASS_ONLY` entry for the bare prefix.
   - This is fail-safe: a false alarm on a class mention, never a miss.

## The builder's claims

1. **The class scan: holds as described.** On the lower-cased line, `TAILNET` matches:
   - `tail[0-9a-f]{4}`;
   - `[a-z0-9_-]\.ts\.net`;
   - 100.64.0.0/10 with `(?<![0-9.])` before and `(?![0-9])` after;
   - Tailscale's IPv6 /48 prefix followed by a colon (not written out here; see note 4).

   `CLASS_ONLY = {b"100.64.0.0"}`.
   - `*.ts.net` doesn't match: `*` isn't in the class.
   - `TAIL…TS.NET` doesn't match: `…` is a non-ASCII byte between `tail` and the hex.
   - At 6b78b89, `hits(ROOT)` is `[]`.
2. **Hash or environment variable: agreed.**
   - A 6-hex label has 2^24 values, so any stored hash, salted or not, is brute-forced in seconds.
   - An environment variable leaves public CI unguarded.
   - My 058 suggestion of a stored hash was wrong for this reason.
3. **The planted-name test: holds.** It plants nine files with labels freshly made by `secrets.token_hex(3)` and asserts the exact `file:line` list. It also plants a clean near-miss file and asserts no hit. The scan mutants below show each pattern branch is needed by it.
4. **The privacy mutants: hold.** The three new or changed ones run `-k no_tailnet`, and each is killed by `test_no_tailnet_name_or_address_in_tracked_files`. `privacy-kria-other-default` is killed by `test_install_refuses_with_no_host`.
5. **The label is not recoverable from the tree: holds.**
   - Method: my scan reads the label from `e449e40:test/model/test_private_hosts.py` at run time (`ast`, the `NEEDLE` assignment), so neither the script nor this report holds it. It searches all 700 tracked files.
   - Each file is checked as raw bytes, lower-cased, with separators removed (`\s " ' ` + , . _ : ; / \ | -`), and, for `.py`, with all string constants joined through the AST. Compressed members (gzip, bz2, xz, zlib, zip, tar, PNG zTXt) are opened.
   - The 41 forms:
     - plain, upper case, the hex part alone, reversed, the hex part reversed, byte-swapped hex;
     - hex of the label, hex of the hex part;
     - the hex part as a decimal integer, and as octal;
     - rot13;
     - percent-encoding;
     - HTML decimal and hex entities;
     - decimal byte lists, with and without spaces;
     - `\ooo`, `\x` and `\u` escapes;
     - UTF-16 LE and BE;
     - base16, base32, base85, ascii85;
     - base64 and url-safe base64 at all three alignments;
     - md5, sha1, sha256 and sha512 of both the label and its hex part.
   - Positive control at e449e40: `plain` and `hexpart` are found in `test_private_hosts.py` and `test/mutants.py`, through the joined and separator-free views, as 058 found.
   - At 6b78b89: none of the 41 forms is found anywhere. The tree has no compressed member to open (0).
   - `git grep` for the hex part has 0 hits.
6. **Tests at 6b78b89: hold.** The model suite gives 2878 passed and 29 skipped, rc 0, the builder's figure. The count is unchanged because a test was renamed, not added. The four `privacy-*` mutants: 4 of 4 killed (below). I did not rerun the 124-mutant `hw-*`/`fab-*`/`privacy-*` set; `hw_day.py`, `fabric_eth.py`, `eth_tap.py` and their tests are untouched by this diff.

## Asked checks

- **Would the old label be caught if reintroduced? Yes, in every plain context.** In a throwaway runner1 repo I planted the real old label (read at run time, as above) and ran the new `hits()`. It caught all 17 cases planted to be caught:
  - `ssh ubuntu@kria.<label>.ts.net` in a doc;
  - a log line with `:22`;
  - the admin URL `…/machines?tailnet=<label>`;
  - the whole name in upper case;
  - the bare label in prose;
  - inside JSON;
  - inside a code span;
  - glued between other letters;
  - in a CRLF and tab line;
  - in a PNG `tEXt` chunk;
  - a made-up MagicDNS name;
  - IPv4 at both ends of the range, with `/32` and with `:22`;
  - IPv6, and IPv6 upper case in brackets with a port.

  It missed the 8 encoded or split forms of note 1, as expected. Mismatches against my expectations: 0.
- **False positives and the allowlist.**
  - With the allowlist: 0 hits on today's tree.
  - With `CLASS_ONLY` emptied: 12 lines in 7 files, all 13 matches exactly `100.64.0.0`. The files are `evidence/README.md`, `evidence/kv260-usb-20261030/README.md`, verifier reports 035, 041 and 042, `evidence/verifier/README.md`, and the test's own docstring.
  - So the one allowlist entry is needed and is all that is needed.
- **The merge onto `f8eed52`: clean.**
  - `git merge-tree --write-tree f8eed52 6b78b89` gives rc 0 with no conflict.
  - In the merged tree (699 files), `test/gl.log` is absent, and the new `hits()` gives `[]`.
  - My 41-form scan finds none.
- **Scan mutants.** I made 14 mutants of the scan in throwaway copies and ran the test file's own tests on each. 12 are killed: no-lower, each of the four pattern branches disabled, no-lookbehind, a narrowed IPv4 range, a widened IPv4 range, allow-everything, allow any `100.` prefix, `*` allowed before `.ts.net`, and reading untracked files. The 2 survivors only widen the scan (note 3).
- **`/home/runner1` and `/home/<other>`: neither matters for a public repo.**
  - `/home/runner1` (3 lines of `evidence/fabric-ping-20261103/c108e09-suite/model.log`) shows only that the runner's user is named like its alias, which the author has chosen to keep. It names no person, host or address.
  - `/home/<other>/p/tt-new-template-proto/…` in `test/tb.gtkw` is the upstream Tiny Tapeout template's own path. The file arrived in the first commit (c8f311e) and was never edited. It is someone else's public file, harmless.
  - Removing either is tidiness, not privacy.
- **Rules.**
  - The commit message's only private-pattern hit is the class mention `` `<label>.ts.net` ``.
  - It ends with the Co-Authored-By line.
  - Its author identity is the history's (item AI).
  - Nothing changes in `src/` or `flow/`.

## What I ran

- **Model suite.** `remote_test.sh model` at 6b78b89 (runner1; `--ignore=test/model/test_swd.py` is built in): rc=0, "2878 passed, 29 skipped in 1683.57s (0:28:03)" on a loaded runner1, with "SIGROK CHECKS SKIPPED: 28 tests".
- **Encoding scan.** `needles.py` locally at e449e40 (positive control: 2 forms in 2 files) and at 6b78b89 ("hits by form: none"), and on runner1 over the merged tree ("files 699 … hits by form: none").
- **Plant test.** `plant.py` on runner1: "caught 17 of 25 | mismatches vs expectation: 0".
- **Allowlist check.** `hits()` with and without `CLASS_ONLY` on 6b78b89: 0 and 12 lines.
- **Merge.** `git merge-tree --write-tree f8eed52 6b78b89`, then `hits()` and `needles.py` on the archived tree.
- **Mutants.** `python test/mutants.py "privacy-*"` in the clone: "4 of 4 mutants killed". `scanmut.py` (14 scan mutants): 12 killed, 2 survive, both widenings.
