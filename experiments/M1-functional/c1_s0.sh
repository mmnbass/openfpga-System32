#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# B2 development / measurement loop: the LIVE builder's S0 (reset-native) microcode on the real GA2 ROM, V2 against an upstream reference log
# (default-mode s0_compare.py: reach = first divergence; the stop must be a trap on an unsupported form, never a wrong value).
#   experiments/M1-functional/b1_s0.sh <tag> [maxinstr] [upstream-log]
# Output: build/M1/c1/<tag> (must NOT exist). Uses the existing S0 bench binaries of build/M1/r4/r4b_run2 (the RTL is unchanged by B2).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
TAG="$1"; N="${2:-200000}"; UP="${3:-$ROOT/build/M1/b2/up_ref2/up.log}"
BIN="${B2_BIN:-$ROOT/build/M1/b2/obj1}"
IOREC="${B2_IOREC:-$ROOT/build/M1/b2/ioref/io_reads.txt}"
OUT="$ROOT/build/M1/c1/$TAG"; [ ! -e "$OUT" ] || { echo "$OUT exists"; exit 2; }
mkdir -p "$OUT"
ROM="$ROOT/build/S0/rom/ga2u_prog.hex"; TR="$ROOT/build/S0/boot/boot_head.trace"
python3 "$HERE/m1_m11_microcode.py" "$OUT/mc" --reset-native > "$OUT/mc.out" || exit 1
sym() { grep "^$1=" "$OUT/mc/m1_symbols.txt" | cut -d= -f2; }
"$BIN/obj_s0v2/sim" +rom="$ROM" +mc="$OUT/mc" +haltpc="$(sym halt)" +trappc="$(sym trap)" +trapmodepc="$(sym trap_mode)" +trapimmpc="$(sym trap_immdest)" \
    +iorec="$IOREC" +lockstep +maxinstr="$N" +maxcyc=400000000 +memlog > "$OUT/v2.log" 2>&1
grep -h "^END\|^TRAP\|^SIMCHECK\|^STALL" "$OUT/v2.log"
python3 "$HERE/../S0-boot/s0_compare.py" "$UP" "$OUT/v2.log" "$TR" --json "$OUT/s0_result.json" > "$OUT/s0_stdout.json"; echo "s0_compare rc=$?"
python3 - "$OUT/s0_result.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); print("reach", d["v2_reach"], d["v2_end"], json.dumps(d["v2_end_detail"])[:300])
PY
