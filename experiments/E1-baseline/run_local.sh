#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Run one E1 configuration outside CI, with the same steps and gates as
# .github/workflows/e1-fit.yml.
#
#   experiments/E1-baseline/run_local.sh <vp|sr> <seed>
#
# Uses native Quartus if quartus_map is on PATH (e.g. the MSI, Quartus 17.1
# recommended), otherwise tools/quartus.sh (Docker, raetro/quartus:17.1).
set -euo pipefail
cd "$(dirname "$0")/../.."

CFG="${1:?usage: run_local.sh <vp|sr> <seed>}"
SEED="${2:?usage: run_local.sh <vp|sr> <seed>}"
RUN="$CFG-seed$SEED"
PROJ="build/E1/$RUN"
OUT="reports/E1/$RUN"

python3 tools/check_upstream.py
python3 experiments/E1-baseline/gen_project.py --config "$CFG" --seed "$SEED" --out "$PROJ"
mkdir -p "$OUT"
cp "$PROJ/run.json" "$OUT/"

q() {   # q <quartus_tool> [args...]   (runs inside the project directory)
    if command -v quartus_map >/dev/null 2>&1; then
        (cd "$PROJ" && "$@")
    else
        tools/quartus.sh bash -c "cd '$PROJ' && $*"
    fi
}

q quartus_sh --version | tee "$OUT/toolchain.txt"
q quartus_map ga2_e1 | tee "$PROJ/map.log"
python3 tools/e1_gate.py "$PROJ" --stage map --config "$CFG"
q quartus_fit ga2_e1 | tee "$PROJ/fit.log"
python3 tools/e1_gate.py "$PROJ" --stage fit --config "$CFG"
q quartus_sta ga2_e1 | tee "$PROJ/sta.log"
q quartus_sta -t ../../../tools/timing-report.tcl ga2_e1 | tee "$OUT/timing_attribution.txt" || true

python3 tools/e1_report.py "$PROJ" "$OUT"
for f in map.summary fit.summary sta.summary; do
    cp "$PROJ/output_files/ga2_e1.$f" "$OUT/" 2>/dev/null || true
done
python3 tools/parse-fit.py "$PROJ/output_files/ga2_e1.fit.rpt" --under "s32_core:core" --depth 4 \
    > "$OUT/entities_depth4.txt"
echo "reports in $OUT; combine with: python3 tools/e1_compare.py reports/E1"
