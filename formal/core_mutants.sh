#!/bin/bash
# Planted v0.3 bugs that formal/core.sby's properties (in src/pe_core.sv) must
# catch: each runs the core proof on a scratch copy with one line changed and
# must end DONE (FAIL). Needs sby + yices (OSS CAD Suite). Not in `make all`.
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
  (cd "$d/formal" && sby -f core.sby > sby.log 2>&1 < /dev/null)
  r=$(grep -o 'DONE ([A-Z]*' "$d/formal/sby.log" | tail -1)
  echo "$name: $r"
  [ "$r" = "DONE (FAIL" ] || bad=1
done <<'LIST'
table-word-inverted|        osr <= instr;|        osr <= ~instr;
table-clock-executes|  assign      exec    = attempt && bank_ok && !tbl_pend;|  assign      exec    = attempt && bank_ok;
table-pc-jumps|      pc <= tbl_issue ? pc_seq : fetch_addr;|      pc <= fetch_addr;
table-word-dropped|      if (tbl_pend) begin\n        osr <= instr;|      if (1'b0) begin\n        osr <= instr;
ticks-plus-2|          tcnt <= tcnt + 16'd1;|          tcnt <= tcnt + 16'd2;
ticks-every-clock|        cnt <= cnt_next;\n        if (tick) begin|        cnt <= cnt_next;\n        if (1'b1) begin
LIST
echo "scratch: $out"
exit $bad
