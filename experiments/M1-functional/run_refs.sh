#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# M1.0: check that the two references agree — upstream s32_v60 RTL (pinned,
# read in place) against ref/v60_ref.py — on 100 random seeds of upstream's
# own generator and on the deterministic programs of m1_directed.py.
# Compared: R0-R31, PSW, ordered data writes, halt, retired-instruction count.
#   experiments/M1-functional/run_refs.sh [nseeds] [outdir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
N="${1:-100}"
OUT="${2:-$ROOT/build/M1/refs}"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
VFLAGS=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)     # S32_V60_NO_FP as in the GA2 build
verilator "${VFLAGS[@]}" --top-module tb_m1_up --Mdir "$OUT/obj_up" -o sim \
    "$U/rtl/cpu/v60/s32_v60.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" \
    "$HERE/tb/m1_mem.sv" "$HERE/tb/tb_m1_up.sv" > "$OUT/build_up.log" 2>&1 || { echo "BUILD FAILED"; tail "$OUT/build_up.log"; exit 1; }
python3 "$HERE/ref/v60_ref.py" > /dev/null || { echo "v60_ref self-test FAILED"; exit 1; }

pass=0; fail=0; failed=()
one() {   # one <name> <prefix>
    python3 "$HERE/m1_ref.py" "$2.bin" > "$2.ref"
    "$OUT/obj_up/sim" +img="$2.hex" > "$2.up" 2>&1
    if python3 "$HERE/m1_compare.py" "$2.ref" "$2.up" --label-a ref --label-b rtl > "$2.cmp"; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); failed+=("$1")
    fi
}
for s in $(seq 1 "$N"); do
    python3 "$HERE/m1_image.py" random "$s" "$OUT/seed$s"
    one "seed$s" "$OUT/seed$s"
done
python3 "$HERE/m1_directed.py" "$OUT/dir" > /dev/null
for t in "$OUT"/dir/*.bin; do
    n=$(basename "$t" .bin); one "dir:$n" "${t%.bin}"
done
echo "M1 REFS: $pass agree, $fail disagree (of $((pass + fail)): $N random + $(ls "$OUT"/dir/*.bin | wc -l | tr -d ' ') directed)"
# documented reference discrepancies: each probe must still DIFFER (R3a: enforced, see the exit status below)
probes_ok=1
for t in "$OUT"/dir/probes/*.bin; do
    n=$(basename "$t" .bin); p="${t%.bin}"
    python3 "$HERE/m1_ref.py" "$p.bin" > "$p.ref"; "$OUT/obj_up/sim" +img="$p.hex" > "$p.up" 2>&1
    if python3 "$HERE/m1_compare.py" "$p.ref" "$p.up" --label-a ref --label-b rtl > "$p.cmp"; then
        echo "  probe $n: references now AGREE (documented discrepancy no longer reproduces)"; probes_ok=0
    else
        echo "  probe $n: references differ, as documented"
    fi
done
# R3a: probe_movea_value (D1) is ENFORCED, not merely printed: both runs must succeed and show exactly v60_ref R1 = 0x5A
# with a data read and upstream R1 = 0x20 without one, and nothing else may differ (m1_probe_movea.py). PROBE_REF_SCRIPT
# (default m1_ref.py) lets the negative control substitute a deliberately fixed copy of the reference.
movea_ok=1
python3 "$HERE/m1_probe_movea.py" --sim "$OUT/obj_up/sim" --ref-script "${PROBE_REF_SCRIPT:-$HERE/m1_ref.py}" \
    --workdir "$OUT/probe_movea_value" || movea_ok=0
for f in "${failed[@]:-}"; do [ -n "$f" ] && echo "  disagree: $f"; done
[ "$probes_ok" -eq 1 ] || echo "  FAIL: a documented discrepancy no longer reproduces"
[ "$fail" -eq 0 ] && [ "$probes_ok" -eq 1 ] && [ "$movea_ok" -eq 1 ]
