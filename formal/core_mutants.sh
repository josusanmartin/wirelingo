#!/bin/bash
# Planted v0.3 bugs (and the retimed CRC's) that formal/core.sby's properties (in src/pe_core.sv) must
# catch: each runs the core proof on a scratch copy with one line changed and
# must end DONE (FAIL). (Counting every active clock instead of every tick
# ends UNKNOWN at depth 6, not FAIL, so it is not in the list.) Needs sby +
# yices (OSS CAD Suite). Not in `make all`.
#   formal/core_mutants.sh            # from the repository root or formal/
set -u
here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
out=$(mktemp -d)
bad=0
while IFS='|' read -r name old new; do
  [ -z "$name" ] && continue
  d="$out/$name"; mkdir -p "$d/src" "$d/formal"
  cp "$root"/src/*.sv "$d/src/"
  cp "$here/core.sby" "$here/core_props.sv" "$d/formal/"
  python3 - "$d/src/pe_core.sv" "$old" "$new" <<'EOF' || { echo "$name: STALE (site not found)"; bad=1; continue; }
import sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
old, new = (a.replace('\\n', '\n') for a in sys.argv[2:4])
if s.count(old) != 1:
    sys.exit(1)
p.write_text(s.replace(old, new))
EOF
  (cd "$d/formal" && sby -f core.sby prove > sby.log 2>&1 < /dev/null)
  r=$(grep -o 'DONE ([A-Z]*' "$d/formal/sby.log" | tail -1)
  echo "$name: $r"
  [ "$r" = "DONE (FAIL" ] || bad=1
done <<'LIST'
table-word-inverted|        osr <= instr;|        osr <= ~instr;
table-clock-executes|  assign      exec    = attempt && bank_ok && !tbl_pend;|  assign      exec    = attempt && bank_ok;
table-pc-jumps|      pc <= tbl_issue ? pc_seq : fetch_addr;|      pc <= fetch_addr;
table-word-dropped|      if (tbl_pend) begin\n        osr <= instr;|      if (1'b0) begin\n        osr <= instr;
ticks-plus-2|          tcnt <= tcnt + 16'd1;|          tcnt <= tcnt + 16'd2;
ticks-kept-on-restart|        stalled <= 1'b0;\n        tcnt <= 16'd0;\n|        stalled <= 1'b0;\n
crc-step-new-settings|(((crc << 1) & crc_mask_q) ^ (crc_fbq ? crc_pm_q : 32'd0))|(((crc << 1) & crc_mask) ^ (crc_fbq ? crc_poly_m : 32'd0))
crc-step-dropped|      crc <= crc_eff;\n|      crc <= crc;\n
crc-pend-survives-set|exec && crc_feed && !crc_set && !(host_w|exec && crc_feed && !(host_w
crc-pend-survives-host-write|exec && crc_feed && !crc_set && !(host_w && bus_off == 5'h07);|exec && crc_feed && !crc_set;
crc-feed-reads-register|  wire        crc_fb = crc_eff[crc_w] ^ crc_bit;|  wire        crc_fb = crc[crc_w] ^ crc_bit;
crc-mov-reads-register|      L_CRC:    src_val = crc_eff;|      L_CRC:    src_val = crc;
crc-host-reads-register|      5'h07: bus_rdata = crc_eff;|      5'h07: bus_rdata = crc;
crc-pend-without-exec|      crc_pend <= exec && crc_feed|      crc_pend <= crc_feed
LIST
echo "scratch: $out"
exit $bad
