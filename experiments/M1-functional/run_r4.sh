#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# R4a/R4b: the `movea.b disp32[PC], Rn` test suite (docs/r4-movea-pcrel-plan.md section 6).
#   R4_PHASE=r4a  today's V2 (the R3b microcode, mtab[0x0F2] = trap_mode): every PC-relative MOVEA traps `mode` at its own start PC
#   R4_PHASE=r4b  after the R4b microcode: the LIVE builder's images (never the fixture) must give the planned behaviour
#
#   1. m1_r4.py generates the programs and their INDEPENDENT expectations (also m1_r3.py's programs, for the live-builder R3 stage)
#   2. V60X (--ext) and the pinned upstream run every positive program; each must equal the generator's expectation
#   3. V2 (tb_m1_v2 / tb_s0_v2) at the 4 fetch configurations on every program; withheld fetches; the SMC builds
#   4. structural: tag scan, table pins vs the frozen pre-R4 fixture, assertions over all eight builder combinations
#   5. R3 programs on the live builder with the supersession table reported at every use
#   6. infrastructure-level negative controls
#
#   experiments/M1-functional/run_r4.sh [--no-negctl]
#   Environment: R4_OUT (REQUIRED: a NEW directory, never an existing one), R4_PHASE (r4a), R4_JOBS (4),
#                R4_MCGEN (microcode generator; default the live builder m1_m11_microcode.py)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
R="$ROOT/experiments/V2-datapath-floor/rtl"
S0="$ROOT/experiments/S0-boot"
[ -n "${R4_OUT:-}" ] || { echo "R4_OUT is required (a new, unique output directory)"; exit 2; }
OUT="$R4_OUT"
[ ! -e "$OUT" ] || { echo "R4_OUT $OUT already exists: every run needs a NEW output directory"; exit 2; }
PHASE="${R4_PHASE:-r4a}"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
df -h "$ROOT" | tail -1

VUP=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)
build_up() {   # build_up <objdir> <top> <mem.sv> <tb.sv>
    verilator "${VUP[@]}" --top-module "$2" --Mdir "$1" -o sim \
        "$U/rtl/cpu/v60/s32_v60.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" "$3" "$4" > "$1.log" 2>&1 \
        || { echo "BUILD FAILED $1"; tail "$1.log"; exit 1; }
}
build_v2() {   # build_v2 <objdir> <top> <mem.sv> <tb.sv> <observer.sv>
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
MCGEN="${R4_MCGEN:-$HERE/m1_m11_microcode.py}"
python3 "$HERE/m1_r4.py" "$OUT/progs" || exit 1
python3 "$HERE/m1_r3.py" "$OUT/r3progs" || exit 1          # the R3 programs, for the live-builder R3 stage
python3 "$MCGEN" "$OUT/mc" > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smc" --smc-test > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.s0" --reset-native > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smcm" --smc-test --smc-merged > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smc_s0" --smc-test --reset-native > /dev/null || exit 1
python3 "$MCGEN" "$OUT/mc.smcm_s0" --smc-test --smc-merged --reset-native > /dev/null || exit 1

NEG=1
[ "${1:-}" = "--no-negctl" ] && NEG=0
python3 "$HERE/m1_r4_run.py" --out "$OUT" --phase "$PHASE" --jobs "${R4_JOBS:-4}" --negctl "$NEG" --root "$ROOT"
exit $?
