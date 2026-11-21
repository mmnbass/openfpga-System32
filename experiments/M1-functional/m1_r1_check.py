#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R1a checks: compare a run log with the INDEPENDENT expectations of m1_r1.py
(the manifest); nothing here reads a reference's output as truth.

Log lines understood (all benches and m1_ref.py print them):
    L n pc=.. op=..[ Rnn=..][ PSW=..]   lock-step record        (+lockstep / --lockstep)
    C n dr=.. dw=.. priv=..[ psw=..]    event counters           (+counters / --counters)
    CU n dr=.. dw=.. priv=.. psw=..     V2: counters of an instruction that started but never retired
                                        (the trapped / waiting one), printed before END
    Rnn=xxxxxxxx / PSW=xxxxxxxx         final registers
    W addr=.. size=.. data=..           data write
    END halted=h instrs=n ...
    TRAP <kind> ipc=<pc>                V2: trapped
    SIMCHECK errors=e ipc_errors=i      V2: microcode timing checks
    STALL ...                           S0 V2 bench: no retirement

Library use: check_ref(man, log) / check_v2(man, log, phase, ack=False) return
a list of failure strings (empty = pass). CLI:
    m1_r1_check.py ref  <manifest> <name> <log> [--label X]
    m1_r1_check.py v2   <manifest> <name> <log> --phase r1a|r1b [--ack]
"""
import json
import re
import sys


def _kv(tokens, fatal, what):
    """'k=v' tokens -> dict. A field that occurs twice (or is not numeric) is a FATAL parse error, never silently overwritten:
    dict(...) keeps only the LAST value, which let `C 0 dr=1 dr=0 ...` pass as dr=0 (Codex R3a r2)."""
    out = {}
    for x in tokens:
        k, _, v = x.partition("=")
        if not v.lstrip("-").isdigit():
            fatal.append(f"{what}: non-numeric field {x!r}")
            continue
        if k in out:
            fatal.append(f"{what}: duplicate field {k}")
        out[k] = int(v)
    return out


# R3a fixes 3 (Codex r3 should-fix 1): every record this parser recognises is parsed in FULL against the grammar the benches and
# references print (tb_m1_v2 / tb_m1_up / tb_s0_* / v2_sim_observer.sv / m1_ref.py): a recognised record that does not match,
# carries an extra or repeated field, or occurs twice where one is printed is a FATAL parse error, never ignored or overwritten
# (the old code kept the LAST SIMCHECK / TRAP, or only the matching prefix of a line, so `SIMCHECK errors=7 ipc_errors=2` followed
# by `SIMCHECK errors=0 ipc_errors=0` and a wrong TRAP followed by the expected one both passed).
L_RE = re.compile(r"^L (\d+) pc=[0-9a-f]{8} op=[0-9a-f]{2}((?: (?:R\d\d|PSW)=[0-9a-f]{8})*)$")
C_RE = re.compile(r"^(C|CU) (\d+) dr=\d+ dw=\d+ priv=\d+(?: psw=\d+(?: gpr=\d+)?)?$")      # the three printed key sets, in print order
W_RE = re.compile(r"^W addr=[0-9a-f]{8} size=[124] data=[0-9a-f]{8}$")
END_RE = re.compile(r"^END halted=[01] instrs=\d+(?: cycles=\d+)?(?: ce=\d+)?$")
TRAP_RE = re.compile(r"^TRAP ([a-z]+) ipc=([0-9a-f]{8})$")
SIMCHECK_RE = re.compile(r"^SIMCHECK errors=(\d+) ipc_errors=(\d+)$")
SIMVIOL_RE = re.compile(r"^SIMCHECK \d+ .+ \(upc=\d+ head=[0-9a-f]{8} avail=-?\d+\)$")    # v2_sim_checks.sv `fail`: one per violation (at most 10)
STALL_RE = re.compile(r"^STALL ipc=[0-9a-f]{8} upc=\d+ retired=\d+$")


# R3a fixes 4 (Codex r4 should-fix 1): MALFORMED RECORD BOUNDARIES are detected before the grammar above is applied. The dispatch below
# recognises a record by an exact column-zero keyword followed by a single space, so `TRAP\tmode ipc=00000099`, `END\thalted=0 instrs=0`,
# ` SIMCHECK errors=7 ipc_errors=2`, `ENDhalted=0 ...`, `end halted=0 ...` and `Trap mode ...` used to match nothing and vanish. Evidence for
# the rule (measured, 2026-11-01, on every archived evaluator log: 10,641 = 8,376 R3 + 993 R1 + 1,272 R2): every byte is printable ASCII
# or a newline, no line has leading or trailing whitespace, no record keyword occurs in another case, no line is blank. Rules:
#   1. a control character (anything below 0x20 but the newline, 0x7f, 0x85, U+2028, U+2029: the characters str.splitlines() would also
#      split on, so CR, CRLF, NUL, form feed, vertical tab, tab, ...) anywhere in the text is FATAL; so is a non-ASCII character outside a
#      Verilator banner line (`- ` in column zero: the banner carries the host's file system path, which may be non-ASCII);
#   2. a line that is not a banner line and whose first letter-initial text (leading whitespace, NUL, punctuation, digits skipped) is a
#      record keyword in ANY case, or a keyword glued to a following letter, but which is not exactly `<KEYWORD><space>...` in column
#      zero, is a malformed record boundary and FATAL. Lines that are not record-like (banners, the plain-text notes of the references,
#      blank lines) are still ignored exactly as before: this is NOT a closed grammar for the whole log (the S0 checker has that,
#      s0_compare.log_facts, because its input is only a fully known bench).
_WORDS = ("END", "TRAP", "SIMCHECK", "STALL", "GPREV", "COUNTERS", "REDIRECTS", "RETIRE", "MEMSTAT", "MEMDRAIN", "DRAIN")
_BAD_CTRL = re.compile("[\x00-\x09\x0b-\x1f\x7f\x85\u2028\u2029]")
# (loose shape that makes a line look like this record, exact column-zero shape the dispatch accepts)
_W_ = "|".join(_WORDS)
_BOUNDARY = [(re.compile(r"^(?:(?i:" + _W_ + r")(?![A-Za-z])|" + _W_ + r")"),       # a keyword in any case; or the exact keyword glued to a letter
              re.compile(r"^(?:" + _W_ + r")(?: |$)")),
             (re.compile(r"(?i)^(?:L|C|CU|G|P)\s*\d"), re.compile(r"^(?:L|C|CU|G|P) \d")),
             (re.compile(r"(?i)^PR\s*id"), re.compile(r"^PR id=")),
             (re.compile(r"(?i)^W\s*addr"), re.compile(r"^W addr=")),
             (re.compile(r"(?i)^(?:R\d\d|PSW)\s*[=:]"), re.compile(r"^(?:R\d\d|PSW)="))]


def boundary_problems(text):
    """-> list of FATAL messages: control / non-ASCII characters, and record-like lines whose boundary is not the exact column-zero keyword"""
    out = []
    m = _BAD_CTRL.search(text)
    if m:
        out.append(f"control character {m.group(0)!r} in the log (CR, NUL, tab, form feed, ... are never printed by a bench)")
    for line in text.splitlines():
        if line.startswith("- "):
            continue
        if re.search(r"[^\x20-\x7e]", line) and not _BAD_CTRL.search(line):
            out.append(f"non-ASCII character in {line[:50]!r}")
            continue
        cand = re.sub(r"^[^A-Za-z]*", "", line)
        for loose, exact in _BOUNDARY:
            if (loose.match(cand) or loose.match(line)) and not exact.match(line):
                out.append(f"malformed record boundary {line[:60]!r} (a record keyword with leading whitespace, a tab, another case or glued text)")
                break
    return out


# R3a fixes 5 (owner: "reject unrecognized, malformed or contradictory records"): the CLOSED grammar of the shared evaluator. Every line of a
# log must be one of the shapes below or it is FATAL. Enumerated from the emitters (tb_m1_v2.sv, tb_m1_up.sv, tb_s0_v2.sv, tb_s0_up.sv,
# v2_sim_observer.sv, v2_sim_checks.sv, m1_mem.sv / s0_mem.sv, m1_ref.py) and measured on all 10,641 archived logs (8,376 R3, 993 R1,
# 1,272 R2): 0 lines outside it. Not accepted although some emitters can print them (opt-in or failure output, absent from every
# archive): `T` (+trace), `RETIRE` (+retire), `IPC instruction ...`, `M ...` lines, DRAIN / MEMDRAIN, `FATAL:` / `no +img=` / `REFERR`
# (each of which fails a run), `%Error` / `%Warning`, blank lines.
_U = r"(?:0|[1-9]\d*)"
G_RE = re.compile(r"^G (\d+) id=(\d+)(?: be=[0-9a-f])? val=[0-9a-f]{8}$")
P_RE = re.compile(r"^P (\d+) id=(\d+)(?: be=[0-9a-f])? val=[0-9a-f]{8}$")
PR_RE = re.compile(r"^PR id=(\d+) val=[0-9a-f]{8}$")
COUNTERS_RE = re.compile(r"^COUNTERS pre=" + _U + r" exec=" + _U + r" mem=" + _U + r" fetch=" + _U + r" iter=" + _U + r"$")
REDIRECTS_RE = re.compile(r"^REDIRECTS reset=" + _U + r" instr=" + _U + r" instr_outstanding=" + _U + r" \((?: {8}wide port|data-bus prefetch)\)$")
_REGIONS = ("rom", "rom-mirror", "wram", "vram", "spriteram", "sprite-ctl", "palette/mixer", "shared-sound", "comm", "dual", "unmapped-8",
            "protection", "io", "random", "interrupt-ctl", "unmapped-9", "unmapped-b", "unmapped-e")      # s0_mem.sv region()
MEMSTAT_RE = re.compile(r"^MEMSTAT (read|write) +(\S+) +(" + _U + r")$")
MEMSTAT_ROM_RE = re.compile(r"^MEMSTAT rom_writes_ignored " + _U + r"$")
_TU = r"(?:ps|ns|us|ms|s)"
# R3a Codex-r5 fixes (should-fix 2): re.ASCII, so \d means 0-9 only (`\u0660.\u0661` used to pass), and U+FFFD (what an undecodable byte becomes,
# decode_log below) is not acceptable in the host path; the path is the one place any other printable non-ASCII character is accepted.
BANNER_RES = (re.compile(r"^- (?:[^\x00-\x1f\x7f-\x9f\u2028\u2029\ufffd]*/)?tb_[a-z0-9_]+\.sv:\d+: Verilog \$finish$", re.ASCII),
              re.compile(r"^- S i m u l a t i o n   R e p o r t: Verilator \d+\.\d+(?: \d{4}-\d{2}-\d{2})?$", re.ASCII),
              re.compile(r"^- Verilator: \$finish at \d+(?:\.\d+)?" + _TU + r"; walltime \d+(?:\.\d+)? s; speed \d+(?:\.\d+)? " + _TU + r"/s$", re.ASCII),
              re.compile(r"^- Verilator: cpu \d+(?:\.\d+)? s on \d+ threads; allocated \d+ MB$", re.ASCII))


def decode_log(raw):
    """R3a Codex-r5 fixes (should-fix 2 and 3): bytes -> str for every evaluator input, with NO newline translation (a CR must reach
    boundary_problems as a CR; subprocess.run(text=True) and Path.read_text() turn CRLF into LF before any check) and with an undecodable
    byte becoming U+FFFD, which the character rules reject (non-ASCII outside a banner; excluded from the banner path)."""
    return raw.decode("utf-8", errors="replace")


def read_log(path):
    with open(path, "rb") as f:
        return decode_log(f.read())


def closed_problem(line):
    """None if the line is a well-formed record of the kinds parse() leaves to parse2 / parse3 (G, P, PR, GPREV) or has no other reader
    (COUNTERS, REDIRECTS, MEMSTAT, the Verilator banner); else why not"""
    if line == "GPREV on" or G_RE.match(line) or P_RE.match(line) or COUNTERS_RE.match(line) or REDIRECTS_RE.match(line) \
            or MEMSTAT_ROM_RE.match(line) or any(b.match(line) for b in BANNER_RES):
        return None
    m = PR_RE.match(line)
    if m and int(m.group(1)) <= 31:
        return None
    m = MEMSTAT_RE.match(line)
    if m and m.group(2) in _REGIONS and line in (f"MEMSTAT {m.group(1)} {m.group(2)} {m.group(3)}",
                                                  f"MEMSTAT {m.group(1)}{' ' if m.group(1) == 'write' else '  '}{m.group(2):<14} {m.group(3)}"):
        return None
    first = line.split(None, 1)[0] if line.strip() else ""
    if first in ("GPREV", "G", "P", "PR", "COUNTERS", "REDIRECTS", "MEMSTAT"):
        return f"malformed {first} record {line[:60]!r}"
    return f"unrecognised line {line[:60]!r}" if line.strip() else "blank line"


def once_key(line):
    """the key under which a well-formed COUNTERS / REDIRECTS / MEMSTAT line may occur once per log (None for any other record)"""
    if line.startswith(("COUNTERS ", "REDIRECTS ")):
        return line.split(None, 1)[0]
    m = MEMSTAT_RE.match(line)
    if m:
        return f"MEMSTAT {m.group(1)} {m.group(2)}"
    return "MEMSTAT rom_writes_ignored" if MEMSTAT_ROM_RE.match(line) else None


def parse(text, closed=True):
    """closed=True (the default; every user of the evaluator): any line that is not a well-formed record of the grammar is FATAL"""
    d = {"L": [], "C": [], "CU": [], "R": {}, "PSW": None, "W": [], "END": None, "TRAP": None, "SIMCHECK": None,
         "STALL": False, "FATAL": []}
    fatal = d["FATAL"]
    fatal.extend(boundary_problems(text))
    once = set()          # R3a Codex-r5 fixes (should-fix 1): a COUNTERS / REDIRECTS / MEMSTAT record is printed at most once per key
    for line in text.splitlines():
        if line.startswith("CU ") or line.startswith("C "):
            kind = "CU" if line.startswith("CU ") else "C"
            t = line.split()
            if len(t) < 2 or not t[1].isdigit():
                fatal.append(f"malformed {kind} record {line[:60]!r}")
                continue
            c = _kv(t[2:], fatal, f"{kind} {t[1]}") | {"n": int(t[1])}
            mc_ = C_RE.match(line)
            if not mc_ or mc_.group(1) != kind:
                fatal.append(f"malformed {kind} record {line[:60]!r} (expected {kind} n dr= dw= priv= [psw= [gpr=]], each once, in that order)")
            if kind == "CU":
                if d["CU"] and c["n"] <= d["CU"][-1]["n"]:
                    fatal.append(f"CU {c['n']} out of order or duplicated (after CU {d['CU'][-1]['n']})")
            elif c["n"] != len(d["C"]):
                fatal.append(f"counter record C {c['n']} out of order or duplicated (position {len(d['C'])}): retirement must be strictly 0, 1, 2, ...")
            d[kind].append(c)
        elif line.startswith("L ") or line == "L":
            m_ = re.match(r"L (\d+) ", line)
            if not L_RE.match(line):
                fatal.append(f"malformed lock-step record {line[:60]!r}")
            else:
                keys = re.findall(r"(R\d\d|PSW)=", line)
                if len(keys) != len(set(keys)):
                    fatal.append(f"lock-step record {m_.group(1)} has a duplicate register/PSW field")
                if any(k != "PSW" and int(k[1:]) > 31 for k in keys):
                    fatal.append(f"lock-step record {m_.group(1)} names a register above R31")
            if m_ and int(m_.group(1)) != len(d["L"]):
                fatal.append(f"lock-step record L {m_.group(1)} out of order (position {len(d['L'])}): retirement must be strictly 0, 1, 2, ...")
            if line.startswith("L "):
                d["L"].append(line.split(None, 1)[1].strip())
        elif re.match(r"^R\d\d=", line):
            if not re.match(r"^R\d\d=[0-9a-f]{8}$", line) or int(line[1:3]) > 31:
                fatal.append(f"malformed final dump line {line[:40]!r}")
            else:
                if int(line[1:3]) in d["R"]:
                    fatal.append(f"duplicate final dump line {line[:3]}")
                d["R"][int(line[1:3])] = int(line[4:], 16)
        elif line.startswith("PSW="):
            if not re.match(r"^PSW=[0-9a-f]{8}$", line):
                fatal.append(f"malformed final PSW line {line[:40]!r}")
            else:
                if d["PSW"] is not None:
                    fatal.append("duplicate final PSW line")
                d["PSW"] = int(line[4:], 16)
        elif line.startswith("W ") or line == "W":
            if not W_RE.match(line):
                fatal.append(f"malformed data write record {line[:60]!r}")
            d["W"].append(line)
        elif line.startswith("END") and line[3:4] in ("", " "):
            if d["END"] is not None:
                fatal.append("duplicate END record")
            kv = _kv(line.split()[1:], fatal, "END record")
            if not END_RE.match(line):
                fatal.append(f"malformed END record {line[:60]!r}")
            if d["END"] is None:
                d["END"] = {k: str(v) for k, v in kv.items()}
        elif line.startswith("TRAP") and line[4:5] in ("", " "):
            m = TRAP_RE.match(line)
            if not m:
                fatal.append(f"malformed TRAP record {line[:60]!r}")
                if d["TRAP"] is None:
                    d["TRAP"] = ("?", -1)        # a trap record WAS printed: the run is not a halt, whatever else is wrong with the line
            elif d["TRAP"] is not None:
                fatal.append(f"duplicate TRAP record {line[:60]!r}")
            else:
                d["TRAP"] = (m.group(1), int(m.group(2), 16))
        elif line.startswith("SIMCHECK"):
            m = SIMCHECK_RE.match(line)
            if m:
                if d["SIMCHECK"] is not None:
                    fatal.append(f"duplicate SIMCHECK summary {line[:60]!r}")
                else:
                    d["SIMCHECK"] = (int(m.group(1)), int(m.group(2)))
            elif SIMVIOL_RE.match(line):
                fatal.append(f"simulation-check violation record {line[:80]!r}")
            else:
                fatal.append(f"malformed SIMCHECK record {line[:60]!r}")
        elif line.startswith("STALL") and line[5:6] in ("", " "):
            d["STALL"] = True
            if not STALL_RE.match(line):
                fatal.append(f"malformed STALL record {line[:60]!r}")
        elif "FATAL" in line or "no +" in line:
            fatal.append(line)
        elif closed:
            why = closed_problem(line)
            if why:
                fatal.append(why)
            else:
                key = once_key(line)
                if key is not None:
                    if key in once:
                        fatal.append(f"duplicate {key} record {line[:60]!r} (printed once per run; a second one may contradict the first)")
                    once.add(key)
    return d


def event_order_errors(text, tag):
    """R3a fixes 3 (Codex r3 note: reversing the G records across instructions still passed). The per-instruction events `P n ...`
    (privileged writes) and `G n ...` (GPR writes) are printed in program order, and event n is printed after the n lock-step
    records L 0..L n-1 and before L n (so also after C n-1; for an instruction that never retired, after all the L records):
    measured on every archived V2 / extended-reference / upstream log (33,066 G and 752 P events). Checked here: instruction
    numbers never decrease, and, in a log that has lock-step records (else counter records), event n sits after exactly n of them.
    Returns a list of problems (the first of each kind only)."""
    ev, lseen, cseen = [], 0, 0
    for line in text.splitlines():
        if line.startswith("L "):
            lseen += 1
        elif line.startswith("C "):
            cseen += 1
        elif line.startswith(tag + " "):
            m = re.match(tag + r" (\d+) ", line)
            if m:
                ev.append((int(m.group(1)), lseen, cseen))
    errs, last = [], -1
    for n, ls, cs in ev:
        if n < last and not any("out of order" in e for e in errs):
            errs.append(f"{tag} event of instruction {n} printed after one of instruction {last} (events out of order)")
        last = max(last, n)
        pos = ls if lseen else (cs if cseen else None)
        if pos is not None and pos != n and not any("misplaced" in e for e in errs):
            errs.append(f"{tag} event of instruction {n} misplaced: printed after {pos} {'lock-step' if lseen else 'counter'} record(s), expected {n}")
    return errs


def regs_of(d):
    return [d["R"].get(i) for i in range(32)]


def _counters(man, d, n, fails, v2=False, ref=False):
    """Compare the C records of the first n instructions with the expected counters.
    ref=True: the Python references (v60_ref reads the destination of every F12 op)."""
    exp = dict(man.get("exp_counters", {}))
    if ref:
        exp.update(man.get("ref_counters", {}))
    if len(d["C"]) < n:
        fails.append(f"counters: {len(d['C'])} C records, expected at least {n}")
        return
    for i in range(n):
        c = d["C"][i]
        e = exp.get(str(i), {"dr": 0, "dw": 0, "priv": 0})
        for k in ("dr", "dw", "priv"):
            if c.get(k) != e[k]:
                fails.append(f"counters: instruction {i} {k}={c.get(k)} expected {e[k]}")
        if v2 and man.get("jmp_idx") is not None and i == man["jmp_idx"] and c.get("psw", 0) != 0:
            fails.append(f"counters: the JMP (instruction {i}) updated the PSW {c['psw']} time(s)")
    if len(d["C"]) > n:
        fails.append(f"counters: {len(d['C'])} C records, expected {n}")


def check_ref(man, text, ref=False):
    """A reference (ref=True: the Python ones) or upstream run of a positive program: everything
    must match the expectations."""
    d = parse(text)
    f = list(d["FATAL"])
    if "REFERR" in text:
        f.append("the run raised: " + text[text.index("REFERR"):].splitlines()[0][:100])
    exp = man["exp_L"]
    n = man["n_instrs"]
    if d["L"] != exp:
        i = next((k for k in range(max(len(d["L"]), len(exp)))
                  if (d["L"][k] if k < len(d["L"]) else None) != (exp[k] if k < len(exp) else None)), 0)
        f.append(f"lock-step: record {i}: got {d['L'][i] if i < len(d['L']) else '(missing)'}"
                 f" expected {exp[i] if i < len(exp) else '(missing)'}")
    if not d["END"] or d["END"].get("halted") != "1" or int(d["END"].get("instrs", -1)) != n:
        f.append(f"END {d['END']} expected halted=1 instrs={n}")
    if regs_of(d) != man["final_regs"]:
        f.append("final registers differ from the expectation")
    if d["PSW"] != man["final_psw"]:
        f.append(f"final PSW {d['PSW']} expected {man['final_psw']:08x}")
    nw = sum(v["dw"] for v in man["exp_counters"].values())
    if len(d["W"]) != nw:
        f.append(f"data writes {len(d['W'])} expected {nw}")
    _counters(man, d, n, f, ref=ref)
    return f


def _side_effects(man, d, n, fails, need_unretired):
    """Every V2 exit kind: the unretired (trapped / waiting) instruction's counters must be zero and
    exist (CU record); the data writes (W records) must be exactly the expected ones of the first n
    instructions."""
    exp = man.get("exp_counters", {})
    want_w = sum(exp.get(str(i), {"dw": 0})["dw"] for i in range(n))
    if len(d["W"]) != want_w:
        fails.append(f"data writes: {len(d['W'])} W records, expected {want_w}")
    if need_unretired and not d["CU"]:
        fails.append("counters: no CU record for the unretired instruction (run without +counters?)")
    for c in d["CU"]:
        for k in ("dr", "dw", "priv"):
            if c.get(k, 0) != 0:
                fails.append(f"counters: unretired instruction {c['n']} {k}={c[k]} (expected 0)")


def check_v2(man, text, phase, ack=False):
    d = parse(text)
    f = list(d["FATAL"])
    ex = (man.get("v2_ack") if ack else man.get("v2"))
    if ex is None:
        return ["no V2 expectation for this program"]
    ex = ex[phase]
    if d["SIMCHECK"] != (0, 0):
        f.append(f"SIMCHECK {d['SIMCHECK']}")
    kind = ex["kind"]
    exp = man["exp_L"]
    if kind == "halt":
        if d["TRAP"]:
            f.append(f"trapped {d['TRAP'][0]} at {d['TRAP'][1]:08x}, expected to halt")
        if d["STALL"]:
            f.append("stalled")
        if d["L"] != exp:
            f.append("lock-step records differ from the expectation")
        if not d["END"] or d["END"].get("halted") != "1" or int(d["END"].get("instrs", -1)) != man["n_instrs"]:
            f.append(f"END {d['END']} expected halted=1 instrs={man['n_instrs']}")
        if regs_of(d) != man["final_regs"] or d["PSW"] != man["final_psw"]:
            f.append("final state differs from the expectation")
        _counters(man, d, man["n_instrs"], f, v2=True)
        _side_effects(man, d, man["n_instrs"], f, False)
        return f
    if kind == "trap":
        n = ex["n"]
        want = (ex["trap"], ex["pc"])
        if d["TRAP"] != want:
            f.append(f"TRAP {d['TRAP'] and (d['TRAP'][0], '%08x' % d['TRAP'][1])} expected {(want[0], '%08x' % want[1])}")
        if d["L"] != exp[:n]:
            f.append(f"lock-step: {len(d['L'])} records, first {n} expected to equal the independent prefix")
        if not d["END"] or d["END"].get("halted") != "0":
            f.append("END halted != 0 on a trap")
        # state after the n retired instructions: the independent replay
        regs, psw = _replay(exp[:n])
        if regs_of(d) != regs or d["PSW"] != psw:
            f.append(f"state at the trap differs from the independent state after {n} instructions")
        _counters(man, d, n, f, v2=True)
        _side_effects(man, d, n, f, True)
        return f
    if kind == "wait":
        r, sn = ex["retired"], ex["state_n"]
        if d["TRAP"]:
            f.append(f"trapped {d['TRAP'][0]}, expected to wait")
        if not d["END"] or d["END"].get("halted") != "0" or int(d["END"].get("instrs", -1)) != r:
            f.append(f"END {d['END']} expected halted=0 instrs={r}")
        if d["L"] != exp[:r]:
            f.append(f"lock-step: expected the first {r} independent records, got {len(d['L'])}")
        regs, psw = _replay(exp[:sn])
        if regs_of(d) != regs or d["PSW"] != psw:
            f.append(f"state differs from the independent state after {sn} instructions")
        _counters(man, d, r, f, v2=True)
        _side_effects(man, d, r, f, True)
        return f
    return [f"unknown kind {kind}"]


def _replay(lines):
    regs, psw = [0] * 32, 0x10000000
    for line in lines:
        for tok in line.split()[3:]:
            k, v = tok.split("=")
            if k == "PSW":
                psw = int(v, 16)
            elif k.startswith("R"):
                regs[int(k[1:])] = int(v, 16)
    return regs, psw


def main():
    kind, mf, name, log = sys.argv[1:5]
    man = json.load(open(mf))[name]
    text = read_log(log)          # R3a fixes 4/5: bytes, no newline translation (a CRLF log must reach the parser as CRLF), bad UTF-8 -> U+FFFD
    if kind == "ref":
        fails = check_ref(man, text, ref="--up" not in sys.argv)
    else:
        ph = sys.argv[sys.argv.index("--phase") + 1]
        fails = check_v2(man, text, ph, "--ack" in sys.argv)
    if fails:
        print(f"FAIL {name}: " + "; ".join(fails))
        return 1
    print(f"ok {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
