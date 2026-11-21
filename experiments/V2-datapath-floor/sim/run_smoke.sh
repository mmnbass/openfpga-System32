#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build and run the V2 connectivity smoke test (Verilator) for each variant.
#   experiments/V2-datapath-floor/sim/run_smoke.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RTL="$HERE/../rtl"
OUT="$HERE/../../../build/V2-smoke"
mkdir -p "$OUT"
python3 "$HERE/microasm.py" smoke "$OUT" >/dev/null
fail=0
for v in "UNIT_STYLE=0 SHIFT_ITER=0" "UNIT_STYLE=1 SHIFT_ITER=0" "UNIT_STYLE=0 SHIFT_ITER=1"; do
    set -- $v
    tag="${1#*=}_${2#*=}"
    verilator --binary --timing -Wno-fatal -Wno-lint -Wno-style -I"$RTL" \
        -G"$1" -G"$2" --top-module tb_v2_smoke --Mdir "$OUT/obj_$tag" -o sim \
        "$HERE/tb_v2_smoke.sv" $(sed "s|^|$RTL/|" "$RTL/files.f") >/dev/null
    out=$(cd "$OUT" && "$OUT/obj_$tag/sim" | tail -20)
    echo "$out" | grep -E "PASS|FAIL"
    echo "$out" | grep -q "V2 SMOKE PASS" || fail=1
done
exit $fail
