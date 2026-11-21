#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate the release/s32-v1 package tree (mmnbass.System32 / segas32: ONE bitstream, the game chosen by an APF instance file; <out>/stage
from package_rel.sh). U1b's validator (experiments/U1-universal/pkg) with the release identity, the sound ROM slot id 7 and the release top.
  validate_pkg.py <pkgdir> [--rbf <input.rbf>]   exit 0 = every check passed, 1 = at least one failed
  validate_pkg.py --selftest <fresh-workdir>     package a clearly FAKE placeholder .rbf, validate it, then run the negative controls
                                                 (tampered copies; each must FAIL with its expected message)

What it can establish: the package is internally consistent with the RTL that is fitted (slot ids, bridge windows, sizes and the
datatable position parsed from experiments/U1-universal/rtl/derived/d2_core_top_rel.sv) and with Analogue's documented data.json /
instance-json rules (fetched 2026-11-14), and each game's instance file selects that game's own files and its own, identity-checked
configuration file. What it cannot: how the Pocket's firmware resolves instance file names, names saves under an instance, or sequences
the slot loads. Those are hardware observations (U1c).

Rules (each one a check below):
  R1  file set exactly: the core's JSON files, info.txt, icon.bin, bitstream.rbf_r; the platform JSON and image; the common README; one
      configuration file <set>_u1cfg.bin per game in Assets/s32univ/common; one instance file <set>.json per game in the core folder
      Assets/s32univ/mmnbass.S32Univ (and nothing else: no ROM-like file can hide).
  R2  core.json: platform segas32, author.shortname = the folder, version VERSION, names both supported games and no other, the framework block, ONE
      bitstream (cores = [default, id 0, bitstream.rbf_r]).
  R3  data.json order = [Game 0, Program 1, Tile 2, Sprite 3, Save 10, EEPROM default 4, Configuration 6, Sound 7]:
      * slot 0 "Game": the only slot with the Instance JSON bit (4), which Analogue requires in the FIRST slot together with the
        core-specific bit (1); extensions ["json"]; required; parameters 0x113 (+ reloadable 0 + full reload 8, the released pattern);
      * the Save slot's datatable entry 2 * index + 1 equals the RTL's datatable_addr (the size APF reads back on exit comes from it),
        so with the Game slot first the Save slot must stay at index 4;
      * every slot reloadable in the core UI (bit 0) also requests a full core reload (bit 8) (owner decision 2026-11-05: the read-back
        verifier latches done);
      * ROM (incl. the sound ROM, id 7 at the RTL's 0x7000_0000 / 4 MiB) / EEPROM-default / configuration slots: bridge address and size from the RTL, no default filename (only the instance names
        files, so no file can be shared by both games through a default), not core-specific (common folder), not reloadable;
      * Save: nonvolatile, filename cloned from slot 0 (bit 2) so each instance file gives its own save, bit 5 clear, 0x84 (as SpidPlay1
        and the released core), ["sav"], 128 bytes = the RTL datatable value, optional;
      * EEPROM default: optional, read-only, not nonvolatile; configuration: required, 64 bytes, inside the loader's window, and
        size_exact 0x40 (U1b review finding 1: APF writes whole 32-bit words, so a 61-63-byte file would reach the core as 64 bridge
        bytes and pass the RTL's "every word written" completeness check; with size_exact APF loads only a 64-byte file [D]). No
        other slot sets size_exact.
  R4  each game (experiments/U1-universal/games/<set>.json) has <set>.json: magic, data_path "", core_select id 0 / select false;
      data_slots name ids 1, 2, 3, 6, 7 (and 4 only if the game has a default-EEPROM file), each once, never 0 or 10, ids that data.json
      declares; flat ASCII names; no file name is used by two games; the id-6 file is in the package, equals u1_cfg.make(<set>) byte
      for byte and its sha256 is the games/cfg_files.sha256 entry (configuration identity), decodes with reason 0 (u1_cfg's reference
      model of u1_board_cfg) and carries the game's own descriptor; the ROM names appear in the common README.
  R5  instance file names differ case-insensitively (APF names each save after slot 0's file: one save per game).
  R6  video / input / platform / images / bitstream as the GA2Play8 and SpidPlay1 validators (input = GA2Play8's six mappings).
  R7  licence notices for the synthesized bitstream (release/s32-v0.1, Michael 2026-11-21): Cores/<core>/LICENSE.txt is the complete
      GNU GPL v3 text (sha256 of the canonical gpl-3.0.txt, = the repository LICENSE); Cores/<core>/THIRD_PARTY_NOTICES.txt names the
      source address and contains, verbatim (comment markers removed), every T80 file's copyright notice, conditions and disclaimer
      (the BSD-style T80 terms require them "in the documentation and/or other materials provided with the distribution" for the
      synthesized form) and every agg23 MIT file's copyright and permission notice. The blocks are extracted from the compiled
      source files themselves (the pinned upstream T80 files: <repo>/references/s32, or --upstream <dir>), so a missing or edited
      notice fails."""
import hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
U1 = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(U1))
import u1_cfg  # noqa: E402

RTL = U1 / "rtl" / "derived" / "d2_core_top_rel.sv"
VIDEO_RTL = ROOT / "experiments" / "P1-pocket-top" / "rtl" / "ga2_video.sv"
CORE_DIR, PLATFORM = "mmnbass.System32", "segas32"   # provisional public identifiers (Michael 2026-11-19)
VERSION = "0.1.0"   # release/s32-v0.1: the first public release (Michael 2026-11-21)
GAME_TITLES = ("Golden Axe: The Revenge of Death Adder", "Spider-Man: The Video Game")   # public spelling (Michael 2026-11-21)
INST = f"Assets/{PLATFORM}/{CORE_DIR}"
COMMON = f"Assets/{PLATFORM}/common"
ORDER = [0, 1, 2, 3, 10, 4, 6, 7]
EEPROM_DEFAULT_GAMES = {"spidman"}             # games whose instance names a default-EEPROM file (SpidPlay1's optional image); GA2 has none
ICON_BYTES, PLAT_BYTES = 36 * 36 * 2, 521 * 165 * 2
ROMLIKE_EXT = {".bin", ".rom", ".zip", ".7z", ".mra", ".j64", ".jag", ".abs", ".cof", ".sav", ".mpr", ".epr", ".hex"}
ROMLIKE_SIZES = {1 << 20, 2 << 20, 4 << 20, 8 << 20, 16 << 20, 512 << 10, 256 << 10, 128 << 10}
WANT_MAP = [(0, "pad_btn_a"), (1, "pad_btn_b"), (2, "pad_btn_x"), (3, "pad_btn_y"), (20, "pad_btn_start"), (21, "pad_btn_select")]
WANT_NAMES = ["Button 1", "Button 2", "Button 3", "Button 3 (alt)", "Start", "Coin"]   # release/s32-v0.1: game-neutral labels (universal core)
GPL3_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"   # the canonical GNU GPL v3 text (gpl-3.0.txt)
SOURCE_URL = "https://github.com/mmnbass/openfpga-System32"
MIT_FILES = ("experiments/P1-pocket-top/rtl/third_party/data_loader.sv", "experiments/D2-pocket-top/rtl/third_party/sound_i2s.sv",
             "experiments/D2-pocket-top/rtl/third_party/sync_fifo.sv")
T80_FILES = ("T80.vhd", "T80_ALU.vhd", "T80_MCode.vhd", "T80_Reg.vhd", "T80pa.vhd", "T80s.vhd")   # the T80 files the release compiles
UPSTREAM = [ROOT / "references" / "s32"]                     # --upstream overrides (a worktree without the submodule checked out)
FRAMEWORK = {"target_product": "Analogue Pocket", "version_required": "1.1", "sleep_supported": False,
             "dock": {"supported": True, "analog_output": False}, "hardware": {"link_port": False, "cartridge_adapter": -1}}


def games():
    return {p.stem: json.loads(p.read_text()) for p in u1_cfg.games()}


def cfg_manifest():
    return dict(reversed(ln.split()) for ln in (U1 / "games" / "cfg_files.sha256").read_text().splitlines() if ln.strip())


def rtl_constants():
    t = RTL.read_text()
    nib = lambda name: int(re.search(rf"ADDRESS_MASK_UPPER_4\(4'h([0-9a-fA-F])\)[^;]*?\)\s*{name}\s*\(", t).group(1), 16) << 28
    c = {"ids": sorted(set(int(x) for x in re.findall(r"dataslot_requestwrite_id\s*==\s*16'd(\d+)", t)))}
    c["addr"] = {i: nib(f"slot{i}_loader") for i in (1, 2, 3)}
    c["snd"] = nib("slot4_loader")                      # rel: the sound-ROM loader keeps GA2Audio3's name slot4_loader (rel_derive.py C)
    c["hdr"] = {int(m.group(1)): (int(m.group(2).replace("_", ""), 16), int(m.group(3)) << 20)
                for m in re.finditer(r"id (\d) [^,(]*\((?:bridge )?(0x[0-9A-Fa-f_]+), (\d+) MB\)", t)}
    c["cfg"] = nib("cfg_loader")
    c["cfg_window"] = 1 << int(re.search(r"ADDRESS_SIZE\((\d+)\)[^;]*?\)\s*cfg_loader\s*\(", t).group(1))
    c["eep_def"], c["eep_save"] = nib("eepdef_loader"), nib("eepsave_loader")
    c["eep_rd"] = int(re.search(r"sm_eep_bridge_rd #\(\.ADDRESS_MASK_UPPER_4\(4'h([0-9a-fA-F])\)", t).group(1), 16) << 28
    c["eep_mux"] = int(re.search(r"32'h([0-9a-fA-F])xxxxxxx: bridge_rd_data <= eep_bridge_rd_data", t).group(1), 16) << 28
    c["dt_addr"] = int(re.search(r"datatable_addr = 10'd(\d+)", t).group(1))
    c["dt_size"] = int(re.search(r"datatable_data = 32'd(\d+)", t).group(1))
    c["cfg_gate"] = re.search(r"\|\|\s*!u1_ok\)\s*begin\s*rst_r\s*<=\s*1'b1", t) is not None      # the CPU reset waits for a valid file
    v = VIDEO_RTL.read_text()
    c["w"] = int(re.search(r"H_PIXELS\s*=\s*(\d+)", v).group(1)); c["h"] = int(re.search(r"V_LINES\s*=\s*(\d+)", v).group(1))
    return c


def reverse_bits(b):
    return b.translate(bytes(int(f"{i:08b}"[::-1], 2) for i in range(256)))


def P(s):
    try:
        return int(str(s.get("parameters")), 16)
    except Exception:
        return None


def _uncomment(lines, mark):
    out = []
    for l in lines:
        l = l.rstrip()
        if not l:
            out.append(""); continue                              # a blank line between comment lines (the agg23 headers)
        if not l.startswith(mark): raise ValueError(f"not a {mark} comment line: {l!r}")
        l = l[len(mark):]
        out.append(l[1:] if l.startswith(" ") else l)
    return out


def notice_blocks():
    """[(source, text that THIRD_PARTY_NOTICES.txt must contain verbatim)], from the compiled source files themselves."""
    req = []
    t80 = UPSTREAM[0] / "rtl" / "audio" / "T80"
    for n in T80_FILES:
        L = (t80 / n).read_text().splitlines(); h = []
        for l in L:
            if l.startswith("--") or not l.strip(): h.append(l)
            else: break
        h = _uncomment([l for l in h if l.strip()], "--")
        a = next(i for i, l in enumerate(h) if l.startswith("Copyright (c)") and "Daniel Wallner" in l)
        b = next(i for i, l in enumerate(h) if "POSSIBILITY OF SUCH DAMAGE." in l)
        req.append((f"T80 {n} notice", "\n".join(h[a:b + 1])))
        req += [(f"T80 {n} copyright line", l) for l in h if l.startswith("Copyright (c)")]
    for f in MIT_FILES:
        L = [l.rstrip() for l in (ROOT / f).read_text().splitlines()]
        a = L.index("// MIT License"); b = next(i for i in range(a, len(L)) if L[i] == "// SOFTWARE.")
        req.append((f"MIT {Path(f).name} notice", "\n".join(_uncomment(L[a:b + 1], "//"))))
    return req


def validate(pkg, rbf=None):
    pkg = Path(pkg); errs = []; E = errs.append
    try:
        K = rtl_constants()
    except Exception as e:
        return [f"cannot parse RTL constants: {e}"]
    G = games()
    # ---- the RTL contract the package depends on
    if K["ids"] != [1, 2, 3, 7]: E(f"RTL requestwrite size latches {K['ids']} != [1, 2, 3, 7]")
    if K["snd"] != 0x70000000 or K["hdr"].get(7) != (0x70000000, 4 << 20): E(f"RTL sound ROM window {K['snd']:#x} / header {K['hdr'].get(7)} != 0x70000000, 4 MiB")
    if K["addr"] != {1: 0x10000000, 2: 0x20000000, 3: 0x30000000}: E(f"RTL ROM windows {K['addr']}")
    if K["hdr"] != {1: (0x10000000, 2 << 20), 2: (0x20000000, 4 << 20), 3: (0x30000000, 16 << 20), 7: (0x70000000, 4 << 20)}: E(f"RTL header slot sizes {K['hdr']}")
    if (K["cfg"], K["eep_def"], K["eep_save"], K["eep_rd"], K["eep_mux"]) != (0x60000000, 0x40000000, 0x50000000, 0x50000000, 0x50000000):
        E(f"RTL windows cfg/default/save/read-back/mux {K['cfg']:#x}/{K['eep_def']:#x}/{K['eep_save']:#x}/{K['eep_rd']:#x}/{K['eep_mux']:#x}")
    if K["cfg_window"] < 64: E(f"RTL configuration loader window {K['cfg_window']} bytes < 64")
    if not K["cfg_gate"]: E("RTL: the CPU reset does not wait for a valid configuration (u1_ok)")
    if (K["dt_addr"] - 1) % 2 or K["dt_size"] != 128: E(f"RTL datatable entry {K['dt_addr']} / size {K['dt_size']} (expected an odd size entry, 128)")
    save_index = (K["dt_addr"] - 1) // 2
    # ---- R1 file set
    files = {str(p.relative_to(pkg)).replace(os.sep, "/") for p in pkg.rglob("*") if p.is_file()}
    expected = {f"Cores/{CORE_DIR}/{n}" for n in ("core.json", "data.json", "video.json", "input.json", "audio.json", "interact.json",
                                                   "variants.json", "info.txt", "icon.bin", "bitstream.rbf_r",
                                                   "LICENSE.txt", "THIRD_PARTY_NOTICES.txt")} | {
        f"Platforms/{PLATFORM}.json", f"Platforms/_images/{PLATFORM}.bin", f"{COMMON}/README.txt"} | {
        f"{COMMON}/{s}_u1cfg.bin" for s in G} | {f"{INST}/{s}.json" for s in G}
    for f in sorted(expected - files): E(f"R1 missing file: {f}")
    for f in sorted(files - expected): E(f"R1 unexpected file: {f}")
    for f in sorted(files):
        p = pkg / f; n = p.stat().st_size
        if f.endswith("bitstream.rbf_r"): continue
        if f.endswith("_u1cfg.bin"):
            if n != 64: E(f"R1 configuration file {f} is {n} bytes, not 64")
            continue
        if p.suffix.lower() in ROMLIKE_EXT and f not in expected: E(f"R1 ROM-like file extension: {f}")
        if n in ROMLIKE_SIZES: E(f"R1 ROM-like file size {n}: {f}")
        if n > (1 << 20): E(f"R1 file larger than 1 MiB (not the bitstream): {f}")
    cdir = pkg / "Cores" / CORE_DIR

    def J(rel):
        try:
            return json.loads((pkg / rel).read_text())
        except Exception as e:
            E(f"{rel}: unreadable or invalid JSON ({e})"); return None
    for n, key in (("core", "core"), ("data", "data"), ("video", "video"), ("input", "input"), ("audio", "audio"), ("interact", "interact"),
                   ("variants", "variants")):
        d = J(f"Cores/{CORE_DIR}/{n}.json")
        if d is not None and (key not in d or d[key].get("magic") != "APF_VER_1"): E(f"{n}.json: missing '{key}' or magic != APF_VER_1")
    # ---- R2 core.json
    d = J(f"Cores/{CORE_DIR}/core.json")
    if d and "core" in d:
        c = d["core"]; m = c.get("metadata", {})
        if m.get("platform_ids") != [PLATFORM]: E(f"R2 core.json platform_ids {m.get('platform_ids')} != ['{PLATFORM}']")
        if f"{m.get('author')}.{m.get('shortname')}" != CORE_DIR: E("R2 core.json author.shortname != core directory name")
        if m.get("version") != VERSION: E(f"R2 core.json version {m.get('version')} != {VERSION}")
        if not all(t in m.get("description", "") for t in GAME_TITLES): E("R2 core.json description does not name both supported games")
        for k in ("shortname", "author", "version"):
            if len(str(m.get(k, ""))) > 31: E(f"R2 core.json metadata.{k} longer than 31 characters")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,14}", PLATFORM): E(f"R2 platform id {PLATFORM!r}: Analogue allows a-z 0-9 _, at most 15, not starting with _")
        for k in ("url", "date_release"):
            if not m.get(k): E(f"R2 core.json metadata.{k} missing")
        if c.get("framework") != FRAMEWORK: E(f"R2 core.json framework {c.get('framework')} != {FRAMEWORK}")
        if c.get("cores") != [{"name": "default", "id": 0, "filename": "bitstream.rbf_r"}]: E(f"R2 core.json cores {c.get('cores')}: not ONE bitstream")
    # ---- R3 data.json
    d = J(f"Cores/{CORE_DIR}/data.json")
    slots = d["data"].get("data_slots", []) if d and "data" in d else []
    ids = [s.get("id") for s in slots]
    byid = {s.get("id"): s for s in slots}
    if ids != ORDER: E(f"R3 data.json slot order {ids} != {ORDER}")
    if len(slots) > 32: E("R3 more than 32 data slots")
    for s in slots:
        if len(str(s.get("name", ""))) > 15: E(f"R3 slot {s.get('id')} name longer than 15 characters")
        ex = s.get("extensions", [])
        if len(ex) > 4 or any(len(x) > 7 for x in ex): E(f"R3 slot {s.get('id')} extensions {ex}: at most 4, each at most 7 characters")
        pr = P(s)
        if pr is None: E(f"R3 slot {s.get('id')} parameters unreadable"); continue
        if pr & 0x001 and not pr & 0x100: E(f"R3 slot {s.get('id')} is reloadable in the core UI (bit 0) without the full-reload bit 0x100")
        if pr & 0x010 and s is not (slots[0] if slots else None): E(f"R3 slot {s.get('id')} sets the Instance JSON bit 4 but is not the first slot")
    if slots:
        g0 = slots[0]; p0 = P(g0)
        if p0 is None or not p0 & 0x010: E(f"R3 first slot {g0.get('id')} lacks the Instance JSON bit 4 (Analogue: only valid in the first slot)")
        if p0 is None or not p0 & 0x002: E(f"R3 first slot {g0.get('id')} lacks the core-specific bit 1 (Analogue: an instance slot must be core-specific)")
        if (g0.get("id"), g0.get("name"), p0, g0.get("extensions"), g0.get("required")) != (0, "Game", 0x113, ["json"], True):
            E(f"R3 first slot must be id 0 'Game', parameters 0x113, extensions ['json'], required: {g0}")
        if "filename" in g0 or "address" in g0: E("R3 the Game slot has a filename or an address (the instance file is read by APF, not loaded into the core)")
    if 10 in ids and ids.index(10) != save_index:
        E(f"R3 Save slot at index {ids.index(10)}: its datatable entry {2 * ids.index(10) + 1} != RTL datatable_addr {K['dt_addr']} (index {save_index})")
    for i, base, size in ((1, K["addr"].get(1), K["hdr"].get(1, (0, 0))[1]), (2, K["addr"].get(2), K["hdr"].get(2, (0, 0))[1]),
                          (3, K["addr"].get(3), K["hdr"].get(3, (0, 0))[1]), (4, K["eep_def"], K["dt_size"]), (10, K["eep_save"], K["dt_size"]),
                          (6, K["cfg"], 64), (7, K["snd"], K["hdr"].get(7, (0, 0))[1])):
        s = byid.get(i)
        if s is None: E(f"R3 slot id {i} missing"); continue
        if s.get("address") != f"0x{base:08X}": E(f"R3 slot {i} address {s.get('address')} != RTL 0x{base:08X}")
        if s.get("size_maximum") != f"0x{size:X}": E(f"R3 slot {i} size_maximum {s.get('size_maximum')} != RTL {size:#x}")
        if "filename" in s: E(f"R3 slot {i} has a default filename {s['filename']!r}: only the instance may name files (no file shared by both games)")
        pr = P(s) or 0
        if pr & 0x002: E(f"R3 slot {i} is core-specific (bit 1): the files live in {COMMON}")
        if pr & 0x001: E(f"R3 slot {i} is reloadable in the core UI (bit 0): only the Game slot is")
        if i == 6:
            if s.get("size_exact") != "0x40": E(f"R3 slot 6 size_exact {s.get('size_exact')!r} != '0x40' (a 61-63-byte file would pass the RTL's completeness check)")
        elif "size_exact" in s: E(f"R3 slot {i} sets size_exact (only the configuration slot needs it)")
    for i in (1, 2, 3, 6, 7):
        s = byid.get(i, {})
        if s.get("required") is not True: E(f"R3 slot {i} not required")
        if P(s) != 0x108 or s.get("extensions") != ["bin"]: E(f"R3 slot {i} must be parameters 0x108 (read-only, full reload) with extensions ['bin']: {s}")
        if s.get("nonvolatile"): E(f"R3 slot {i} nonvolatile")
    sd, sv = byid.get(4, {}), byid.get(10, {})
    if sd.get("required") is not False or P(sd) != 0x08 or "nonvolatile" in sd:
        E(f"R3 EEPROM default (id 4) must be optional, parameters 0x08 (read-only), not nonvolatile: {sd}")
    ps = P(sv)
    if sv.get("nonvolatile") is not True or sv.get("required") is not False or sv.get("extensions") != ["sav"]:
        E(f"R3 Save (id 10) must be nonvolatile, optional, extensions ['sav']: {sv}")
    if ps is None or not ps & 0x004: E(f"R3 Save parameters {sv.get('parameters')}: bit 2 (filename cloned from slot 0) missing: saves would not be per game")
    if ps is not None and ps & 0x020: E(f"R3 Save parameters {sv.get('parameters')} set bit 5 (initialise with 0xFF): a missing save would override the default")
    if ps != 0x84: E(f"R3 Save parameters {sv.get('parameters')} != 0x84 (SpidPlay1's and the released multi-game core's save slot)")
    # ---- R4 / R5 instance files
    readme = (pkg / COMMON / "README.txt").read_text() if (pkg / COMMON / "README.txt").is_file() else ""
    man = cfg_manifest(); used = {}
    declared = set(ids) - {0}
    for s, g in sorted(G.items()):
        rel = f"{INST}/{s}.json"; d = J(rel) if (pkg / rel).is_file() else None
        if d is None: E(f"R4 {s}: no instance file {rel}"); continue
        it = d.get("instance", {})
        if it.get("magic") != "APF_VER_1": E(f"R4 {s}: instance magic {it.get('magic')!r}")
        if it.get("data_path") != "": E(f"R4 {s}: data_path {it.get('data_path')!r} != '' (files are found as their data slots would find them)")
        if it.get("core_select") != {"id": 0, "select": False}: E(f"R4 {s}: core_select {it.get('core_select')} (one bitstream: id 0, select false)")
        if it.get("memory_writes"): E(f"R4 {s}: memory_writes present (the configuration comes from the id-6 file, as simulated)")
        extra = set(it) - {"magic", "data_path", "core_select", "data_slots"}
        if extra: E(f"R4 {s}: unexpected instance keys {sorted(extra)}")
        ds = it.get("data_slots", [])
        sid = [x.get("id") for x in ds]
        want = {1, 2, 3, 6, 7} | ({4} if s in EEPROM_DEFAULT_GAMES else set())
        if len(sid) != len(set(sid)): E(f"R4 {s}: a data slot id appears twice: {sid}")
        if set(sid) != want: E(f"R4 {s}: instance data slot ids {sorted(set(sid))} != {sorted(want)}")
        for x in ds:
            i, fn = x.get("id"), x.get("filename")
            if i in (0, 10): E(f"R4 {s}: instance names slot {i} (the Game slot / the save are never named by an instance)")
            if i not in declared: E(f"R4 {s}: slot {i} is not declared in data.json")
            if not isinstance(fn, str) or not fn or not fn.isascii() or "/" in fn or "\\" in fn or len(fn) > 255:
                E(f"R4 {s}: slot {i} filename {fn!r} must be a flat ASCII name"); continue
            if fn in used and used[fn] != s: E(f"R4 {s}: file {fn} is also named by {used[fn]} (a file shared by two games)")
            used.setdefault(fn, s)
            if i in (1, 2, 3, 7) and fn not in readme: E(f"R4 {s}: ROM file {fn} is not described in {COMMON}/README.txt")
        cfn = next((x.get("filename") for x in ds if x.get("id") == 6), None)
        if cfn:
            cp = pkg / COMMON / cfn
            if not cp.is_file(): E(f"R4 {s}: its configuration file {COMMON}/{cfn} is not in the package")
            else:
                b = cp.read_bytes(); made = u1_cfg.make(g)
                if b != made: E(f"R4 {s}: {cfn} is not u1_cfg.make({s}) (configuration identity)")
                if hashlib.sha256(b).hexdigest() != man.get(f"{s}.cfg"): E(f"R4 {s}: {cfn} sha256 != games/cfg_files.sha256 entry {s}.cfg")
                r, _f = u1_cfg.decode(b)
                if r != 0: E(f"R4 {s}: {cfn} decodes with reason {r} ({u1_cfg.REASONS.get(r)}): the CPU would stay in reset")
                if b[8:24] != bytes.fromhex(g["descriptor"]): E(f"R4 {s}: {cfn} carries descriptor {b[8:24].hex()} != the game's {g['descriptor']}")
    stems = [p.stem.lower() for p in (pkg / INST).glob("*.json")] if (pkg / INST).is_dir() else []
    if len(stems) != len(set(stems)): E(f"R5 instance file names not distinct case-insensitively: {sorted(stems)} (saves are named after them)")
    # ---- R6 video / input / platform / images / bitstream
    d = J(f"Cores/{CORE_DIR}/video.json")
    sm = d.get("video", {}).get("scaler_modes", []) if d else []
    if len(sm) != 1 or (sm[0].get("width"), sm[0].get("height"), sm[0].get("aspect_w"), sm[0].get("aspect_h"), sm[0].get("rotation"), sm[0].get("mirror")) != (K["w"], K["h"], 4, 3, 0, 0):
        E(f"R6 video.json scaler modes {sm} != one {K['w']}x{K['h']} 4:3 mode")
    d = J(f"Cores/{CORE_DIR}/input.json")
    try:
        cs = d["input"]["controllers"]; got = [(m["id"], m["key"]) for m in cs[0]["mappings"]] if len(cs) == 1 and cs[0].get("type") == "default" else None
    except Exception:
        got = None
    if got != WANT_MAP: E(f"R6 input.json mappings {got} != {WANT_MAP}")
    names = [m.get("name") for m in cs[0]["mappings"]] if got is not None else None
    if names != WANT_NAMES: E(f"R6 input.json labels {names} != {WANT_NAMES} (game-neutral)")
    pj = pkg / "Platforms" / f"{PLATFORM}.json"
    if pj.is_file():
        try:
            p = json.loads(pj.read_text())["platform"]
            for k in ("category", "name", "year", "manufacturer"):
                if k not in p: E(f"R6 platform json lacks {k}")
        except Exception as e:
            E(f"R6 platform json invalid: {e}")
    for f, n in ((f"Cores/{CORE_DIR}/icon.bin", ICON_BYTES), (f"Platforms/_images/{PLATFORM}.bin", PLAT_BYTES)):
        if (pkg / f).is_file():
            b = (pkg / f).read_bytes()
            if len(b) != n: E(f"R6 {f}: {len(b)} bytes, expected {n}")
            elif any(b[1::2]): E(f"R6 {f}: nonzero high bytes")
            elif len(set(b[0::2])) < 2: E(f"R6 {f}: blank image")
    # ---- R7 licence notices (the bitstream is a synthesized form of GPL, BSD-style (T80) and MIT (agg23) sources)
    lp, np_ = cdir / "LICENSE.txt", cdir / "THIRD_PARTY_NOTICES.txt"
    if lp.is_file() and hashlib.sha256(lp.read_bytes()).hexdigest() != GPL3_SHA256: E("R7 LICENSE.txt is not the complete GNU GPL v3 text")
    if lp.is_file() and (ROOT / "LICENSE").is_file() and lp.read_bytes() != (ROOT / "LICENSE").read_bytes(): E("R7 LICENSE.txt differs from the repository LICENSE")
    if np_.is_file():
        nt = np_.read_text()
        if SOURCE_URL not in nt: E(f"R7 THIRD_PARTY_NOTICES.txt does not name the source address {SOURCE_URL}")
        if "LICENSE.txt" not in nt: E("R7 THIRD_PARTY_NOTICES.txt does not refer to LICENSE.txt")
        try:
            req = notice_blocks()
        except (OSError, ValueError, StopIteration) as ex:
            E(f"R7 cannot read the notice sources (references/s32 checked out? --upstream): {ex}"); req = []
        for what, blk in req:
            if blk not in nt: E(f"R7 THIRD_PARTY_NOTICES.txt lacks the verbatim {what}")
    rp = cdir / "bitstream.rbf_r"
    if rp.is_file():
        r = rp.read_bytes()
        if not r: E("R6 bitstream.rbf_r is empty")
        if rbf is not None:
            src = Path(rbf).read_bytes()
            if reverse_bits(src) != r: E("R6 bitstream.rbf_r is NOT the bit-reversal of the given rbf")
            if r == src: E("R6 bitstream.rbf_r equals the un-reversed rbf")
    return errs


# ------------------------------------------------------------------ self test and negative controls
def selftest(work):
    work = Path(work)
    if work.exists():
        sys.exit(f"refusing: {work} exists (use a fresh directory)")
    work.mkdir(parents=True)
    fake = work / "FAKE_PLACEHOLDER_NOT_A_BITSTREAM.rbf"
    fake.write_bytes(b"FAKE PLACEHOLDER - NOT A BITSTREAM - for package validation only\n" + bytes(range(256)) * 16)
    r = subprocess.run([str(HERE / "package_rel.sh"), str(fake), str(work / "pkg")], capture_output=True, text=True)
    print(r.stdout[-1200:], r.stderr[-400:])
    if r.returncode:
        sys.exit("package_rel.sh failed")
    stage = work / "pkg" / "stage"
    e = validate(stage, fake)
    print("POSITIVE:", "PASS" if not e else f"FAIL {e}")
    ok = not e
    cd = f"Cores/{CORE_DIR}"

    def data(fn):
        def f(d):
            p = d / cd / "data.json"; j = json.loads(p.read_text()); a = j["data"]["data_slots"]
            fn({s["id"]: s for s in a}, a); p.write_text(json.dumps(j, indent=2))
        return f

    def inst(s, fn):
        def f(d):
            p = d / INST / f"{s}.json"; j = json.loads(p.read_text()); fn(j["instance"], {x["id"]: x for x in j["instance"]["data_slots"]})
            p.write_text(json.dumps(j, indent=2))
        return f

    def swap(i, k):
        return data(lambda b, a: a.__setitem__(slice(None), [a[k] if n == i else a[i] if n == k else x for n, x in enumerate(a)]))

    def cfgfile(s, fn):
        def f(d):
            p = d / COMMON / f"{s}_u1cfg.bin"; p.write_bytes(fn(p.read_bytes()))
        return f

    def put(rel, b):
        def f(d):
            (d / rel).parent.mkdir(parents=True, exist_ok=True); (d / rel).write_bytes(b)
        return f

    def move(a, b):
        def f(d):
            (d / b).parent.mkdir(parents=True, exist_ok=True); (d / a).rename(d / b)
        return f

    def flip(d):
        p = d / cd / "bitstream.rbf_r"; b = bytearray(p.read_bytes()); b[100] ^= 1; p.write_bytes(bytes(b))

    ga2cfg = u1_cfg.make(games()["ga2u"])
    cases = [
        ("game_slot_not_first", swap(0, 1), "Instance JSON bit 4 but is not the first slot"),
        ("game_slot_no_instance_bit", data(lambda b, a: b[0].update(parameters="0x103")), "lacks the Instance JSON bit 4"),
        ("game_slot_not_core_specific", data(lambda b, a: b[0].update(parameters="0x111")), "lacks the core-specific bit 1"),
        ("game_slot_reload_without_full_reload", data(lambda b, a: b[0].update(parameters="0x013")), "without the full-reload bit"),
        ("game_slot_extension", data(lambda b, a: b[0].update(extensions=["bin"])), "first slot must be"),
        ("instance_bit_on_a_rom_slot", data(lambda b, a: b[2].update(parameters="0x118")), "not the first slot"),
        ("save_moved_to_index_5", swap(4, 5), "datatable entry"),
        ("save_wrong_size", data(lambda b, a: b[10].update(size_maximum="0x100")), "slot 10 size_maximum"),
        ("save_init_ff_bit5", data(lambda b, a: b[10].update(parameters="0xA4")), "bit 5"),
        ("save_not_named_from_slot0", data(lambda b, a: b[10].update(parameters="0x80")), "bit 2"),
        ("save_not_nonvolatile", data(lambda b, a: b[10].pop("nonvolatile")), "Save (id 10) must be"),
        ("rom_slot_default_filename", data(lambda b, a: b[2].update(filename="tiles.bin")), "default filename"),
        ("rom_slot_core_specific", data(lambda b, a: b[1].update(parameters="0x10A")), "core-specific"),
        ("rom_slot_reloadable", data(lambda b, a: b[3].update(parameters="0x109")), "reloadable in the core UI (bit 0): only the Game slot"),
        ("rom_slot_wrong_address", data(lambda b, a: b[3].update(address="0x80000000")), "slot 3 address"),
        ("tile_slot_wrong_size", data(lambda b, a: b[2].update(size_maximum="0x200000")), "slot 2 size_maximum"),
        ("cfg_slot_wrong_address", data(lambda b, a: b[6].update(address="0x80000000")), "slot 6 address"),
        ("cfg_slot_wrong_size", data(lambda b, a: b[6].update(size_maximum="0x80")), "slot 6 size_maximum"),
        ("cfg_slot_optional", data(lambda b, a: b[6].update(required=False)), "slot 6 not required"),
        ("cfg_slot_no_size_exact", data(lambda b, a: b[6].pop("size_exact")), "slot 6 size_exact"),
        ("cfg_slot_size_exact_63", data(lambda b, a: b[6].update(size_exact="0x3F")), "slot 6 size_exact"),
        ("rom_slot_size_exact", data(lambda b, a: b[1].update(size_exact="0x200000")), "slot 1 sets size_exact"),
        ("eeprom_default_filename", data(lambda b, a: b[4].update(filename="spidman_eeprom.bin")), "default filename"),
        ("eeprom_default_nonvolatile", data(lambda b, a: b[4].update(nonvolatile=True)), "EEPROM default (id 4) must be"),
        ("extra_slot", data(lambda b, a: a.append({"name": "Extra", "id": 8, "required": False, "parameters": "0x08", "address": "0x80000000"})), "slot order"),
        ("sound_slot_rc1_window", data(lambda b, a: b[7].update(address="0x40000000")), "slot 7 address"),
        ("sound_slot_wrong_size", data(lambda b, a: b[7].update(size_maximum="0x200000")), "slot 7 size_maximum"),
        ("sound_slot_optional", data(lambda b, a: b[7].update(required=False)), "slot 7 not required"),
        ("sound_slot_core_specific", data(lambda b, a: b[7].update(parameters="0x10A")), "slot 7 is core-specific"),
        ("sound_slot_default_filename", data(lambda b, a: b[7].update(filename="sound.bin")), "slot 7 has a default filename"),
        ("sound_slot_before_save", data(lambda b, a: a.insert(4, a.pop(7))), "datatable entry"),
        ("ga2_without_sound", inst("ga2u", lambda i, b: i["data_slots"].remove(b[7])), "instance data slot ids"),
        ("spidman_uses_ga2_sound", inst("spidman", lambda i, b: b[7].update(filename="ga2u_sound.bin")), "also named by ga2u"),
        ("version_changed", lambda d: (d / cd / "core.json").write_text((d / cd / "core.json").read_text().replace('"0.1.0"', '"0.1.1"')), "version"),
        ("description_drops_a_game", lambda d: (d / cd / "core.json").write_text((d / cd / "core.json").read_text().replace("Spider-Man: The Video Game", "Spider-Man")), "both supported games"),
        ("spidman_uses_ga2_cfg", inst("spidman", lambda i, b: b[6].update(filename="ga2u_u1cfg.bin")), "also named by ga2u"),
        ("spidman_cfg_bytes_are_ga2s", cfgfile("spidman", lambda b: ga2cfg), "configuration identity"),
        ("ga2_uses_spidman_tiles", inst("ga2u", lambda i, b: b[2].update(filename="spidman_tiles.bin")), "spidman_tiles.bin is also named by ga2u"),
        ("cfg_check_word_broken", cfgfile("ga2u", lambda b: b[:9] + bytes([b[9] ^ 1]) + b[10:]), "reason 3"),
        ("cfg_truncated", cfgfile("ga2u", lambda b: b[:32]), "not 64"),
        ("cfg_missing", lambda d: (d / COMMON / "spidman_u1cfg.bin").unlink(), "not in the package"),
        ("instance_missing_tiles", inst("ga2u", lambda i, b: i["data_slots"].remove(b[2])), "instance data slot ids"),
        ("instance_names_save", inst("ga2u", lambda i, b: i["data_slots"].append({"id": 10, "filename": "ga2u.sav"})), "never named by an instance"),
        ("instance_names_undeclared_slot", inst("ga2u", lambda i, b: i["data_slots"].append({"id": 8, "filename": "x.bin"})), "not declared"),
        ("instance_data_path", inst("spidman", lambda i, b: i.update(data_path="spidman/")), "data_path"),
        ("instance_core_select", inst("ga2u", lambda i, b: i.update(core_select={"id": 1, "select": True})), "core_select"),
        ("instance_memory_writes", inst("ga2u", lambda i, b: i.update(memory_writes=[{"address": "0x60000000", "data": "0x0"}])), "memory_writes"),
        ("instance_subfolder_name", inst("ga2u", lambda i, b: b[1].update(filename="ga2u/ga2u_main.bin")), "flat ASCII"),
        ("instance_missing", lambda d: (d / INST / "spidman.json").unlink(), "no instance file"),
        ("instance_in_common", move(f"{INST}/ga2u.json", f"{COMMON}/ga2u.json"), "unexpected file"),
        ("instance_extra", put(f"{INST}/ga2.json", (stage / INST / "ga2u.json").read_bytes()), "unexpected file"),
        ("rom_included", put(f"{COMMON}/ga2u_main.bin", bytes(2 << 20)), "ROM-like"),
        ("readme_missing_rom_name", lambda d: (d / COMMON / "README.txt").write_text("no names\n"), "not described"),
        ("two_bitstreams", lambda d: (d / cd / "core.json").write_text((d / cd / "core.json").read_text().replace(
            '"filename": "bitstream.rbf_r"\n      }\n    ]', '"filename": "bitstream.rbf_r"\n      },\n      {"name": "b", "id": 1, "filename": "b.rbf_r"}\n    ]')), "ONE bitstream"),
        ("platform_mismatch", lambda d: (d / cd / "core.json").write_text((d / cd / "core.json").read_text().replace('"segas32"', '"ga2rc1"', 1)), "platform_ids"),
        ("input_mapping_changed", lambda d: (d / cd / "input.json").write_text((d / cd / "input.json").read_text().replace("pad_btn_x", "pad_btn_l")), "mappings"),
        ("input_label_game_specific", lambda d: (d / cd / "input.json").write_text((d / cd / "input.json").read_text().replace('"Button 3"', '"Button 3 (Magic)"')), "game-neutral"),
        ("rbf_r_flipped_bit", flip, "NOT the bit-reversal"),
        ("missing_icon", lambda d: (d / cd / "icon.bin").unlink(), "missing file"),
        ("license_missing", lambda d: (d / cd / "LICENSE.txt").unlink(), "missing file: Cores/mmnbass.System32/LICENSE.txt"),
        ("license_truncated", lambda d: (d / cd / "LICENSE.txt").write_bytes((d / cd / "LICENSE.txt").read_bytes()[:-200]), "complete GNU GPL v3"),
        ("notices_missing", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").unlink(), "missing file: Cores/mmnbass.System32/THIRD_PARTY_NOTICES.txt"),
        ("notices_t80_condition_dropped", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").write_text((d / cd / "THIRD_PARTY_NOTICES.txt").read_text().replace(
            "Redistributions in synthesized form must reproduce the above copyright\nnotice", "Redistributions in synthesized form may reproduce the above copyright\nnotice")), "verbatim T80"),
        ("notices_t80_disclaimer_dropped", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").write_text((d / cd / "THIRD_PARTY_NOTICES.txt").read_text().replace(
            "POSSIBILITY OF SUCH DAMAGE.", "")), "verbatim T80"),
        ("notices_sorgelig_dropped", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").write_text((d / cd / "THIRD_PARTY_NOTICES.txt").read_text().replace(
            "Copyright (c) 2018 Sorgelig", "")), "copyright line"),
        ("notices_t80_reg_year", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").write_text((d / cd / "THIRD_PARTY_NOTICES.txt").read_text().replace(
            "Copyright (c) 2002 Daniel Wallner", "Copyright (c) 2001-2002 Daniel Wallner")), "T80 T80_Reg.vhd"),
        ("notices_mit_dropped", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").write_text((d / cd / "THIRD_PARTY_NOTICES.txt").read_text().replace(
            "Copyright (c) 2022 Adam Gastineau", "")), "verbatim MIT"),
        ("notices_no_source_url", lambda d: (d / cd / "THIRD_PARTY_NOTICES.txt").write_text((d / cd / "THIRD_PARTY_NOTICES.txt").read_text().replace(
            SOURCE_URL, "the project page")), "source address"),
    ]
    for name, edit, want in cases:
        dst = work / f"neg_{name}"; shutil.copytree(stage, dst)
        try:
            edit(dst)
        except Exception as ex:                                   # noqa: BLE001
            print(f"NEGATIVE {name}: tamper did not apply ({ex!r})"); ok = False; continue
        e = validate(dst, fake)
        hit = any(want in x for x in e)
        print(f"NEGATIVE {name}: {'FAILS as required' if hit else 'NOT CAUGHT'}  ({next((x for x in e if want in x), e[0] if e else 'no error')[:150]})")
        ok &= hit
    print(f"SELFTEST: {'PASS' if ok else 'FAIL'} (positive + {len(cases)} negative controls)")
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--upstream" in a:
        UPSTREAM[0] = Path(a[a.index("--upstream") + 1]).expanduser().resolve(); os.environ["S32_UPSTREAM"] = str(UPSTREAM[0])
    elif os.environ.get("S32_UPSTREAM"):
        UPSTREAM[0] = Path(os.environ["S32_UPSTREAM"])
    if len(a) >= 2 and a[0] == "--selftest":
        sys.exit(selftest(a[1]))
    if not a or a[0].startswith("--"):
        sys.exit(__doc__)
    rbf = a[a.index("--rbf") + 1] if "--rbf" in a else None
    if "--upstream" in a: UPSTREAM[0] = Path(a[a.index("--upstream") + 1]).expanduser().resolve()
    errs = validate(a[0], rbf)
    for x in errs:
        print("FAIL:", x)
    print("VALIDATION", "FAILED" if errs else "PASSED", f"({len(errs)} problem(s))")
    sys.exit(1 if errs else 0)
