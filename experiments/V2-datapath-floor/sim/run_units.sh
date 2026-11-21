#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Focused edge-case tests: shifter SHA-left result/carry/overflow and the
# bit-field first-set search against independent reference models.
#   experiments/V2-datapath-floor/sim/run_units.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RTL="$HERE/../rtl"
OUT="$HERE/../../../build/V2-units"
mkdir -p "$OUT"
verilator --binary --timing -Wno-fatal -Wno-lint -Wno-style -I"$RTL" --top-module tb_v2_units \
    --Mdir "$OUT/obj" -o sim "$HERE/tb_v2_units.sv" "$RTL/v2_shift.sv" "$RTL/v2_bcd_bitfield.sv" "$RTL/v2_clz.sv" >/dev/null
out=$("$OUT/obj/sim")
echo "$out" | tail -22
echo "$out" | grep -q "V2 UNITS PASS"
