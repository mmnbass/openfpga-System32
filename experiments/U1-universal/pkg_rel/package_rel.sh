#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Assemble the release/s32-v1 package (Sega System 32 for Analogue Pocket, mmnbass.System32: one bitstream, GA2 + Spider-Man by instance
# file; pkg_rel = U1b's pkg with the release identity and the sound ROM slot id 7). Does NOT build, stage
# or deploy anything.
#   experiments/U1-universal/pkg_rel/package_rel.sh <ga2_d2.rbf> <outdir>
# <outdir> must not exist (nothing is ever overwritten or removed). Result:
#   <outdir>/stage/                        the SD-card tree (Cores/, Platforms/, Assets/)
#   <outdir>/mmnbass.System32_0.9.0-rc1.zip     the same tree, zipped
#   <outdir>/MANIFEST.sha256               sha256 of every file in stage/, the zip, and the input rbf
# As SpidPlay1 / GA2Play8 package_d2.sh: the .rbf is bit-reversed into bitstream.rbf_r, icon.bin and the platform image are drawn.
# Added for U1: the per-game instance files (committed, checked by u1_pkg.py check) and the per-game 64-byte configuration files
# (made by u1_pkg.py cfg from games/*.json, checked against games/cfg_files.sha256). No ROM data is ever included.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RBF="${1:-}"; OUT="${2:-}"
[ -n "$RBF" ] && [ -n "$OUT" ] || { echo "usage: package_rel.sh <ga2_d2.rbf> <outdir>" >&2; exit 2; }
[ -f "$RBF" ] && [ -s "$RBF" ] || { echo "refusing: no bitstream: '$RBF' is not a non-empty file" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "refusing: outdir '$OUT' already exists (use a fresh directory)" >&2; exit 2; }
CORE=${PKG_CORE:-mmnbass.System32}; PLAT=${PKG_PLATFORM:-segas32}; TPL=${PKG_TEMPLATES:-$HERE}   # SN: package_sn.sh sets these (as release/s32-v1-diag / -ro did)
VER=${PKG_VERSION:-$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["core"]["metadata"]["version"])' "$TPL/Cores/$CORE/core.json")}   # default: the version in core.json (as release/s32-v0.1)
python3 -B "$HERE/u1_pkg.py" check
mkdir -p "$OUT/stage"
cp -R "$TPL/Cores" "$TPL/Platforms" "$TPL/Assets" "$OUT/stage/"
[ "$CORE" = mmnbass.System32 ] && cp "$HERE/../../../LICENSE" "$OUT/stage/Cores/$CORE/LICENSE.txt"   # the GPL-3.0 text travels with the bitstream (release/s32-v0.1, R7)
python3 -B "$HERE/u1_pkg.py" cfg "$OUT/stage/Assets/$PLAT/common"
python3 -B "$HERE/../u1_cfg.py" check-files "$HERE/../games/cfg_files.sha256" "$OUT/stage/Assets/$PLAT/common/"*_u1cfg.bin
mkdir -p "$OUT/stage/Platforms/_images"
python3 -I "$HERE/make_images.py" "$OUT/stage/Cores/$CORE/icon.bin" "$OUT/stage/Platforms/_images/$PLAT.bin"
python3 -I - "$RBF" "$OUT/stage/Cores/$CORE/bitstream.rbf_r" <<'PY'
import sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
table = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))
dst.write_bytes(src.read_bytes().translate(table))
PY
find "$OUT/stage" -name '.*' -print | grep . && { echo "unexpected dotfile in stage" >&2; exit 1; } || true
( cd "$OUT/stage" && zip -qrX "../${CORE}_${VER}.zip" . -x ".*" )
python3 -I - "$OUT" "$RBF" "${CORE}_${VER}.zip" <<'PY'
import hashlib, sys
from pathlib import Path
out, rbf, zipname = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
lines = [f"{h(p)}  stage/{p.relative_to(out / 'stage')}" for p in sorted((out / "stage").rglob("*")) if p.is_file()]
lines.append(f"{h(out / zipname)}  {zipname}")
lines.append(f"{h(rbf)}  INPUT {rbf.name} (not part of the package)")
(out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
PY
python3 -B "$HERE/validate_pkg.py" "$OUT/stage" --rbf "$RBF" ${S32_UPSTREAM:+--upstream "$S32_UPSTREAM"}
echo "built $OUT/${CORE}_${VER}.zip"; cat "$OUT/MANIFEST.sha256"
