#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# D1 regression (tb_d1_fetch), both fetch variants, over data-bus prefetch
# with memory wait states and jitter, the wide port, contending data traffic,
# and the self-modifying-code suite (+smc):
#   * the CURRENT fetch RTL must pass every configuration;
#   * the pre-fix RTL (fc5516c, defect D1), the 4-bit-epoch D1 fix (dbe89f4,
#     review finding C1-1) and every plausible-incorrect mutant from
#     d1_mutants.py (D1b alone reverted among them) must each fail at least
#     one configuration, or the bench proves nothing about them;
#   * the round-1 RTL (05bb4ea, before the SMC fix) must fail the +smc groups
#     of the defects it has: below, half and wrap (v2_fetch; C1-3, C1-2,
#     C1-2w) and below (v2_fetch_byteq; C1-3).
# Per-run outputs are kept in build/M1/d1/runs/.
#   experiments/M1-functional/run_d1.sh [outdir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
R="$ROOT/experiments/V2-datapath-floor/rtl"
OUT="${1:-$ROOT/build/M1/d1}"      # output directory argument (R4 tooling; default unchanged; use a NEW directory for every run)
mkdir -p "$OUT/runs"
for rev in fc5516c dbe89f4 05bb4ea; do
    mkdir -p "$OUT/rtl_$rev"
    for f in v2_fetch.sv v2_fetch_byteq.sv v2_bus.sv; do
        git -C "$ROOT" show "${rev}:experiments/V2-datapath-floor/rtl/$f" > "$OUT/rtl_$rev/$f"
    done
done
python3 "$HERE/d1_mutants.py" "$R" "$OUT/mutants" || exit 1

build() {   # build <name> <rtl-dir> <std|byteq> [extra defines]
    local def="${4:-}"; [ "$3" = byteq ] && def="$def +define+BYTEQ"
    verilator --binary --timing --threads 1 -Wno-fatal -Wno-lint -Wno-style -Wno-TIMESCALEMOD \
        +define+SIMULATION $def --top-module tb_d1_fetch --Mdir "$OUT/obj_$1" -o sim \
        "$2/v2_fetch.sv" "$2/v2_fetch_byteq.sv" "$2/v2_bus.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" \
        "$HERE/tb/m1_mem.sv" "$HERE/tb/tb_d1_fetch.sv" > "$OUT/build_$1.log" 2>&1 \
        || { echo "BUILD $1 FAILED"; tail "$OUT/build_$1.log"; exit 1; }
}
CONFIGS=("+memwait=0" "+memwait=2" "+memwait=5 +memjit=4" "+wide" "+wide +memwait=3"
         "+dtraffic" "+dtraffic +memwait=3 +memjit=2" "+wide +dtraffic" "+smc" "+smc +wide")
run() {     # run <build> <config...>: prints the result, keeps the output
    local tag; tag="$1_$(echo "${*:2}" | tr -d '+' | tr ' =' '_-')"
    "$OUT/obj_$1/sim" "${@:2}" > "$OUT/runs/$tag.log" 2>&1
    grep -E "^D1 (PASS|FAIL)|^errors:" "$OUT/runs/$tag.log" | tr '\n' ' '
}

status=0
# current RTL: must pass everywhere
build fixed       "$R" std
build fixed_byteq "$R" byteq
for v in fixed fixed_byteq; do
    for c in "${CONFIGS[@]}"; do
        line=$(run $v $c)
        printf "%-12s %-32s %s\n" "$v" "$c" "$line"
        echo "$line" | grep -q "D1 PASS" || status=1
    done
done
grep -E "^phase coverage|^  of which|^requests" "$OUT/runs/fixed_memwait-0.log" | sed 's/^/  (fixed +memwait=0) /'
grep -E "^phase coverage \(wide" "$OUT/runs/fixed_wide.log" | sed 's/^/  (fixed +wide) /'
grep -E "^SMC" "$OUT/runs/fixed_smc.log" | sed 's/^/  (fixed +smc) /'

# known-bad RTL and mutants: each must fail somewhere
must_fail() {   # must_fail <build> <what>
    local caught=0 c line first=""
    for c in "${CONFIGS[@]}"; do
        line=$(run "$1" $c)
        if echo "$line" | grep -q "D1 FAIL"; then
            caught=$((caught + 1))
            [ -z "$first" ] && first="$c: $(echo "$line" | sed 's/.*errors: //')"
        fi
    done
    if [ $caught -gt 0 ]; then
        printf "caught     %-16s %d/%d configs; first %s\n" "$1" $caught ${#CONFIGS[@]} "$first"
    else
        printf "NOT CAUGHT %-16s (%s)\n" "$1" "$2"; status=1
    fi
}
OLD="+define+NO_DWR_SIZE"     # fetch RTL from before the snoop took the write size
build orig        "$OUT/rtl_fc5516c" std   "$OLD"; must_fail orig        "pre-fix D1"
build orig_byteq  "$OUT/rtl_fc5516c" byteq "$OLD"; must_fail orig_byteq  "pre-fix D1, byte queue"
build epoch       "$OUT/rtl_dbe89f4" std   "$OLD"; must_fail epoch       "4-bit epoch (C1-1)"
build epoch_byteq "$OUT/rtl_dbe89f4" byteq "$OLD"; must_fail epoch_byteq "4-bit epoch (C1-1), byte queue"
while read -r name variant what; do
    build "mut_$name" "$OUT/mutants/$name" "$variant"
    must_fail "mut_$name" "$what"
done < <(python3 "$HERE/d1_mutants.py" --list)

# the round-1 RTL: each SMC defect it has must fail its own +smc group
smc_groups() {  # smc_groups <build> <group...>: each named group must have failures
    "$OUT/obj_$1/sim" +smc > "$OUT/runs/$1_smc.log" 2>&1
    local g n
    for g in "${@:2}"; do
        n=$(awk -v g="$g" '$1 == "SMC" && $2 == g { print $5 }' "$OUT/runs/$1_smc.log")
        if [ "${n:-0}" -gt 0 ]; then printf "caught     %-16s SMC group %-6s %s failed cases\n" "$1" "$g" "$n"
        else printf "NOT CAUGHT %-16s SMC group %s\n" "$1" "$g"; status=1; fi
    done
}
build r1       "$OUT/rtl_05bb4ea" std   "$OLD"; smc_groups r1       below half wrap
build r1_byteq "$OUT/rtl_05bb4ea" byteq "$OLD"; smc_groups r1_byteq below
exit $status
