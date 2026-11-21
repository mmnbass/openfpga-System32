#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# M1.1: the MOV.W / BR8 program set of m1_m11.py.
#   refs  upstream s32_v60 RTL against ref/v60_ref.py: every "agree" and
#         "unsupported" program must agree in final state and in lock-step
#         (m1_lockstep.py: start PC, opcode and registers changed, per retired
#         instruction); every "probe" must still differ (documented discrepancy).
#         Corrupted record sets (a start PC, a register value, a missing
#         record, a PSW value) must be rejected by the lock-step comparison.
#   v2    V2 (tb_m1_v2 + m1_m11_microcode.py) on the same programs:
#         agree        halts; final state and lock-step identical to BOTH
#                      references; no SIMCHECK / ipc errors
#         unsupported  traps with the listed kind at the listed start PC of
#         probe        instruction n (from the program; cross-checked against the
#                      reference record where one exists); its first n lock-step
#                      records and its registers/PSW equal the reference after n
#                      instructions. A wrong expected trap PC must be rejected.
#         truncated    with fetches at or above <limit> withheld (+ackmax), V2
#                      waits: no trap, r instructions retired, state equal to the
#                      reference after n instructions, SIMCHECK 0 (no early
#                      dispatch or immediate latch)
#   The self-modifying-code set (m11_smc_list.txt) runs with the TEST-ONLY
#   --smc-test microcode (review C2-1).
#   experiments/M1-functional/run_m11.sh [nrandom] [config...]
#   Each config is one quoted string of V2 plusargs (e.g. "+memwait=5 +memjit=4",
#   "+wide"); the V2 stage runs once per config ("" = default, the only one if
#   none is given).
#   Environment (used by run_m11_mutants.sh): M11_RTL (V2 RTL dir), M11_MCGEN
#   (microcode generator), M11_TAG (suffix for the V2 build and outputs),
#   M11_SKIP_REFS=1 (reuse the reference outputs of an earlier full run),
#   M11_GEN (program generator, default m1_m11.py; m1_m12.py for M1.2) and
#   M11_OUT (output directory, default build/M1/m11), M11_LABEL (summary label).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="$ROOT/references/s32"
NR="${1:-1000}"
shift || true
CONFIGS=("$@")
[ ${#CONFIGS[@]} -eq 0 ] && CONFIGS=("")
R="${M11_RTL:-$ROOT/experiments/V2-datapath-floor/rtl}"
MCGEN="${M11_MCGEN:-$HERE/m1_m11_microcode.py}"
MT="${M11_TAG:-}"
OUT="${M11_OUT:-$ROOT/build/M1/m11}"
GEN="${M11_GEN:-$HERE/m1_m11.py}"
REFX="${M11_REF_FLAGS:-}"        # B1 tooling: extra m1_ref.py flags (e.g. --ext for V60X); default unchanged
P="$OUT/progs"
mkdir -p "$OUT"
python3 "$ROOT/tools/check_upstream.py" || exit 1
status=0
if [ "${M11_SKIP_REFS:-0}" != 1 ]; then
VFLAGS=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT
  -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME
  -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD
  +define+SIMULATION +define+S32_V60_NO_FP=1)
verilator "${VFLAGS[@]}" --top-module tb_m1_up --Mdir "$OUT/obj_up" -o sim \
    "$U/rtl/cpu/v60/s32_v60.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" \
    "$HERE/tb/m1_mem.sv" "$HERE/tb/tb_m1_up.sv" > "$OUT/build_up.log" 2>&1 || { echo "BUILD FAILED"; tail "$OUT/build_up.log"; exit 1; }
python3 "$GEN" "$P" "$NR"

agree=0; disagree=0; failed=()
while read -r cls name n kind epc; do
    p="$P/$name"
    python3 "$HERE/m1_ref.py" "$p.bin" --lockstep $REFX > "$p.ref" 2> "$p.ref.err"
    "$OUT/obj_up/sim" +img="$p.hex" +maxcyc=200000 +lockstep > "$p.up" 2>&1
    python3 "$HERE/m1_compare.py" "$p.ref" "$p.up" --label-a ref --label-b rtl > "$p.cmp"; same=$?
    python3 "$HERE/m1_lockstep.py" "$p.ref" "$p.up" --label-a ref --label-b rtl >> "$p.cmp" || same=1
    case "$cls" in
        agree|unsupported|truncated)
            if [ $same -eq 0 ]; then agree=$((agree + 1)); else disagree=$((disagree + 1)); failed+=("$name"); status=1; fi ;;
        probe)
            if [ $same -eq 0 ]; then echo "  probe $name: references now AGREE (documented discrepancy no longer reproduces)"; status=1
            else echo "  probe $name: references differ, as documented ($(tail -1 "$p.ref.err" 2>/dev/null | cut -c1-60))"; fi ;;
    esac
done < <(cat "$P/m11_list.txt" "$P/m11_smc_list.txt")
echo "${M11_LABEL:-M1.1} REFS: $agree agree, $disagree disagree ($(grep -c '^agree rand' "$P/m11_list.txt") random, $(grep -vc '^agree rand\|^probe' "$P/m11_list.txt") directed/unsupported)"
for f in "${failed[@]:-}"; do [ -n "$f" ] && echo "  disagree: $f"; done
echo "  lock-step records compared: $(while read -r c nm rest; do cat "$P/$nm.ref"; done < "$P/m11_list.txt" | grep -c '^L ') (reference side)"
# the lock-step comparison must reject corrupted record sets
c="$P/$(awk '$1 == "agree" && $2 !~ /^rand/ {print $2; exit}' "$P/m11_list.txt")"
for how in pc reg drop psw; do
    python3 - "$c.up" "$c.bad_$how" "$how" <<'EOF'
import re, sys
t = open(sys.argv[1]).read().splitlines(); how = sys.argv[3]
i = next(k for k, l in enumerate(t) if l.startswith("L ") and " R" in l)
if how == "pc":   t[i] = re.sub(r"pc=\w{8}", "pc=0000beef", t[i])
if how == "reg":  t[i] = re.sub(r"(R\d\d=)\w{8}", r"\g<1>0badf00d", t[i], count=1)
if how == "drop": del t[i]
if how == "psw":  t[i] = re.sub(r" PSW=\w{8}", "", t[i]) + " PSW=0badf00d"
open(sys.argv[2], "w").write("\n".join(t) + "\n")
EOF
    if python3 "$HERE/m1_lockstep.py" "$c.ref" "$c.bad_$how" > /dev/null; then
        echo "  lock-step self-check ($how): corrupted records NOT rejected"; status=1
    else echo "  lock-step self-check ($how): corrupted records rejected"; fi
done
else
    python3 "$GEN" "$P" "$NR" > /dev/null
fi

# ---------------------------------------------------------------- V2
verilator --binary --timing --threads 1 -Wno-fatal -Wno-lint -Wno-style -Wno-TIMESCALEMOD \
    +define+SIMULATION -I"$R" --top-module tb_m1_v2 --Mdir "$OUT/obj_v2$MT" -o sim \
    $(sed "s|^|$R/|" "$R/files.f") "$U/rtl/cpu/v60/s32_v60_bus.sv" \
    "$HERE/tb/m1_mem.sv" "$HERE/tb/v2_sim_observer.sv" "$HERE/tb/v2_sim_checks.sv" "$HERE/tb/tb_m1_v2.sv" \
    > "$OUT/build_v2$MT.log" 2>&1 || { echo "V2 BUILD FAILED"; tail "$OUT/build_v2$MT.log"; exit 1; }
MC="$OUT/mc$MT"
python3 "$MCGEN" "$MC" > /dev/null || { echo "MICROCODE GENERATION FAILED"; exit 1; }
python3 "$MCGEN" "$MC.smc" --smc-test > /dev/null || { echo "MICROCODE GENERATION FAILED"; exit 1; }
python3 "$MCGEN" "$MC.smcm" --smc-test --smc-merged > /dev/null || { echo "MICROCODE GENERATION FAILED"; exit 1; }
sym_s() { grep "^$1=" "$MC.smc/m1_symbols.txt" | cut -d= -f2; }
SYMS_S=(+haltpc=$(sym_s halt) +trappc=$(sym_s trap) +trapmodepc=$(sym_s trap_mode) +trapimmpc=$(sym_s trap_immdest))
sym() { grep "^$1=" "$MC/m1_symbols.txt" | cut -d= -f2; }
SYMS=(+haltpc=$(sym halt) +trappc=$(sym trap) +trapmodepc=$(sym trap_mode) +trapimmpc=$(sym trap_immdest))
for cfg in "${CONFIGS[@]}"; do
    tag=$(echo "${cfg:-default}" | tr -d '+' | tr ' =' '_-')$MT
    vpass=0; vfail=0; vfailed=()
    while read -r cls name n kind epc; do
        p="$P/$name"; v="$p.v2_$tag"
        "$OUT/obj_v2$MT/sim" +img="$p.hex" +mc="$MC" "${SYMS[@]}" +lockstep +maxcyc=400000 $cfg > "$v" 2>&1
        ok=1
        grep -q "^SIMCHECK errors=0 ipc_errors=0" "$v" || ok=0
        case "$cls" in
            agree)
                grep -q "^TRAP" "$v" && ok=0
                python3 "$HERE/m1_compare.py" "$p.ref" "$v" --label-a ref --label-b v2 > "$v.cmp" || ok=0
                python3 "$HERE/m1_compare.py" "$p.up" "$v" --label-a rtl --label-b v2 >> "$v.cmp" || ok=0
                python3 "$HERE/m1_lockstep.py" "$p.ref" "$v" --label-a ref --label-b v2 >> "$v.cmp" || ok=0
                python3 "$HERE/m1_lockstep.py" "$p.up" "$v" --label-a rtl --label-b v2 >> "$v.cmp" || ok=0 ;;
            unsupported|probe)
                rpc=$(grep "^L $n " "$p.ref" | sed 's/.*pc=\([0-9a-f]*\).*/\1/')
                [ -n "$rpc" ] && [ "$rpc" != "$epc" ] && ok=0         # the reference disagrees with the program
                grep -qx "TRAP $kind ipc=$epc" "$v" || ok=0
                python3 "$HERE/m1_lockstep.py" "$p.ref" "$v" --prefix "$n" --label-a ref --label-b v2 > "$v.cmp" || ok=0
                python3 "$HERE/m1_ref.py" "$p.bin" "$n" $REFX > "$p.ref_$n"
                python3 "$HERE/m1_compare.py" "$p.ref_$n" "$v" --label-a ref --label-b v2 --unhalted >> "$v.cmp" || ok=0 ;;
            truncated)
                "$OUT/obj_v2$MT/sim" +img="$p.hex" +mc="$MC" "${SYMS[@]}" +lockstep +maxcyc=20000 +ackmax="$kind" $cfg > "$v" 2>&1
                grep -q "^SIMCHECK errors=0 ipc_errors=0" "$v" || ok=0
                grep -q "^TRAP" "$v" && ok=0
                grep -q "^END halted=0 instrs=$epc " "$v" || ok=0
                python3 "$HERE/m1_lockstep.py" "$p.ref" "$v" --prefix "$epc" --label-a ref --label-b v2 > "$v.cmp" || ok=0
                python3 "$HERE/m1_ref.py" "$p.bin" "$n" $REFX > "$p.ref_$n"
                python3 "$HERE/m1_compare.py" "$p.ref_$n" "$v" --label-a ref --label-b v2 --unhalted --no-instrs >> "$v.cmp" || ok=0 ;;
        esac
        if [ $ok -eq 1 ]; then vpass=$((vpass + 1)); else vfail=$((vfail + 1)); vfailed+=("$name"); status=1; fi
    done < "$P/m11_list.txt"
    recs=$(while read -r c nm rest; do cat "$P/$nm.v2_$tag"; done < "$P/m11_list.txt" | grep -c '^L ')
    red=$(while read -r c nm rest; do grep "^REDIRECTS" "$P/$nm.v2_$tag"; done < "$P/m11_list.txt" | awk '{for (i = 2; i <= 4; i++) {split($i, a, "="); t[a[1]] += a[2]}} END {print t["reset"] " reset redirects, " t["instr"] " instruction (BR8) redirects, " t["instr_outstanding"] " of them with a fetch request outstanding"}')
    echo "${M11_LABEL:-M1.1} V2 [${cfg:-default}]: $vpass pass, $vfail fail; lock-step records $recs; $red"
    for f in "${vfailed[@]:-}"; do [ -n "$f" ] && echo "  V2 fail: $f"; done | head -20
    # self-modifying code (review C2-1): the TEST-ONLY [Rn]-store microcode,
    # with the store's bus word separate from (smc) or merged with (smcm) the
    # jump to the decode poll
    for sv in smc smcm; do
    symv() { grep "^$1=" "$MC.$sv/m1_symbols.txt" | cut -d= -f2; }
    SYMS_S=(+haltpc=$(symv halt) +trappc=$(symv trap) +trapmodepc=$(symv trap_mode) +trapimmpc=$(symv trap_immdest))
    spass=0; sfail=0
    while read -r cls name rest; do
        p="$P/$name"; v="$p.v2_${tag}_$sv"
        "$OUT/obj_v2$MT/sim" +img="$p.hex" +mc="$MC.$sv" "${SYMS_S[@]}" +lockstep +maxcyc=400000 $cfg > "$v" 2>&1
        ok=1
        grep -q "^SIMCHECK errors=0 ipc_errors=0" "$v" || ok=0
        grep -q "^TRAP" "$v" && ok=0
        python3 "$HERE/m1_compare.py" "$p.ref" "$v" --label-a ref --label-b v2 > "$v.cmp" || ok=0
        python3 "$HERE/m1_compare.py" "$p.up" "$v" --label-a rtl --label-b v2 >> "$v.cmp" || ok=0
        python3 "$HERE/m1_lockstep.py" "$p.ref" "$v" --label-a ref --label-b v2 >> "$v.cmp" || ok=0
        if [ $ok -eq 1 ]; then spass=$((spass + 1)); else sfail=$((sfail + 1)); echo "  V2 SMC fail ($sv): $name"; status=1; fi
    done < "$P/m11_smc_list.txt"
    # the trap-PC check must reject a wrong expected PC (review C2-2)
    tp=$(awk '$1 == "probe" || $1 == "unsupported" {print $2; exit}' "$P/m11_list.txt")
    tpc=$(awk -v n="$tp" '$2 == n {print $5}' "$P/m11_list.txt")
    tkind=$(awk -v n="$tp" '$2 == n {print $4}' "$P/m11_list.txt")
    wrong=$(printf "%08x" $(( 0x$tpc + 1 )))
    if grep -qx "TRAP $tkind ipc=$wrong" "$P/$tp.v2_$tag" || ! grep -qx "TRAP $tkind ipc=$tpc" "$P/$tp.v2_$tag"; then
        echo "  trap-PC self-check: a wrong expected PC was NOT rejected"; status=1
    else echo "  trap-PC self-check: a wrong expected PC is rejected"; fi
    echo "${M11_LABEL:-M1.1} V2 SMC [${cfg:-default}] [$sv]: $spass pass, $sfail fail (stores into queued code, test-only [Rn] microcode)"
    done
done
exit $status
