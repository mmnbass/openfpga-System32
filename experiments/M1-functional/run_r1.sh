#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# R1a: the `jmp /abs` test infrastructure (docs/r1-jmp-abs-plan.md sections 3, 10, 11).
# Test infrastructure only: no JMP microcode exists; V2 traps JMP today (phase r1a).
#
#   1. m1_r1.py generates the programs and their INDEPENDENT expected outcomes
#      (lock-step records, final state, counters, V2 trap/halt/wait per phase)
#   2. refs: m1_ref.py, m1_ref.py --ext (through the R1a start-PC / 24-bit
#      mirrored-memory adapter for the S0 cases) and the pinned upstream s32_v60
#      (M1 bench for low addresses, S0 bench for J10/J13) run every positive
#      program; each must equal the generator's expectation (so also each other),
#      lock-step records and event counters included
#   3. V2 (tb_m1_v2) at the 4 fetch configurations used since M1.1 ("",
#      "+memwait=2", "+memwait=5 +memjit=4", "+wide") on every program; the
#      J12 programs also with fetches withheld (+ackmax); the S0 programs on
#      tb_s0_v2. The expectation is the PHASE: r1a (today: JMP traps `opcode` at
#      the JMP after the preceding instructions retire; G1 `mode`; immediate
#      destinations `immdest`) or r1b (JMP halts and matches) - R1_PHASE=r1b
#   4. negative controls (reference mutants, table mutant, counter mutants,
#      privileged/data-write microcode mutants, a wrong expected trap PC,
#      corrupted logs): every one must be REJECTED
#
#   experiments/M1-functional/run_r1.sh [--no-negctl]
#   Environment: R1_OUT (default build/M1/r1a/run), R1_PHASE (r1a), R1_JOBS (4)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
R="$ROOT/experiments/V2-datapath-floor/rtl"
S0="$ROOT/experiments/S0-boot"
OUT="${R1_OUT:-$ROOT/build/M1/r1a/run}"
PHASE="${R1_PHASE:-r1a}"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
df -h "$ROOT" | tail -1

VUP=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)
fresh() {   # fresh <sim> <sources...>: the binary exists and is newer than every source
    [ -x "$1" ] && [ "${R1_REBUILD:-0}" != 1 ] || return 1
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
python3 "$HERE/m1_r1.py" "$OUT/progs" || exit 1
# microcode: production, the TEST-ONLY --smc-test variant (the nv_store non-vacuity
# program), and the S0 (--reset-native) variant. The builder is run, never edited.
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc" > /dev/null || exit 1
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc.smc" --smc-test > /dev/null || exit 1
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc.s0" --reset-native > /dev/null || exit 1
# --smc-test option combinations: separate / merged store, normal / reset-native start
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc.smcm" --smc-test --smc-merged > /dev/null || exit 1
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc.smc_s0" --smc-test --reset-native > /dev/null || exit 1
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc.smcm_s0" --smc-test --smc-merged --reset-native > /dev/null || exit 1

NEG=1
[ "${1:-}" = "--no-negctl" ] && NEG=0
python3 "$HERE/m1_r1_run.py" --out "$OUT" --phase "$PHASE" --jobs "${R1_JOBS:-4}" --negctl "$NEG" \
    --root "$ROOT"
rc=$?
# R1b: the microcode / table mutants (plan section 5, 10 #3, 11 #1/#2)
if [ "$PHASE" = r1b ] && [ "$NEG" = 1 ]; then
    python3 "$HERE/m1_r1b_mutants.py" --out "$OUT" --jobs "${R1_JOBS:-4}" || rc=1
fi
exit $rc
