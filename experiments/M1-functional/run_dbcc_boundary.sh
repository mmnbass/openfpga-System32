#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Regression for the upstream bench's instruction-boundary count (tb_m1_up):
# taken DBcc loops that upstream runs through its S_DECODE -> S_DECODE
# loop-window restore must count every retired instruction (expected counts
# from m1_ref_ext). Negative control: the previous rule (entry into S_DECODE
# only, as upstream's +PCHIST) on a copy of the bench must UNDERCOUNT.
#   experiments/M1-functional/run_dbcc_boundary.sh [outdir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
OUT="${1:-$ROOT/build/M1/dbcc_boundary}"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
VFLAGS=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)
build() {   # build <objdir> <tb file>
    verilator "${VFLAGS[@]}" --top-module tb_m1_up --Mdir "$1" -o sim \
        "$U/rtl/cpu/v60/s32_v60.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" \
        "$HERE/tb/m1_mem.sv" "$2" > "$1.log" 2>&1 || { echo "BUILD FAILED $2"; tail "$1.log"; exit 1; }
}
build "$OUT/obj_up" "$HERE/tb/tb_m1_up.sv"
mkdir -p "$OUT/old"
python3 - "$HERE/tb/tb_m1_up.sv" "$OUT/old/tb_m1_up.sv" <<'PYEOF'
import sys
s = open(sys.argv[1]).read()
new = "(st_prev != S_DECODE_V || cpu.pf_epoch != epoch_prev)"
if s.count(new) != 1:
    open(sys.argv[2], "w").write("ANCHOR NOT FOUND\n"); sys.exit("old-rule anchor not found")
open(sys.argv[2], "w").write(s.replace(new, "st_prev != S_DECODE_V"))
PYEOF
build "$OUT/obj_old" "$OUT/old/tb_m1_up.sv"

fails=0; under=0
while read -r name want; do
    got=$("$OUT/obj_up/sim" +img="$OUT/p/$name.hex" | sed -n 's/.*instrs=\([0-9]*\).*/\1/p')
    old=$("$OUT/obj_old/sim" +img="$OUT/p/$name.hex" | sed -n 's/.*instrs=\([0-9]*\).*/\1/p')
    if [ "$got" = "$want" ]; then echo "PASS $name: $got retired (reference $want); previous rule counted $old"
    else echo "FAIL $name: bench counted $got, reference $want"; fails=$((fails + 1)); fi
    [ -n "$old" ] && [ "$old" -lt "$want" ] && under=$((under + 1))
done < <(python3 "$HERE/dbcc_boundary_progs.py" "$OUT/p")
if [ "$under" -gt 0 ]; then echo "PASS negative control: the previous rule undercounts $under program(s)"
else echo "FAIL negative control: the previous rule never undercounts (the loop-restore path was not exercised)"; fails=$((fails + 1)); fi
echo "DBCC BOUNDARY: $fails failure(s)"
exit $((fails != 0))
