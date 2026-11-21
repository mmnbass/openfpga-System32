#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# B2: build the S0 upstream and V2 benches (with the +iorec replay in s0_mem.sv) into a NEW directory.
#   experiments/M1-functional/b2_build.sh <outdir>      (same verilator flags as experiments/S0-boot/run_s0.sh)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$1"; [ ! -e "$OUT" ] || { echo "$OUT exists"; exit 2; }; mkdir -p "$OUT"
U="$ROOT/references/s32"; R="$ROOT/experiments/V2-datapath-floor/rtl"; M1="$HERE"; S0="$ROOT/experiments/S0-boot"
python3 "$ROOT/tools/check_upstream.py" || exit 1
VUP=(--binary --timing --threads 1 -Wno-fatal -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-UNOPTFLAT -Wno-CASEINCOMPLETE -Wno-BLKANDNBLK
  -Wno-MULTIDRIVEN -Wno-INITIALDLY -Wno-DECLFILENAME -Wno-PINMISSING -Wno-UNSIGNED -Wno-WIDTH -Wno-TIMESCALEMOD +define+SIMULATION +define+S32_V60_NO_FP=1)
verilator "${VUP[@]}" --top-module tb_s0_up --Mdir "$OUT/obj_s0up" -o sim "$U/rtl/cpu/v60/s32_v60.sv" "$U/rtl/cpu/v60/s32_v60_bus.sv" \
    "$S0/tb/s0_mem.sv" "$S0/tb/tb_s0_up.sv" > "$OUT/build_up.log" 2>&1 || { echo "UPSTREAM BUILD FAILED"; tail "$OUT/build_up.log"; exit 1; }
verilator --binary --timing --threads 1 -Wno-fatal -Wno-lint -Wno-style -Wno-TIMESCALEMOD +define+SIMULATION -I"$R" --top-module tb_s0_v2 --Mdir "$OUT/obj_s0v2" -o sim \
    $(sed "s|^|$R/|" "$R/files.f") "$U/rtl/cpu/v60/s32_v60_bus.sv" "$S0/tb/s0_mem.sv" "$M1/tb/v2_sim_observer.sv" "$M1/tb/v2_sim_checks.sv" "$S0/tb/tb_s0_v2.sv" \
    > "$OUT/build_v2.log" 2>&1 || { echo "V2 BUILD FAILED"; tail "$OUT/build_v2.log"; exit 1; }
echo built
