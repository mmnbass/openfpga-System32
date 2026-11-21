#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Quartus runner for V2 CI jobs.
#
#   ci_quartus.sh setup  <toolchain-key> <env-dir>
#   ci_quartus.sh run    <toolchain-key> <env-dir> <project-dir> <tool> [args...]
#
# Toolchain keys come from toolchain.json.
#
# A string value (the DE10 "raetro/quartus:17.1" image) runs exactly as in
# E1 and Q1:
#   - docker run, repository mounted at /build.
#
# An object value (the Pocket Quartus 25.1 Lite pair) gets the hardening
# proven in experiments/toolchain-validation:
#   - both images pulled by digest, and the digests checked;
#   - every mounted Cyclone V device file hash-checked against the audit
#     listing;
#   - Quartus run with --network none, --cap-drop ALL, no-new-privileges, as
#     the runner's non-root uid, with a passwd entry and HOME=/tmp.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cmd="$1"; key="$2"; ENVD="$(mkdir -p "$3" && cd "$3" && pwd)"; shift 3
tc() { python3 -c "import json,sys; v=json.load(open('$HERE/toolchain.json'))['$key']; print(v if isinstance(v,str) else v[sys.argv[1]])" "$1"; }
kind=$(python3 -c "import json; v=json.load(open('$HERE/toolchain.json'))['$key']; print('none' if v is None else 'image' if isinstance(v,str) else 'pair')")
[ "$kind" = none ] && { echo "toolchain '$key' is not set" >&2; exit 1; }
CNAME="v2-devinfo"

case "$cmd" in
setup)
    if [ "$kind" = image ]; then
        docker pull -q "$(tc x)"
        docker run --rm "$(tc x)" quartus_sh --version > "$ENVD/quartus_version.txt"
        exit 0
    fi
    BASE=$(tc base); DEV=$(tc devinfo); DEVDIR=$(tc devinfo_path)
    docker pull -q "$BASE"; docker pull -q "$DEV"
    docker image inspect "$BASE" > "$ENVD/base_inspect.json"
    docker image inspect "$DEV"  > "$ENVD/dev_inspect.json"
    python3 - "$ENVD" "${BASE#*@}" "${DEV#*@}" <<'EOF'
import json, sys
envd, want_base, want_dev = sys.argv[1:]
for name, want in (("base", want_base), ("dev", want_dev)):
    d = json.load(open(f"{envd}/{name}_inspect.json"))[0].get("RepoDigests") or []
    print(name, d)
    if not any(x.endswith(want) for x in d):
        sys.exit(f"{name}: digest mismatch, expected {want}")
EOF
    docker rm -f "$CNAME" >/dev/null 2>&1 || true
    docker create --name "$CNAME" -v "$DEVDIR" "$DEV" >/dev/null
    docker run --rm --network none --cap-drop ALL --volumes-from "$CNAME" --entrypoint bash "$BASE" \
        -c "cd / && find ${DEVDIR#/} -type f -print0 | sort -z | xargs -0 sha256sum" > "$ENVD/devinfo_sha256.txt"
    python3 "$ROOT/experiments/toolchain-validation/devinfo_check.py" "$ENVD/devinfo_sha256.txt" \
        "$ROOT/experiments/toolchain-validation/image-audit/listings/nik_25.1-cyclonev_0.tsv.gz" \
        | tee "$ENVD/devinfo_check.txt"
    docker run --rm --network none --entrypoint cat "$BASE" /etc/passwd > "$ENVD/passwd"
    docker run --rm --network none --entrypoint cat "$BASE" /etc/group  > "$ENVD/group"
    echo "v2:x:$(id -u):$(id -g):v2:/tmp:/bin/sh" >> "$ENVD/passwd"
    echo "v2:x:$(id -g):" >> "$ENVD/group"
    docker run --rm --network none --cap-drop ALL --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$ENVD/passwd:/etc/passwd:ro" -v "$ENVD/group:/etc/group:ro" \
        --entrypoint quartus_sh "$BASE" --version > "$ENVD/quartus_version.txt"
    grep -q "Version $(tc expect_version)" "$ENVD/quartus_version.txt"
    ;;
run)
    proj="$(cd "$1" && pwd)"; shift
    rel="${proj#"$ROOT"/}"
    if [ "$kind" = image ]; then
        exec docker run --rm -v "$ROOT:/build" -w "/build/$rel" "$(tc x)" "$@"
    fi
    tool="$1"; shift
    exec docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
        --user "$(id -u):$(id -g)" -e HOME=/tmp -e USER=v2 -e LOGNAME=v2 \
        -v "$ENVD/passwd:/etc/passwd:ro" -v "$ENVD/group:/etc/group:ro" \
        --volumes-from "$CNAME" -v "$ROOT:/build" -w "/build/$rel" \
        --entrypoint "$tool" "$(tc base)" "$@"
    ;;
*)
    echo "usage: ci_quartus.sh setup|run ..." >&2; exit 2;;
esac
