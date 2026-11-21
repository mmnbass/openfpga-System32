#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# R3a: the `movea.b /abs, Rn` test infrastructure (docs/r3-movea-plan.md sections 4, 6, 8).
# Test infrastructure only: no MOVEA microcode and no dispatch entry exist; V2 traps MOVEA today
# (phase r3a: `opcode` at the MOVEA, the S0 reach stays 2). R3_PHASE=r3b flips every V2 expectation to the planned
# behaviour (success / mode / opcode traps) for the R3b step; R3_SIM_R3B=1 runs that phase against an in-memory
# table-only stub (m1_r3b_simbuilder.py, inert self-loop routine) to show the suite is runnable and rejects the stub.
#
#   1. m1_r3.py generates the programs and their INDEPENDENT expectations (lock-step records, final state,
#      counters, per-instruction GPR and privileged write events, V2 trap/halt/wait per phase)
#   2. refs: m1_ref.py --ext (the CORRECTED V60X; through the R1a start-PC / 24-bit adapter for the S0 programs) and the
#      pinned upstream s32_v60 run every positive program; each must equal the generator's expectation. The base
#      m1_ref (v60_ref) is not corrected: its MOVEA is the retained discrepancy D1 (probe_movea_value, enforced)
#   3. V2 (tb_m1_v2 / tb_s0_v2) at the 4 fetch configurations ("", "+memwait=2", "+memwait=5 +memjit=4", "+wide") on
#      every program; A7 with withheld fetches (+ackmax, released by +ackdelay or never); the SMC builds; the TEST-ONLY
#      probe microcode image (the plan's execute step on spare opcode 0xFE, trap-in-the-middle probes 0xFC / 0xFB)
#   4. structural: dispatch isolation and the mode-table invariants over all eight builder option combinations
#   5. the mutant-catcher contract (m1_r3a_catchers.py) and the infrastructure-level negative controls (m1_r3a_negctl.py)
#
#   experiments/M1-functional/run_r3.sh [--no-negctl]
#   Environment: R3_OUT (default build/M1/r3a/run), R3_PHASE (r3a), R3_JOBS (4), R3_REBUILD=1, R3_SIM_R3B=1
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
R="$ROOT/experiments/V2-datapath-floor/rtl"
S0="$ROOT/experiments/S0-boot"
OUT="${R3_OUT:-$ROOT/build/M1/r3a/run}"
PHASE="${R3_PHASE:-r3a}"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
df -h "$ROOT" | tail -1

VUP=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)
fresh() {   # fresh <sim> <sources...>: the binary exists and is newer than every source
    [ -x "$1" ] && [ "${R3_REBUILD:-0}" != 1 ] || return 1
    local s; shift
    for s in "$@"; do [ "$s" -nt "$OBJ_SIM" ] && return 1; done
    [ -z "$(find "$R" "$U/rtl/cpu/v60" -newer "$OBJ_SIM" -name '*.sv*' 2>/dev/null | head -1)" ]
}
build_up() {   # build_up <objdir> <top> <mem.sv> <tb.sv>
    OBJ_SIM="$1/sim"; fresh "$1/sim" "$3" "$4" && return 0
    verilator "${VUP[@]}" --top-module "$2" --Mdir "$1" -o sim \
        "$U/rtl/cpu/v60/s32_v60.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" "$3" "$4" > "$1.log" 2>&1 \
        || { echo "BUILD FAILED $1"; tail "$1.log"; exit 1; }
}
build_v2() {   # build_v2 <objdir> <top> <mem.sv> <tb.sv> <observer.sv>
    OBJ_SIM="$1/sim"; fresh "$1/sim" "$3" "$4" "$5" "$HERE/tb/v2_sim_checks.sv" && return 0
    verilator --binary --timing --threads 1 -Wno-fatal -Wno-lint -Wno-style -Wno-TIMESCALEMOD \
        +define+SIMULATION -I"$R" --top-module "$2" --Mdir "$1" -o sim \
        $(sed "s|^|$R/|" "$R/files.f") "$U/rtl/cpu/v60/s32_v60_bus.sv" \
        "$3" "$5" "$HERE/tb/v2_sim_checks.sv" "$4" > "$1.log" 2>&1 \
        || { echo "BUILD FAILED $1"; tail "$1.log"; exit 1; }
}
echo "== building benches (m1 up/v2, s0 up/v2)"
build_up "$OUT/obj_up" tb_m1_up "$HERE/tb/m1_mem.sv" "$HERE/tb/tb_m1_up.sv"
build_v2 "$OUT/obj_v2" tb_m1_v2 "$HERE/tb/m1_mem.sv" "$HERE/tb/tb_m1_v2.sv" "$HERE/tb/v2_sim_observer.sv"
build_up "$OUT/obj_s0up" tb_s0_up "$S0/tb/s0_mem.sv" "$S0/tb/tb_s0_up.sv"
build_v2 "$OUT/obj_s0v2" tb_s0_v2 "$S0/tb/s0_mem.sv" "$S0/tb/tb_s0_v2.sv" "$HERE/tb/v2_sim_observer.sv"

echo "== generating programs and independent expectations"
MCGEN="$HERE/m1_m11_microcode.py"
# R3_SIM_R3B=1 (with R3_PHASE=r3b): TEST-ONLY simulated R3b table state, in memory (m1_r3b_simbuilder.py)
[ "${R3_SIM_R3B:-0}" = 1 ] && MCGEN="$HERE/m1_r3b_simbuilder.py"
# R3_MCGEN (R4 tooling, default unchanged): another generator, e.g. m1_r4_fixbuilder.py = the frozen pre-R4 builder state; R3_BUILDER_MODULE installs it in the driver too
[ -n "${R3_MCGEN:-}" ] && MCGEN="$R3_MCGEN"
python3 "$HERE/m1_r3.py" "$OUT/progs" || exit 1
# microcode: production, the TEST-ONLY --smc-test variants (separate / merged store, M1 and S0 start) and the
# S0 (--reset-native) variant. The builder is run, never edited. The probe image is built in memory by the driver.
python3 "$MCGEN" "$OUT/mc" > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smc" --smc-test > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.s0" --reset-native > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smcm" --smc-test --smc-merged > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smc_s0" --smc-test --reset-native > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smcm_s0" --smc-test --smc-merged --reset-native > /dev/null || exit 1

NEG=1
[ "${1:-}" = "--no-negctl" ] && NEG=0
python3 "$HERE/m1_r3_run.py" --out "$OUT" --phase "$PHASE" --jobs "${R3_JOBS:-4}" --negctl "$NEG" --root "$ROOT" $([ "${R3_SIM_R3B:-0}" = 1 ] && echo --sim-r3b) $([ -n "${R3_BUILDER_MODULE:-}" ] && echo --builder-module "$R3_BUILDER_MODULE")
exit $?
