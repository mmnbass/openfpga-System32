#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# M1.0, V2 side: build tb_m1_v2 (v2_core + upstream s32_v60_bus + m1_mem +
# the simulation-only observer), load the M1.0 microcode, run each program
# and compare V2's canonical dump with BOTH references.
#   experiments/M1-functional/run_v2.sh [outdir]
# Expected outcomes:
#   halt   MATCH both references
#   nops   MATCH both references. Before the D1 fix this failed: the reset
#          redirect withdrew an outstanding prefetch and V2 accepted stale data.
#          It runs with the plain M1.0 microcode, no reset workaround.
# Both are also compared in lock-step (m1_lockstep.py): one record per retired
# instruction (start PC, opcode, registers changed), V2 against each
# reference and the references against each other. A deliberately corrupted
# V2 record set must FAIL the comparison, or the check proves nothing.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
R="$ROOT/experiments/V2-datapath-floor/rtl"
OUT="${1:-$ROOT/build/M1/v2}"
REFS="$ROOT/build/M1/refs"
mkdir -p "$OUT"
[ -x "$REFS/obj_up/sim" ] || { echo "run run_refs.sh first (builds the upstream bench)"; exit 1; }
verilator --binary --timing --threads 1 -Wno-fatal -Wno-lint -Wno-style -Wno-TIMESCALEMOD \
    +define+SIMULATION -I"$R" --top-module tb_m1_v2 --Mdir "$OUT/obj_v2" -o sim \
    $(sed "s|^|$R/|" "$R/files.f") "$U/rtl/cpu/v60/s32_v60_bus.sv" \
    "$HERE/tb/m1_mem.sv" "$HERE/tb/v2_sim_observer.sv" "$HERE/tb/v2_sim_checks.sv" "$HERE/tb/tb_m1_v2.sv" \
    > "$OUT/build_v2.log" 2>&1 || { echo "BUILD FAILED"; tail "$OUT/build_v2.log"; exit 1; }
python3 "$HERE/m1_microcode.py" "$OUT/mc" > /dev/null
HP=$(grep '^halt=' "$OUT/mc/m1_symbols.txt" | cut -d= -f2)
TP=$(grep '^trap=' "$OUT/mc/m1_symbols.txt" | cut -d= -f2)
python3 -c "import sys; sys.path.insert(0, '$HERE'); from m1_image import write; write(bytes([0x00]), '$OUT/halt'); write(bytes([0xCD]*5 + [0x00]), '$OUT/nops')"
status=0
for t in halt nops; do
    MC="$OUT/mc"
    HP=$(grep '^halt=' "$MC/m1_symbols.txt" | cut -d= -f2)
    TP=$(grep '^trap=' "$MC/m1_symbols.txt" | cut -d= -f2)
    "$OUT/obj_v2/sim" +img="$OUT/$t.hex" +mc="$MC" +haltpc="$HP" +trappc="$TP" +retire +lockstep > "$OUT/$t.v2" 2>&1
    python3 "$HERE/m1_ref.py" "$OUT/$t.bin" --lockstep > "$OUT/$t.ref"
    "$REFS/obj_up/sim" +img="$OUT/$t.hex" +lockstep > "$OUT/$t.up" 2>&1
    a=$(python3 "$HERE/m1_compare.py" "$OUT/$t.ref" "$OUT/$t.v2" --label-a ref --label-b v2 | tail -1)
    b=$(python3 "$HERE/m1_compare.py" "$OUT/$t.up" "$OUT/$t.v2" --label-a rtl --label-b v2 | tail -1)
    echo "$t: V2 vs ref: $a; V2 vs upstream RTL: $b"
    la=$(python3 "$HERE/m1_lockstep.py" "$OUT/$t.ref" "$OUT/$t.v2" --label-a ref --label-b v2 | head -1)
    lb=$(python3 "$HERE/m1_lockstep.py" "$OUT/$t.up" "$OUT/$t.v2" --label-a rtl --label-b v2 | head -1)
    lc=$(python3 "$HERE/m1_lockstep.py" "$OUT/$t.ref" "$OUT/$t.up" --label-a ref --label-b rtl | head -1)
    echo "$t: lock-step V2 vs ref: $la; V2 vs upstream RTL: $lb; ref vs RTL: $lc"
    case "$la$lb$lc" in *DIFF*) status=1 ;; esac
    grep -E "^(RETIRE|END|COUNTERS|TRAP)" "$OUT/$t.v2" | sed 's/^/    /'
    case "$t" in
        halt|nops) [ "$a" = "RESULT MATCH" ] && [ "$b" = "RESULT MATCH" ] || status=1 ;;
    esac
done
# the lock-step check must reject a corrupted record set
python3 -c "import re,sys; t=open(sys.argv[1]).read(); open(sys.argv[2],'w').write(re.sub(r'^L 1 pc=\w{8}', 'L 1 pc=0000beef', t, count=1, flags=re.M))" "$OUT/nops.v2" "$OUT/nops_corrupt.v2"
if python3 "$HERE/m1_lockstep.py" "$OUT/nops.ref" "$OUT/nops_corrupt.v2" > /dev/null; then
    echo "lock-step self-check: a corrupted record set was NOT rejected"; status=1
else
    echo "lock-step self-check: a corrupted record set is rejected"
fi
exit $status
