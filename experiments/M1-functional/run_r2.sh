#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# R2a: the `ldpr #imm32, #q` test infrastructure (docs/r2-ldpr-plan.md sections 4, 7, 9).
# Test infrastructure only: no LDPR microcode and no dispatch entry exist; V2 traps LDPR today
# (phase r2a: `opcode` at the LDPR, the S0 reach stays 1). R2_PHASE=r2b flips every V2 expectation to the
# planned behaviour (success / regid / mode / priv traps) for the R2b step.
#
#   1. m1_r2.py generates the programs and their INDEPENDENT expectations (lock-step records, final state,
#      counters, per-instruction privileged write events, V2 trap/halt/wait per phase)
#   2. refs: m1_ref.py --ext (V60X; through the R1a start-PC / 24-bit adapter for the S0 programs) and the
#      pinned upstream s32_v60 run every positive program; each must equal the generator's expectation.
#      The base m1_ref (V60) cannot execute LDPR and is not extended (plan section 9 #1).
#   3. V2 (tb_m1_v2 / tb_s0_v2) at the 4 fetch configurations ("", "+memwait=2", "+memwait=5 +memjit=4",
#      "+wide") on every program; L6 with withheld fetches (+ackmax, released by +ackdelay or never); N5
#      with the TEST-ONLY +forceel; the SMC builds (separate / merged store, M1 and S0 benches); the
#      TEST-ONLY probe microcode image (WR_ASEL = 3, trap kinds priv / regid, EL force)
#   4. dispatch isolation over all eight builder option combinations (structural)
#   5. the mutant-catcher contract (m1_r2a_catchers.py) and the infrastructure-level negative controls
#
#   experiments/M1-functional/run_r2.sh [--no-negctl]
#   Environment: R2_OUT (default build/M1/r2a/run), R2_PHASE (r2a), R2_JOBS (4), R2_REBUILD=1
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
R="$ROOT/experiments/V2-datapath-floor/rtl"
S0="$ROOT/experiments/S0-boot"
OUT="${R2_OUT:-$ROOT/build/M1/r2a/run}"
PHASE="${R2_PHASE:-r2a}"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
df -h "$ROOT" | tail -1

VUP=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)
fresh() {   # fresh <sim> <sources...>: the binary exists and is newer than every source
    [ -x "$1" ] && [ "${R2_REBUILD:-0}" != 1 ] || return 1
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
# R2_SIM_R2B=1 (with R2_PHASE=r2b): TEST-ONLY simulated R2b table state, in memory (m1_r2b_simbuilder.py)
[ "${R2_SIM_R2B:-0}" = 1 ] && MCGEN="$HERE/m1_r2b_simbuilder.py"
python3 "$HERE/m1_r2.py" "$OUT/progs" || exit 1
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
python3 "$HERE/m1_r2_run.py" --out "$OUT" --phase "$PHASE" --jobs "${R2_JOBS:-4}" --negctl "$NEG" --root "$ROOT" $([ "${R2_SIM_R2B:-0}" = 1 ] && echo --sim-r2b)
exit $?
