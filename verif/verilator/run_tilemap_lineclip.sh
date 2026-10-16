#!/usr/bin/env bash
# Directed Verilator test: $1FF04 bit4/5 per-line clip windows (MAME PR #16038).
set -u
cd "$(dirname "$0")/../.." || exit 1   # repo root
WARN="-Wno-fatal -Wno-lint -Wno-style"
VERILATOR_SAFE="${VERILATOR_SAFE:-verilator-safe}"
VERILATOR_SIM_SAFE="${VERILATOR_SIM_SAFE:-verilator-sim-safe}"
command -v "$VERILATOR_SAFE" >/dev/null 2>&1 || { echo "missing $VERILATOR_SAFE" >&2; exit 127; }
command -v "$VERILATOR_SIM_SAFE" >/dev/null 2>&1 || { echo "missing $VERILATOR_SIM_SAFE" >&2; exit 127; }

source verif/verilator/workspace.sh
s32_verilator_workspace "$VERILATOR_SAFE"
MDIR="$(s32_verilator_mdir vlineclip)"
BUILD_LOG="$S32_VERILATOR_WORKSPACE/vbuild.log"

"$VERILATOR_SAFE" --binary --timing --threads 1 $WARN \
   --top-module tb_tilemap_lineclip +define+SIMULATION \
   -CFLAGS "-D_GLIBCXX_USE_CXX11_ABI=0" --Mdir "$MDIR" -o tb_tilemap_lineclip \
   rtl/video/s32_big_dpram.sv rtl/video/s32_vram.sv rtl/video/s32_tilemap.sv \
   verif/common/tb_tilemap_lineclip.sv >"$BUILD_LOG" 2>&1 || { echo "BUILD FAILED"; tail -20 "$BUILD_LOG"; exit 1; }

out="$("$VERILATOR_SIM_SAFE" -- "$MDIR/tb_tilemap_lineclip.exe" 2>&1)"
echo "$out"
echo "$out" | grep -q "TILEMAP LINECLIP PASS"
