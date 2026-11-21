#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# B2: re-run the whole-chip V2 sim (experiments/WC-sim/run_wcv2.sh objects of an EARLIER run dir) with the LIVE microcode, into a NEW dir.
#   experiments/M1-functional/b2_wc.sh <prior-run-dir> <new-dir> [max-instr]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
PRIOR="$1"; RUN="$2"; MAXI="${3:-2000000}"
[ -e "$RUN" ] && { echo "refusing: $RUN exists"; exit 2; }
mkdir -p "$RUN/sim"; RUN="$(cd "$RUN" && pwd)"
python3 -B "$HERE/m1_m11_microcode.py" "$RUN/mc" --reset-native > "$RUN/mc.log" 2>&1 || { echo "microcode generation failed"; exit 1; }
sym() { grep "^$1=" "$RUN/mc/m1_symbols.txt" | cut -d= -f2; }
cp "$ROOT/references/s32/rtl/cpu/v25/s80x86/generated/microcode.bin" "$RUN/sim/"
( cd "$RUN/sim" && python3 "$ROOT/tools/heavy_run.py" --label "batch2 wc sim $(basename "$RUN")" -- "$PRIOR/obj/wc_v2_sim" --rom "$PRIOR/rom/main_rom.bin" --tiles "$PRIOR/rom/tiles.bin" --out "$RUN/sim" \
    --eeprom "${EEPROM_IMAGE:-$ROOT/build/WC/mame_eep1/nvram/ga2u/mainpcb_eeprom}" --cap-nonblack --video-delay "${VIDEO_DELAY:-0}" --max-frames "${FRAMES:-1000000}" +mc="$RUN/mc" +haltpc="$(sym halt)" +trappc="$(sym trap)" +trapmodepc="$(sym trap_mode)" \
    +trapimmpc="$(sym trap_immdest)" +maxinstr="$MAXI" +stallcyc="${STALLCYC:-100000000}" +lockstep +buslog +busall +counters +privev +irqlog +maxinstcyc="${MAXINSTCYC:-1000000000}" > "$RUN/sim.log" 2>&1 )
grep -E "^(TRAP|STALL|END|SIMCHECK|TB)" "$RUN/sim.log"
