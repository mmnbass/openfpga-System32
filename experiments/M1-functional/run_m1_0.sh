#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# M1.0 in full: reference agreement (100 random seeds + deterministic
# programs + documented-discrepancy probes), the V2 runs, and the D1
# fetch/redirect regression (fixed RTL must pass, pre-fix RTL must fail).
#   experiments/M1-functional/run_m1_0.sh [nseeds]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/run_refs.sh" "${1:-100}"; r=$?
"$HERE/run_v2.sh"; v=$?
"$HERE/run_d1.sh"; d=$?
echo "M1.0: references $([ $r -eq 0 ] && echo OK || echo FAIL); V2 $([ $v -eq 0 ] && echo OK || echo FAIL); D1 regression $([ $d -eq 0 ] && echo OK || echo FAIL)"
[ $r -eq 0 ] && [ $v -eq 0 ] && [ $d -eq 0 ]
