#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# M1.1 front-end mutants (m11_mutants.py): the V2 stage of run_m11.sh (the
# directed, unsupported and probe programs plus N random, default and
# "+memwait=5 +memjit=4") must FAIL on every mutant, except those listed as
# equivalent under the M1.1 microcode (m11_mutants.py EQUIVALENT: reported,
# with where they are caught instead). Reuses the reference
# outputs of an earlier full run_m11.sh (run that first).
#   experiments/M1-functional/run_m11_mutants.sh [nrandom]   (default 50)
#   M11_MUTSET=m12: the M1.2 mutants (ALU / PSW) against the M1.2 program set
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
R="$ROOT/experiments/V2-datapath-floor/rtl"
SET="${M11_MUTSET:-m11}"
SETARG=(); [ "$SET" = m12 ] && SETARG=(--set m12)
OUT="$ROOT/build/M1/$SET/mutants"
export M11_GEN="$HERE/m1_$SET.py" M11_OUT="$ROOT/build/M1/$SET" M11_LABEL="M1.${SET#m1}"
N="${1:-50}"
mkdir -p "$OUT"
[ -f "$ROOT/build/M1/$SET/progs/rand$N.ref" ] || { echo "run run_m11.sh first for $SET (reference outputs)"; exit 1; }
python3 "$HERE/m11_mutants.py" "$R" "$OUT" ${SETARG[@]+"${SETARG[@]}"} || exit 1
status=0; caught=0; total=0; nequiv=0
while read -r name kind what; do
    total=$((total + 1))
    equiv=0; case "$kind" in *-equiv) equiv=1; kind=${kind%-equiv} ;; esac
    if [ "$kind" = rtl ]; then env_rtl="$OUT/$name/rtl"; env_mc="$HERE/m1_m11_microcode.py"
    else env_rtl="$R"; env_mc="$OUT/$name/mcgen.py"; fi
    M11_SKIP_REFS=1 M11_RTL="$env_rtl" M11_MCGEN="$env_mc" M11_TAG="_mut_$name" \
        bash "$HERE/run_m11.sh" "$N" "" "+memwait=5 +memjit=4" > "$OUT/$name.log" 2>&1
    rc=$?
    summary=$(grep -E "^M1\.[0-9] V2" "$OUT/$name.log" | sed 's/; lock-step.*//' | tr '\n' ' ')
    if [ $rc -ne 0 ] && grep -qE "[1-9][0-9]* fail" <<< "$summary" && ! grep -q "BUILD FAILED\|GENERATION FAILED" "$OUT/$name.log"; then
        caught=$((caught + 1))
        how=$(grep -h "^SIMCHECK [0-9]" "$ROOT"/build/M1/$SET/progs/*_mut_"$name" 2>/dev/null | head -1 | cut -c1-60)
        printf "caught     %-18s %-4s %s\n" "$name" "$kind" "$summary${how:+ [first SIMCHECK: $how]}"
    elif [ $equiv -eq 1 ]; then
        nequiv=$((nequiv + 1))
        printf "equivalent %-18s %-4s %s (not caught, as documented in m11_mutants.py)\n" "$name" "$kind" "$what"
    else
        printf "NOT CAUGHT %-18s %-4s %s (%s)\n" "$name" "$kind" "$what" "$summary"; status=1
    fi
done < <(python3 "$HERE/m11_mutants.py" --list ${SETARG[@]+"${SETARG[@]}"})
echo "$M11_LABEL mutants caught: $caught/$total ($nequiv documented equivalent)"
exit $status
