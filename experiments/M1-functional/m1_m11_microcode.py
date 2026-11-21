#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M1.1 microcode and decode tables for V2: the V60 F1/F2 front end, MOV.W
with register, #quick and #imm32 operands, BR8, NOP and HALT (loaded through
V2's control-store / table write ports).

Front end (docs/v2-m1.1-plan.md §3):
  disp     poll decode-ready (ucond 27) with IMM_OFS 1, i.e. the opcode and
           the format byte were queued and settled in the previous cycle;
           dispatch (NXT 5, IMM_OFS 1). The dispatch latches the fields, the
           format byte (ifl) and the instruction-start PC (ipc).
  operand  poll decode-ready with IMM_OFS 0 and the operand's MODM_SEL;
           CALL a one-word stub that mode-dispatches (NXT 6) with the same
           fields; the mode routine of every supported mode is one RETURN
           word, which leaves the latched descriptor (ucond 14 register
           direct, 21 immediate, 26 quick) and the latched register (mreg)
           for the caller. Operand 2 always starts at window offset 0,
           because operand 1's specifier is consumed first.
  MOV.W    CONSUME 2 (opcode, format byte); F1 / F2 and D by ucond 24 / 25.
           F2 D=1: source specifier (modm ifl[6]) -> R[ifl[4:0]].
           F2 D=0: R[ifl[4:0]] -> destination specifier (modm ifl[6]).
           F1: source (modm ifl[6]) -> T0; destination (modm ifl[5]) <- T0.
  BR8      ipc + sign-extended byte 1 -> REDIRECT (M1.1e).
  ALU      (M1.2) ADD.W 0x84, SUB.W 0xAC, CMP.W 0xBC, AND.W 0xA4, in the same
           forms as MOV.W: operand 1 is the source a (A bus), operand 2 the
           destination b, read (B bus), operated on (b op a), written back
           (not CMP), flags by the operation's FLAG_UPD mask (ADD/SUB/CMP: Z S
           OV CY; AND: Z S OV, CY unchanged). One execute word per form; a
           #quick value is masked in the word before and bypassed (FWD_EN).
           CMP also takes an immediate operand 2 (both references agree it
           is a value, nothing is written); ADD/SUB/AND to an immediate trap
           immdest (the references disagree).
  GETPSW   (M1.2) 0xF7, modm 1 (opcode bit 0, MODM_SEL 3): the PSW -> R[n].
Every lookup word repeats the fields of the lookup it dispatches on (a
stalled word re-reads the tables with its own fields); v2_sim_checks
enforces this and the other timing rules.

Traps (self-loops; the bench reports which and where):
  trap          an opcode outside the subset (V2-unsupported)
  trap_mode     an addressing mode outside the subset, including the modm 1
                groups 6/7 (the references disagree there)
  trap_immdest  MOV.W to an immediate destination: the references disagree
                (upstream RTL stores to the immediate taken as an address,
                v60_ref discards); a decision is pending

TEST-ONLY variant (--smc-test): adds one memory destination, register
indirect [Rn] (modm 0, 0x60|r) for F2 D=0 only (MOV.W Rs, [Rn]: a word store
through MAR/MDR), so that self-modifying-code programs can store into
queued instruction bytes (review C2-1). It is not M1.3: no other memory form,
and [Rn] as a source is not supported. The normal M1.1 microcode traps it.

--smc-merged (with --smc-test): the store's bus word also jumps to the decode
poll (BUS_OP and NXT in one word), the tightest schedule (review C2 round 2).

Usage: m1_m11_microcode.py <out-dir> [--smc-test [--smc-merged]]
Writes m1_cstore.hex, m1_dispatch.hex, m1_mode.hex, m1_symbols.txt.
"""
# release/s32-v1 microcode union (Michael 2026-11-19, decision 2: "the conservative univ U SP3 U SP4 microcode union, not P1.2"):
#   three-way merge of the universal builder (univ/u1b f71ca46, pin univ) and the Spider-Man builder (sm/spidaudio1-4p-divu 53c909c =
#   SpidPlay3 d28cfc4 + SP4 1755e85 / 53c909c), base sm/spidman 6a4026a. The five univ x SP3 conflicts are resolved exactly as P1.0
#   (docs/p1-unified-microcode.md section 1: R1 / R3 / R4 / R5 / R6 the universal routine, R2 SP3's sp3_dvs_d0; both routines of each
#   pair stay in the image, SP3's REMU routine renamed g_remub_sp). Without SP4 this source rebuilds P1.0's merged image byte for byte
#   (1,723 words, cstore 611e11e3...). SP4 adds two conflicts: R7 MUL.W F2 D = 1 keeps GA2Play3's mul_d1 (SP4-M's sp4_mul_d1 stays
#   in the image, unreached; both equal MAME with identical cycles on every vector); SP4-R REMU.W (dispatch 0x55) uses SP3's REMU
#   routine (g_remub_sp), the routine SP4 validated at word size. DIVU.W F2 D = 1 (SP4 sp4_divu_d1) merged without conflict.
#   Evidence (stored MAME results, no MAME run): docs/release-s32-v1.md.

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "V2-datapath-floor" / "sim"))
from microasm import word, A, ALU, RES, CW_W, write_hex, cond  # noqa: E402

WR = {"GPR": 1, "TEMP": 2, "PRIV": 3, "PSW": 4, "MAR": 6, "MDR": 7}
C_REGDIR, C_IMM, C_QUICK, C_F1, C_D, C_READY = 14, 21, 26, 24, 25, 27
C_TAG = 23                               # R4b (S-D): ea_desc[11], the TEMPORARY PC-relative tag (docs/r4-movea-pcrel-plan.md 5.5); RTL name "deferred"
MS_ZERO, MS_B6, MS_B5, MS_OP0 = 0, 1, 2, 3   # MODM_SEL
# M1.2 ALU operations: name -> (opcode, ALU op, writes operand 2, FLAG_UPD {cy, ov, s, z})
ALU_OPS = {"addw": (0x84, ALU["ADD"], True, 0b1111), "subw": (0xAC, ALU["SUB"], True, 0b1111),
           "cmpw": (0xBC, ALU["SUB"], False, 0b1111), "andw": (0xA4, ALU["AND"], True, 0b0111)}
T0 = 0                                   # temp: an operand value in flight
C_PRIV, C_ZERO = 16, 6                   # R2b: privileged instruction at EL != 0; result (res_q) is zero
# R2b regID predicate: res = (q + LDPR_ADD) AND LDPR_MASK is zero exactly for the register IDs 2..5
LDPR_ADD, LDPR_MASK = 30, 28
LDPR_IDS = (2, 3, 4, 5)                  # L1SP, L2SP, L3SP, SBR (plan section 3a)


_NO_CHECKS = False
_NO_COMPACT = False                                      # tools that need the uncompacted layout set this (b3_reach.py)
# MC-compact (experiments/MC-compact): exactly cycle-equivalent packing after compact(), OFF by default (images unchanged).
# Selected by env GA2_MC_PACK=<mode> (so every tool that imports build() inherits it) or main()'s --mc-pack=<mode>; see mc_pack.py.
import os as _os
_PACK = _os.environ.get("GA2_MC_PACK", "")


def check_tables(sym, dtab, mtab, smc_test):
    """Builder self-assertions (plan sections 10 #1, 10 #1 dependency and 11 #3, 11 note 5)."""
    if _NO_CHECKS:                                      # TEST-ONLY mutant runs that bypass the assertions (m1_r3b_mutants.py)
        return
    trap, tmode = sym["trap"], sym["trap_mode"]
    if smc_test:
        # variant isolation, keyed on smc_test alone: no JMP, and /abs is not a mode (MOV.W R0,/abs
        # must not become a store through [Rn])
        assert dtab[0xD6] == trap and dtab[0xD7] == trap, "smc_test: D6/D7 must stay trap"
        assert mtab[0x0F3] == tmode, "smc_test: mtab[0x0F3] must stay trap_mode"
        assert mtab[0x0F2] == tmode, "smc_test: mtab[0x0F2] must stay trap_mode (R4b variant isolation)"
        assert "jmp" not in sym, "smc_test: no JMP routine"
        # R3b: MOVEA is not dispatched in --smc-test builds ([Rn] is a descriptor that is neither register nor
        # immediate there: movea.b [Rn], R would read it as /abs and load a bogus address)
        assert dtab[0x40] == trap, "smc_test: dtab[0x40] must stay trap (R3b variant isolation)"
        return
    assert dtab[0xD6] == sym["jmp"] and dtab[0xD7] == sym["jmp"], "production: D6 / D7 = jmp (B3: modm 1 added)"
    assert mtab[0x0F3] == (0x14C << 12) | sym["mret"], "production: mtab[0x0F3] (both fields)"
    assert mtab[0x0F2] == (0x88C << 12) | sym["mret"], "production: mtab[0x0F2] (R4b disp32[PC]: base 2, disp32, not register / immediate, tag bit 11)"
    # R3b: MOVEA's "neither register nor immediate" test identifies /abs only while /abs is the only memory descriptor
    assert (dtab[0x40] & 0xFFF) == sym["movea"], "production: dtab[0x40] = movea"
    # uniqueness: among supported (non-trapping) entries, 0x0F3 is the only one that is neither
    # register direct (desc bit 0) nor immediate (bit 1)
    odd = [i for i, v in mtab.items() if (v & 0xFFF) not in (trap, tmode)
           and not (((v >> 12) & 1) or ((v >> 12) & 2))]
    # R4b: the memory descriptors are /abs (0x14C) and disp32[PC] (0x88C); B1 adds modm 1 [Rn+] (0x210) and [-Rn] (0x410) for every register
    # (bit 4 = the TEMPORARY register-based-memory-mode tag, read only by ucond 22)
    ipc_ = [0x0F8, 0x0F9] if "mpi" in sym else []          # SP3-B: [d8 / d16[PC]] (absent only in the TEST-ONLY sm3_ipc_off build)
    assert odd == list(range(0x00, 0xC0)) + [0x0F0, 0x0F1, 0x0F2, 0x0F3] + ipc_ + list(range(0x100, 0x160)) + list(range(0x180, 0x1E0)), f"production: non-register non-immediate entries {odd}"   # R-play1: 0x80..0xBF are memory descriptors, 0xC0..0xDF carry the immediate bit
    assert {i: mtab[i] >> 12 for i in (0x0F2, 0x0F3)} == {0x0F3: 0x14C, 0x0F2: 0x88C}, "production: memory descriptors {0x0F3: 0x14C, 0x0F2: 0x88C}"
    assert all(mtab[0x180 + r] >> 12 == 0xA10 and mtab[0x1A0 + r] >> 12 == 0xC10 for r in range(32)), "production: [Rn+] / [-Rn] descriptors"
    assert all(mtab[0x1C0 + r] >> 12 == 0x830 and all(mtab[g + r] >> 12 == {0x80: 0x1D4, 0xA0: 0x1D8, 0xC0: 2}[g] and (mtab[g + r] & 0xFFF) == sym["mdd"] for g in (0x80, 0xA0, 0xC0)) for r in range(32)), "production: index-prefix / mdd descriptors (R-play1: [d8[Rn]] 0x1D4, [d16[Rn]] 0x1D8, [d32[Rn]] bit 1)"
    # R-post1: in bank 0 only the [d16[Rn]] entries are "register direct" (the index-base REGDIR branches rely on it); every bank-0 entry with bit 0 or 1 that is
    # not #q / #imm runs `mdd`
    assert sorted(i for i in range(0x100) if (mtab[i] >> 12) & 1) == [], "R-play1: no bank-0 entry is register direct (the R-post1 [d16[Rn]] bit 0 is gone)"
    assert all((mtab[i] & 0xFFF) == sym["mdd"] for i in range(0x80, 0xE0)), "R-post1: bank-0 indirect entries run mdd"
    assert all([mtab[r] >> 12, mtab[0x20 + r] >> 12, mtab[0x40 + r] >> 12, mtab[0x60 + r] >> 12] == [0x014, 0x018, 0x01C, 0x010] for r in range(32)), \
        "production: d8/d16/d32[Rn] / [Rn] descriptors"
    assert sorted(i for i, v in mtab.items() if (v & 0xFFF) not in (trap, tmode) and (v >> 16) & 1) == list(range(0x00, 0xC0)) + [0x0F0, 0x0F1] + ipc_ + list(range(0x100, 0x160)) + list(range(0x180, 0x1E0)), \
        "production: descriptor bit 4 exactly on the register-based memory entries (SM1: + d8[PC] / d16[PC]; SP3-B: + [d8 / d16[PC]])"
    if ipc_:
        assert [mtab[0x0F8], mtab[0x0F9]] == [(0x1D4 << 12) | sym["mdd"], (0x1D8 << 12) | sym["mdd"]], "SP3-B: F8 / F9 = [d8 / d16[Rn]]'s descriptors and routine (mdd -> mpi)"
    assert {i: mtab[i] >> 12 for i in (0x0F0, 0x0F1)} == {0x0F0: 0x094, 0x0F1: 0x098}, "SM1: d8[PC] / d16[PC] = bit 4, AGU base 2 (ipc), 8 / 16-bit displacement, untagged"
    # GETPSW (modm 1) classifier: no modm-1 entry is immediate, so an immediate can never reach its trailing trap (equivalent mutants, plan 11 #1);
    # [Rn+] / [-Rn] fall to its `jmp trap_mode`
    assert all(not ((v >> 12) & 2) for i, v in mtab.items() if i >= 0x100 and (v & 0xFFF) not in (trap, tmode)), \
        "production: an immediate modm-1 entry"


def check_tag(p, sym, mtab, smc_test, mut=None):
    """R4b builder assertions for the S-D tag (docs/r4-movea-pcrel-plan.md 5.5): the descriptor bit 11 (ea_desc[11] = micro-condition 23)
    appears on exactly one supported mode-table entry (0x0F2) in production builds and on none in --smc-test builds; condition 23 is
    branched on by exactly two words in production (the jmp guard -> trap_mode, the movea branch -> movea_pcrel), each preceded in its
    routine by an operand() call, and by none in --smc-test builds; no word could consume the descriptor's base / size / step bits
    (DEC_EA, AGU_*, MAR_LD, an AGU RES_SEL, CONSUME 9-11)."""
    if _NO_CHECKS:
        return
    from microasm import FIELDS
    f = lambda wd, k: (wd >> FIELDS[k][0]) & ((1 << FIELDS[k][1]) - 1)
    trap, tmode = sym["trap"], sym["trap_mode"]
    tagged = sorted(i for i, v in mtab.items() if (v & 0xFFF) not in (trap, tmode) and (v >> 12) & 0x800)
    c23 = [ix for ix, w_ in enumerate(p) if f(w_, "NXT") == 2 and (f(w_, "COND") | (f(w_, "COND_HI") << 4)) == C_TAG]
    live = [ix for ix, w_ in enumerate(p) if f(w_, "DEC_EA") or any(f(w_, k) for k in ("AGU_BASE", "AGU_OFF", "AGU_IDX", "AGU_STEP", "MAR_LD"))
            or f(w_, "RES_SEL") in (RES["AGU"], RES["AGUSTEP"]) or 9 <= f(w_, "CONSUME") <= 11]
    starts0 = sorted((a, n) for n, a in sym.items())
    owner = lambda ix: [n for a, n in starts0 if a <= ix][-1]
    bad = [(ix, owner(ix)) for ix in live if not (owner(ix).startswith(("ea0", "ea1")) or owner(ix).endswith("_di") or owner(ix).startswith("movcu") or owner(ix) in ("irq_core", "src1_imm", "movea_c23", "jsr_rb2", "gf_imm") or owner(ix) in ("movea_rb", "movea_c23_b", "movea_f1_rb", "src0_imm", "movea_f1_idx", "movea_f1_idx_b", "jsr_x"))]
    assert not bad, f"B2: DEC_EA / AGU words outside the ea0 / ea1 subroutines and movea_rb: {bad}"
    if smc_test:
        assert not live, f"smc_test: DEC_EA words {live}"
    if smc_test:
        assert tagged == [] and c23 == [], f"smc_test: tagged entries {tagged} / condition-23 words {c23} (must be none)"
        return
    assert tagged == [0x0F2] + list(range(0x100, 0x160)) + list(range(0x180, 0x1E0)), f"production: tagged (bit 11) supported entries {[hex(i) for i in tagged]}, expected 0x0F2, the [Rn+] / [-Rn] and the index-prefix entries"
    starts = sorted((a, n) for n, a in sym.items())
    names = {a: n for n, a in sym.items()}
    got = []
    for ix in c23:
        r = [n for a, n in starts if a <= ix][-1]
        assert f(p[ix], "CPOL") == 0, f"condition-23 word {ix} ({r}) has inverted polarity"
        assert r in ("ea0_rb", "ea1_rb", "ea0_idx_rb", "ea1_idx_rb", "movea_rb", "movea_c23_b", "movea_c23_rb", "movea_f1_rb", "jsr_rb", "sm_mvf1x_rb") or any(f(p[j], "NXT") == 3 and names.get(f(p[j], "JADDR"), "").startswith("mstub") for j in range(sym[r], ix)), \
            f"condition-23 word {ix} in {r} is not preceded by an operand() call"
        got.append((r, names.get(f(p[ix], "JADDR"))))
    sm_rb = [] if (mut or {}).get("sm_mvf1x_norb") or (mut or {}).get("rp3_idx_anybase") else [("sm_mvf1x_rb", "trap_mode")]   # SM1: movea F1 indexed register-based base
    assert sorted(got) == sorted([("ea0_idx_rb", "trap_mode"), ("ea0_rb", "ea0_step"), ("ea1_idx_rb", "trap_mode"), ("ea1_rb", "ea1_step"), ("jmp", "jmp_ea"), ("jsr_normal", "jsr_pc"), ("jsr_rb", "trap_mode"), ("jsr_x", "jsr_xb"), ("movea", "movea_pcrel"),
                           ("movea_c23_rb", "trap_mode"), ("movea_f1", "movea_f1_pc"), ("movea_f1_idx", "movea_f1_idx_b"), ("movea_f1_rb", "movea_f1_idx"), ("movea_rb", "movea_c23")] + sm_rb), f"condition-23 words {got}"


def check_ldpr(p, sym, dtab, fix, start, chk_start, end):
    """R2b builder self-assertions (plan sections 3, 9 #2, 9 #6): the regID predicate, isolation from the
    SMC [Rn] store path, the SBR file entry, and the dispatch-table flag."""
    from microasm import FIELDS
    f = lambda wd, k: (wd >> FIELDS[k][0]) & ((1 << FIELDS[k][1]) - 1)
    # (1) the regID predicate, from the emitted RCONST fields, exhaustively over q = 0..15
    add, msk = f(p[chk_start], "RCONST"), f(p[chk_start + 1], "RCONST")
    assert f(p[chk_start], "ALU_OP") == ALU["ADD"] and f(p[chk_start + 1], "ALU_OP") == ALU["AND"]
    assert f(p[chk_start - 1], "RCONST") == 15 and f(p[chk_start - 1], "ALU_OP") == ALU["AND"]
    for q in range(16):
        zero = (((q & 15) + add) & 0xFFFFFFFF & msk) == 0
        assert zero == (q in LDPR_IDS), f"LDPR regID predicate wrong at q={q}"
        # the quick byte actually seen by hardware is 0xE0|q: mreg = byte & 31 must equal q (the file entry)
        assert ((0xE0 | q) & 31) == q
    # (2) no FLAG_UPD, no bus/MAR/MDR traffic, no GPR write anywhere in the routine
    for i in range(start, end):
        assert f(p[i], "FLAG_UPD") == 0 and f(p[i], "BUS_OP") == 0, f"LDPR word {i}: flag update or bus op"
        assert f(p[i], "WR_DST") in (0, WR["TEMP"], WR["PRIV"]), f"LDPR word {i}: unexpected write target"
    privw = [i for i in range(start, end) if f(p[i], "WR_DST") == WR["PRIV"]]
    assert len(privw) == 1 and f(p[privw[0]], "WR_ASEL") == 3, "LDPR: exactly one privileged write, WR_ASEL = 3"
    # (3) isolation from the test-only [Rn] store path: no jump from the routine into dst / dst_reg / store code
    for i, name in fix:
        if start <= i < end:
            assert name in ("trap_priv", "trap_mode", "trap_regid", "disp") or name.startswith("mstub"), \
                f"LDPR references {name}"
    # (4) dispatch: the privileged flag only on dtab[0x12]
    assert (dtab[0x12] >> 12) & 0x80 and (dtab[0x12] & 0xFFF) == sym["ldpr"], "dtab[0x12] = ldpr with the privileged flag"
    assert [op for op, v in dtab.items() if (v >> 12) & 0x80] == [0x12], "only LDPR is privileged"


def check_movea(p, sym, dtab, mtab, fix, start, end, smc_test):
    """R3b builder self-assertions (plan sections 5, 6): the routine is the 9-word sketch, writes exactly one GPR with
    WR_ASEL = 1, has no FLAG_UPD, bus or privileged traffic, jumps only to the dispatch loop or the mode trap (never to the
    SMC [Rn] store path), and only dtab[0x40] is dispatched to it."""
    from microasm import FIELDS
    f = lambda wd, k: (wd >> FIELDS[k][0]) & ((1 << FIELDS[k][1]) - 1)
    if smc_test:
        assert "movea" not in sym and start == end, "smc_test: no MOVEA routine"
        assert all(v != sym.get("movea") for v in dtab.values())
        return
    assert end - start == 13, f"MOVEA routines (abs: 11 words, movea_pcrel: 2 words) are 13 words (is {end - start})"
    for i in range(start, end):
        assert f(p[i], "FLAG_UPD") == 0 and f(p[i], "BUS_OP") == 0, f"MOVEA word {i}: flag update or bus op"
        assert f(p[i], "WR_DST") in (0, WR["GPR"]), f"MOVEA word {i}: unexpected write target"
    gw = [i for i in range(start, end) if f(p[i], "WR_DST") == WR["GPR"]]
    pc0 = sym["movea_pcrel"]
    assert len(gw) == 2 and all(f(p[i], "WR_ASEL") == 1 for i in gw), "MOVEA: exactly two GPR writes (abs, pcrel), WR_ASEL = 1 (the format-byte register)"
    ga = [i for i in gw if i < pc0]
    gp = [i for i in gw if i >= pc0]
    assert len(ga) == 1 and len(gp) == 1, "MOVEA: one GPR write in each of the abs routine and movea_pcrel"
    assert f(p[ga[0]], "IMM_LD") == 1 and f(p[ga[0]], "IMM_OFS") == 1 and f(p[ga[0]], "IMM_SZ") == 2 and f(p[ga[0]], "CONSUME") == 5, \
        "MOVEA: the address is the 32-bit field at +3 (IMM_OFS 1, IMM_SZ 2), CONSUME 5"
    g = p[gp[0]]
    assert (f(g, "IMM_LD"), f(g, "IMM_OFS"), f(g, "IMM_SZ"), f(g, "CONSUME")) == (1, 1, 2, 5) and f(g, "ALU_OP") == ALU["ADD"], \
        "MOVEA pcrel: ipc + the 32-bit field at +3 (IMM_OFS 1, IMM_SZ 2, ADD), CONSUME 5"
    assert f(g, "ASEL") == A["PC"] and f(g, "BSEL") == A["IMM"] and f(g, "FLAG_UPD") == 0, "MOVEA pcrel: ASEL = PC (ipc), BSEL = IMM, no FLAG_UPD"
    assert sym["movea_pcrel"] == end - 2, "movea_pcrel is the last two words of the MOVEA block"
    for i, name in fix:
        if start <= i < end:
            assert name in ("trap_mode", "disp", "movea_pcrel", "movea_f1", "movea_rb", "movea_c23") or name.startswith("mstub"), f"MOVEA references {name}"
    assert [op for op, v in dtab.items() if (v & 0xFFF) == sym["movea"]] == [0x40, 0x42, 0x44], "only dtab[0x40 / 0x42 / 0x44] dispatch movea"
    assert (dtab[0x42] & 0xFFF) == sym["movea"] and (dtab[0x44] & 0xFFF) == sym["movea"], "B3: dtab[0x42] / dtab[0x44] = movea (size1 = half / word)"
    assert mtab[0x1F3] == sym["trap_mode"], "F3 with m = 1 stays trap_mode"


def self_check():
    """All 8 option combinations satisfy the assertions (plan section 11 note 5)."""
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                build(st, sm, rn)


# ---- V60 S1 mul / div on the release (research/v60-s1md dadc239 / 7176b33, 2026-11-19): `mul`, `div` of research/v60-s1 (17d5c8f) = P1.1 /
# P1.2's shared arithmetic routine g_mulg (multiply, divide, remainder in every operand form), ported alone onto the pinned release builder.
# C1 slice 1 (var, dbcc, cv) and ADDC / SUBC (addc) are NOT ported.
# release/s32-v1-ro2 (Codex S1MD r1 finding 3): the routine is selected ONLY by the explicit builder argument `--arith=mul,div` (recorded in the
# microcode pin's `args`); without it this file builds the release image byte for byte. The research switch C1_GEN is not honoured here: an
# ambient C1_GEN (any value) makes the builder refuse, so the environment can change neither the default nor the selected image.
import os as _c1os
if "C1_GEN" in _c1os.environ:
    raise SystemExit("refusing: C1_GEN is set in the environment; this builder selects the S1 multiply / divide routine only by --arith=mul,div")
ARITH_ARG = "--arith=mul,div"
_C1GEN = set()                                           # set to {"mul", "div"} by main() for --arith=mul,div only
_S1 = False


def build(smc_test=False, smc_merged=False, reset_native=False, mut=None):
    """mut: TEST-ONLY in-memory LDPR mutants (m1_r2b_mutants.py); None (the default) builds the production image."""
    global _NO_CHECKS
    mut = mut or {}
    _NO_CHECKS = bool(mut.get("mv_no_checks"))
    p, sym, fix = [], {}, []

    def at(name):
        assert name not in sym, f"duplicate microcode label {name}"   # U1 merge: a second definition used to win silently
        sym[name] = len(p)

    def w(**f):
        f.setdefault("SIZE_SEL", 2)                     # word
        f.setdefault("BE_MODE", 1)                      # all four byte lanes
        p.append(word(**f))

    def jmp(name, **f):                                 # jump to a symbol (fixed up later)
        fix.append((len(p), name))
        p.append(word(NXT=1, **f))

    def br(c, name, pol=0):                             # conditional jump on ucond c
        fix.append((len(p), name))
        p.append(word(NXT=2, CPOL=pol, **cond(c)))

    def call(name, **f):
        fix.append((len(p), name))
        p.append(word(NXT=3, **f))

    def poll(ofs, msel):                                # wait for decode-ready
        here = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here, IMM_OFS=ofs, MODM_SEL=msel, **cond(C_READY)))

    def operand(msel):                                  # look up the specifier at the head
        poll(0, msel)
        call(f"mstub{msel}", IMM_OFS=0, MODM_SEL=msel)

    # ---- reset: redirect the queue to 0, then decode ----
    # reset_native (S0, real GA2 code): no redirect; decode from the fetch
    # queue's own reset address 0xFFFFFFF0, the V60 reset PC
    at("reset")
    if not reset_native:
        w(ASEL=A["CONST"], RCONST=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
        p.extend([word()] * 3)                          # the redirect takes effect in E3
    # ---- opcode dispatch ----
    at("disp")
    poll(1, MS_ZERO)
    p.append(word(NXT=5, IMM_OFS=1))

    # ---- mode-dispatch stubs and the common mode routine ----
    for msel in (MS_B6, MS_B5, MS_OP0):
        at(f"mstub{msel}")
        p.append(word(NXT=6, IMM_OFS=0, MODM_SEL=msel))
    at("mret")
    p.append(word(NXT=4))

    # ---- HALT, NOP ----
    # HALT (research/v60-halt, RO2 hardware P081935 U005): wait while no eligible interrupt is pending (ucond 10 = irq_pending: the IRQ line with
    # PSW.IE set, or NMI), then fall through into NOP: consume the 1-byte HALT and dispatch the next instruction, whose dispatch stub takes the
    # interrupt with ipc = HALT + 1 (upstream s32_v60 S_HALT: wake on NMI or IRQ & IE, resume at PC + 1, the interrupt is taken before it executes).
    # Without an eligible interrupt this word loops on itself (a conditional self-branch, not a trap).
    at("halt")
    p.append(word(NXT=2, CPOL=1, JADDR=sym["halt"], **cond(10)))
    at("nop")
    p.append(word(CONSUME=1))
    jmp("disp")

    # ---- MOV.W ----
    at("movw")
    p.append(word(CONSUME=2))                            # opcode + format byte
    br(C_F1, "movw_f1")
    br(C_D, "movw_d1")
    # F2 D=0: R[ifl[4:0]] -> destination specifier
    w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
    operand(MS_B6)
    jmp("dst")
    at("movw_d1")                                       # F2 D=1: source specifier -> R[ifl[4:0]]
    operand(MS_B6)
    br(C_REGDIR, "src_reg_to_fld")
    br(C_QUICK, "src_quick_to_fld")
    br(C_IMM, "trap_mode", pol=1)                       # R1b guard: /abs (neither register nor #q nor #imm) is not a value
    if mut.get("pc_third_c23"):                         # TEST-ONLY mutant: a third condition-23 consumer
        br(C_TAG, "trap_mode")
    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"],
      RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)  # #imm32
    jmp("disp")
    at("src_reg_to_fld")
    w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
      WR_DST=WR["GPR"], WR_ASEL=1, CONSUME=1)
    jmp("disp")
    at("src_quick_to_fld")
    w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15,
      ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
    jmp("disp")
    at("movw_f1")                                       # F1: source -> T0 -> destination
    operand(MS_B6)
    br(C_REGDIR, "src_reg_to_t0")
    br(C_QUICK, "src_quick_to_t0")
    br(C_IMM, "trap_mode", pol=1)                       # R1b guard (as movw_d1)
    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"],
      RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
    jmp("f1_dst")
    at("src_reg_to_t0")
    w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
      WR_DST=WR["TEMP"], TADDR=T0, CONSUME=1)
    jmp("f1_dst")
    at("src_quick_to_t0")
    w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15,
      ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
    at("f1_dst")
    operand(MS_B5)
    # destination: T0 -> the specifier (register direct only; immediates trap)
    at("dst")
    br(C_REGDIR, "dst_reg")
    if smc_test:                                        # TEST-ONLY: [Rn] word store
        br(C_IMM, "trap_immdest")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"], CONSUME=1)
        w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        p.extend([word()] * 3)
        if smc_merged:
            jmp("disp", BUS_OP=2, BUS_SIZE=3)           # store and jump to decode in one word
        else:
            p.append(word(BUS_OP=2, BUS_SIZE=3))        # the last word: then straight to decode
            jmp("disp")
    br(C_IMM, "trap_immdest")                           # R1b: an immediate destination ...
    jmp("trap_mode")                                    # ... else (/abs, memory modes) trap_mode
    at("dst_reg")
    w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
      WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
    jmp("disp")

    # ---- M1.2: ADD.W / SUB.W / CMP.W / AND.W ----
    for n, (opc, aop, writes, fmask) in ALU_OPS.items():
        def exe(dst_sel, **f):                          # the execute word: b (B bus) op a (A bus) -> operand 2
            w(ALU_OP=aop, RES_SEL=RES["ALU"], FLAG_UPD=fmask,
              **({"WR_DST": WR["GPR"], "WR_ASEL": dst_sel} if writes else {}), **f)
        quick_mask = dict(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"],
                          RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])     # no write: bypassed
        at(n)
        p.append(word(CONSUME=2))                        # opcode + format byte
        br(C_F1, f"{n}_f1")
        br(C_D, f"{n}_d1")
        # F2 D=0: a = R[ifl[4:0]] -> T0; b = the destination specifier
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        operand(MS_B6)
        jmp(f"{n}_dst")
        at(f"{n}_d1")                                   # F2 D=1: a = the source specifier, b = R[ifl[4:0]]
        operand(MS_B6)
        br(C_REGDIR, f"{n}_d1_reg")
        br(C_QUICK, f"{n}_d1_quick")
        br(C_IMM, "trap_mode", pol=1)                   # R1b guard (value source)
        exe(1, IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], BSEL=A["RB"], RB_SEL=1)
        jmp("disp")
        at(f"{n}_d1_reg")
        exe(1, RA_SEL=3, ASEL=A["RA"], RB_SEL=1, BSEL=A["RB"], CONSUME=1)
        jmp("disp")
        at(f"{n}_d1_quick")
        w(**quick_mask)
        exe(1, ASEL=A["RES"], FWD_EN=1, RB_SEL=1, BSEL=A["RB"])
        jmp("disp")
        at(f"{n}_f1")                                   # F1: a = source -> T0; b = destination
        operand(MS_B6)
        br(C_REGDIR, f"{n}_f1_reg")
        br(C_QUICK, f"{n}_f1_quick")
        br(C_IMM, "trap_mode", pol=1)                   # R1b guard (value source)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"],
          RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        jmp(f"{n}_f1_dst")
        at(f"{n}_f1_reg")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
          WR_DST=WR["TEMP"], TADDR=T0, CONSUME=1)
        jmp(f"{n}_f1_dst")
        at(f"{n}_f1_quick")
        w(**dict(quick_mask, WR_DST=WR["TEMP"], TADDR=T0))
        at(f"{n}_f1_dst")
        operand(MS_B5)
        at(f"{n}_dst")                                  # b = operand 2 (T0 holds a)
        br(C_REGDIR, f"{n}_dst_reg")
        if writes:
            br(C_IMM, "trap_immdest")                   # immediate destination: the references disagree
            jmp("trap_mode")                            # R1b: anything else (/abs) is a mode trap
        else:                                           # CMP: an immediate operand 2 is a value
            br(C_QUICK, f"{n}_dst_quick")
            br(C_IMM, "trap_mode", pol=1)               # R1b guard (value operand 2)
            exe(0, TADDR=T0, ASEL=A["RT"], IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, BSEL=A["IMM"])
            jmp("disp")
            at(f"{n}_dst_quick")
            w(**quick_mask)
            exe(0, TADDR=T0, ASEL=A["RT"], BSEL=A["RES"], FWD_EN=1)
            jmp("disp")
        at(f"{n}_dst_reg")
        exe(3, TADDR=T0, ASEL=A["RT"], RB_SEL=3, BSEL=A["RB"], CONSUME=1)
        jmp("disp")

    # ---- M1.2: GETPSW (0xF7, modm 1 = opcode bit 0) to a register ----
    at("getpsw")
    p.append(word(CONSUME=1))                            # opcode; the specifier is byte 1
    operand(MS_OP0)
    br(C_REGDIR, "getpsw_reg")
    br(C_IMM, "trap_immdest")                           # an immediate destination
    jmp("trap_mode")                                    # R1b: anything else is a mode trap
    at("getpsw_reg")
    w(ASEL=A["PSW"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
    jmp("disp")

    # ---- R1b: JMP /abs (D6 F3 a0 a1 a2 a3): PC <- the little-endian word at start + 2 ----
    # Production tables only (variant isolation, plan section 10 #1): with smc_test the routine is not
    # emitted, dtab[0xD6] stays trap and mtab[0x0F3] stays trap_mode. Register direct and immediates
    # trap_mode (the C_REGDIR guard is defensive: only D7 could reach it and D7 is not dispatched).
    if not smc_test:
        at("jmp")
        p.append(word(CONSUME=1))                        # opcode; the specifier is byte 0 of the window
        if mut.get("pc_jmp_guard_early"):                # TEST-ONLY mutant: the guard BEFORE the lookup reads the previous instruction's descriptor
            br(mut.get("pc_jmp_cond", C_TAG), "trap_mode", pol=mut.get("pc_jmp_pol", 0))
        operand(MS_OP0)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        br(22, "jmp_ea")                                 # B3: register-based memory (d[Rn], [Rn], dd[Rn], indexed, ...): the target is the effective address (ea0)
        if not mut.get("pc_no_jmp_guard") and not mut.get("pc_jmp_guard_early"):
            br(mut.get("pc_jmp_cond", C_TAG), "jmp_ea", pol=mut.get("pc_jmp_pol", 0))      # B3: jmp d32[PC] likewise (was a mode trap after R4b)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"],
          RES_SEL=RES["ALU"], REDIRECT=1)
        p.extend([word()] * 3)                          # the redirect takes effect in E3
        jmp("disp")

    if not smc_test:
        at("jmp_ea")
        call("ea0")
        p.extend([word()] * 3)
        w(ASEL=A["MAR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
        p.extend([word()] * 3)
        jmp("disp")

    # ---- BR8: ipc + sign-extended disp8 ----
    # The word consumes the instruction's two bytes, as every instruction
    # does, so by E1 the fetch head has moved past them: the PC source must be
    # the instruction-start PC (ipc), not the head (the redirect then
    # discards the queue anyway).
    at("br8")
    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"],
      ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=2)
    p.extend([word()] * 3)                              # the redirect takes effect in E3
    jmp("disp")

    # ---- traps ----
    for t in ("trap", "trap_mode", "trap_immdest"):
        at(t)
        p.append(word(NXT=1, JADDR=sym[t]))

    # R2b: the new trap kinds and the LDPR routine are appended after every existing word, so no existing
    # control-store address (and no existing dispatch / mode table entry except dtab[0x12]) moves
    for t in ("trap_priv", "trap_regid"):
        at(t)
        p.append(word(NXT=1, JADDR=sym[t]))

    # ---- R2b: LDPR #imm32, #q (0x12; format II; plan docs/r2-ldpr-plan.md section 3) ----
    # PrivilegedRegister(q) <- the 32-bit immediate, q in {2, 3, 4, 5} (L1SP, L2SP, L3SP, SBR); flags and every
    # other register unchanged. Present in every build (production and --smc-test): it does not touch the
    # test-only [Rn] store path (self-checked below). `mut` knobs are TEST-ONLY mutants (never set by default).
    at("ldpr")
    ldpr_start = len(p)
    if not mut.get("no_priv_check"):
        br(C_PRIV, "trap_priv")                          # privileged instruction at EL != 0 (dtab privileged flag)
    if not mut.get("no_f1_guard"):
        br(C_F1, "trap_mode", pol=1)                     # format I encodings (byte 1 bit 7 = 0): out of scope
    p.append(word(CONSUME=2))                            # opcode + format byte
    operand(MS_B6)
    if not mut.get("no_src_imm_guard"):
        br(C_IMM, "trap_mode", pol=1)                    # the source must be an immediate ...
    if not mut.get("no_src_quick_guard"):
        br(C_QUICK, "trap_mode")                         # ... and not #q (OI-3: MAME history-dependent)
    w(IMM_LD=1, IMM_OFS=mut.get("imm_ofs", 1), IMM_SZ=mut.get("imm_sz", 2), CONSUME=mut.get("imm_consume", 5),
      ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0,
      **({"FLAG_UPD": mut["flag_upd_imm"]} if "flag_upd_imm" in mut else {}))
    operand(MS_B5)
    if not mut.get("no_regid_quick_guard"):
        br(C_QUICK, "trap_mode", pol=1)                  # the register ID must be #q (OI-5: register / memory regID)
    # regID check: res = ((q & 15) + 30) & 28 is zero exactly for q = 2..5; result-only ALU words, chained
    # through the result bypass (no temp, no FLAG_UPD)
    w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15,
      ALU_OP=ALU["AND"], RES_SEL=RES["ALU"],
      **({"FLAG_UPD": mut["flag_upd_q"]} if "flag_upd_q" in mut else {}))
    ldpr_chk_start = len(p)
    w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=mut.get("id_add", LDPR_ADD), ALU_OP=ALU["ADD"],
      RES_SEL=RES["ALU"], **({"FLAG_UPD": mut["flag_upd_add"]} if "flag_upd_add" in mut else {}))
    w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=mut.get("id_mask", LDPR_MASK), ALU_OP=ALU["AND"],
      RES_SEL=RES["ALU"], **({"FLAG_UPD": mut["flag_upd_and"]} if "flag_upd_and" in mut else {}))
    p.extend([word()] * 2)                               # res_q is registered: the branch issues 3 words after the result word
    if not mut.get("no_id_check"):
        br(C_ZERO, "trap_regid", pol=1)                  # nonzero: not one of the IDs 2..5
    w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=mut.get("wr_dst", WR["PRIV"]),
      WR_ASEL=mut.get("wr_asel", 3), RCONST=mut.get("wr_rconst", 0),
      CONSUME=mut.get("final_consume", 1),               # the #q byte
      **({"FLAG_UPD": mut["flag_upd_wr"]} if "flag_upd_wr" in mut else {}))
    jmp("disp")
    ldpr_end = len(p)

    # ---- R3b: MOVEA.B /abs, Rn (0x40; Format I [manual naming], V2 F2, d = 1; plan docs/r3-movea-plan.md section 5) ----
    # Rn <- the 32-bit EFFECTIVE ADDRESS of the /abs source (not the contents at that address): the full word, no sign or
    # zero extension, no byte masking; no memory access; flags and every other register unchanged; length 7.
    # Production tables only (variant isolation, as JMP): in --smc-test builds the routine is not emitted and
    # dtab[0x40] stays trap, because [Rn] there is a descriptor that is neither register nor immediate. Guards (all
    # trap `mode`): Format II (V2 C_F1 set), d = 0 (C_D clear), source Rn (C_REGDIR), source #q / #imm (C_IMM).
    # `mut` mv_* knobs are TEST-ONLY mutants (m1_r3b_mutants.py); never set by default.
    movea_start = len(p)
    if not smc_test or mut.get("mv_smc_dispatch"):
        at("movea")
        p.append(word(CONSUME=mut.get("mv_fmt_consume", 2)))     # opcode + format byte
        if not mut.get("mv_no_f1_guard"):
            br(C_F1, "movea_f1")                                 # Format II (byte 1 bit 7 set): B1, only with a [-Rn] destination (else trap `mode` there)
        if not mut.get("mv_no_d_guard"):
            br(C_D, "trap_mode", pol=1)                          # d = 0: the register is operand 1 (illegal source)
        operand(MS_B6)
        if not mut.get("mv_no_regdir_guard"):
            br(C_REGDIR, "trap_mode")                            # source Rn
        if not mut.get("mv_no_imm_guard"):
            br(C_IMM, "trap_mode")                               # source #q / #imm
        br(22, "movea_rb")                                       # B2: register-based memory (d[Rn], [Rn], [Rn+], [-Rn]; descriptor bit 4) -> movea_rb (DEC_EA)
        if not mut.get("pc_no_branch"):
            br(mut.get("pc_cond", C_TAG), "movea_pcrel", pol=mut.get("pc_pol", 0))   # R4b: the tagged descriptor (disp32[PC]) -> the PC-relative word
        imm = dict(IMM_LD=1, IMM_OFS=mut.get("mv_imm_ofs", 1), IMM_SZ=mut.get("mv_imm_sz", 2),
                   CONSUME=mut.get("mv_consume", 5), ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        if mut.get("mv_imm_sx"):
            imm["IMM_SX"] = 1
        if mut.get("mv_mask8"):
            imm.update(ALU_OP=ALU["ZEXT"], SIZE_SEL=3, RCONST=0)   # result zero-extended from a byte (size = RCONST[1:0] = 0)
        wr = dict(WR_DST=mut.get("mv_wr_dst", WR["GPR"]), WR_ASEL=mut.get("mv_wr_asel", 1))
        if mut.get("mv_wr_asel") == 0:
            wr["RCONST"] = mut.get("mv_rconst", 0)
        if "mv_flag_upd" in mut:
            wr["FLAG_UPD"] = mut["mv_flag_upd"]
        if mut.get("mv_nowrite"):                                # the value goes to a temp: no GPR write
            w(**dict(imm, WR_DST=WR["TEMP"], TADDR=T0))
        elif mut.get("mv_dup"):                                  # the same GPR write twice
            w(**dict(imm, WR_DST=WR["TEMP"], TADDR=T0))
            p.extend([word()] * 3)
            wd = word(SIZE_SEL=2, BE_MODE=1, TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], **wr)
            p.extend([wd, wd])
        elif mut.get("mv_transient"):                            # a constant, then the address: two GPR events
            w(ASEL=A["CONST"], RCONST=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
            w(**dict(imm, **wr))
        elif mut.get("mv_value_read"):                           # the v60_ref defect: load the byte AT the address
            w(**dict(imm, WR_DST=WR["MAR"]))
            w(BUS_OP=1, BUS_SIZE=1)
            p.extend([word()] * 4)
            w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], **wr)
        else:
            w(**dict(imm, **wr))                                 # the address, no flag update
        if not mut.get("mv_no_disp_jmp"):
            jmp("disp")
        # ---- R4b: MOVEA.B disp32[PC], Rn (Format I d = 1; plan docs/r4-movea-pcrel-plan.md section 5.4) ----
        # Rn <- ipc + disp32 (ipc = the address of the instruction's FIRST byte, as BR8 uses it): one execute word, no flag update, no bus.
        # `pc_*` knobs are TEST-ONLY mutants (m1_r4b_mutants.py); never set by default.
        if not mut.get("pc_no_pcrel"):
            at("movea_pcrel")
            pcimm = dict(IMM_LD=1, IMM_OFS=mut.get("pc_imm_ofs", 1), IMM_SZ=mut.get("pc_imm_sz", 2), ASEL=A["PC"], BSEL=A["IMM"],
                         ALU_OP=ALU["SUB"] if mut.get("pc_sub") else ALU["ADD"], RES_SEL=RES["ALU"])
            if mut.get("pc_swap"):
                pcimm.update(ASEL=A["IMM"], BSEL=A["PC"])
            if mut.get("pc_base0"):
                pcimm.update(ASEL=A["ZERO"])
            if mut.get("pc_imm_sx"):
                pcimm["IMM_SX"] = 1
            pcwr = dict(WR_DST=mut.get("pc_wr_dst", WR["GPR"]), WR_ASEL=mut.get("pc_wr_asel", 1))
            if mut.get("pc_wr_asel") == 0:
                pcwr["RCONST"] = mut.get("pc_rconst", 0)
            if "pc_flag_upd" in mut:
                pcwr["FLAG_UPD"] = mut["pc_flag_upd"]
            pcc = mut.get("pc_consume", 5)
            if mut.get("pc_addk"):                                   # wrong base: start + k (the sum, then + k through the result bypass)
                w(**pcimm)
                w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=mut["pc_addk"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], CONSUME=pcc, **pcwr)
            elif mut.get("pc_mask"):                                 # result zero-extended from 8 / 16 bits
                w(**pcimm)
                w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["ZEXT"], SIZE_SEL=3, RCONST={8: 0, 16: 1}[mut["pc_mask"]], RES_SEL=RES["ALU"], CONSUME=pcc, **pcwr)
            elif mut.get("pc_nowrite"):
                w(**dict(pcimm, CONSUME=pcc, WR_DST=WR["TEMP"], TADDR=T0))
            elif mut.get("pc_dup"):
                w(**dict(pcimm, WR_DST=WR["TEMP"], TADDR=T0))
                p.extend([word()] * 3)
                wd = word(SIZE_SEL=2, BE_MODE=1, TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], CONSUME=pcc, **pcwr)
                p.extend([wd, wd])
            elif mut.get("pc_transient"):
                w(ASEL=A["CONST"], RCONST=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
                w(**dict(pcimm, CONSUME=pcc, **pcwr))
            elif mut.get("pc_value_read"):                           # the v60_ref defect: load the byte AT the address
                w(**dict(pcimm, WR_DST=WR["MAR"]))
                w(BUS_OP=1, BUS_SIZE=1)
                p.extend([word()] * 4)
                w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], CONSUME=pcc, **pcwr)
            elif mut.get("pc_dummy_read"):                           # an extra bus READ of the target whose result is discarded
                w(**dict(pcimm, WR_DST=WR["MAR"]))
                w(BUS_OP=1, BUS_SIZE=1)
                p.extend([word()] * 4)
                w(**dict(pcimm, CONSUME=pcc, **pcwr))
            elif mut.get("pc_dummy_store"):                          # an extra bus WRITE (a store of a constant to the target)
                w(**dict(pcimm, WR_DST=WR["MAR"]))
                w(ASEL=A["CONST"], RCONST=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
                p.extend([word()] * 3)
                w(BUS_OP=2, BUS_SIZE=3)
                w(**dict(pcimm, CONSUME=pcc, **pcwr))
            else:
                w(**dict(pcimm, CONSUME=pcc, **pcwr))                # the address ipc + disp32, no flag update
            if not mut.get("pc_no_disp_jmp"):
                jmp("disp")
            if mut.get("pc_dec_ea_word"):                            # TEST-ONLY: a word that would make the 0x88C base/size/step bits live
                p.append(word(DEC_EA=1))
    movea_end = len(p)

    # ====================================================================================================================
    # B1 (CPU batch 1: the ladder forms after R4; production builds only; docs/ga2-bringup-roadmap.md). Exploratory standard: the S0
    # lock-step against upstream is the check; no per-form plan. Operand modes added: [Rn+] (bank 1, 0x80|r) and [-Rn] (bank 1, 0xA0|r),
    # descriptors 0x210 / 0x410 (bit 4 is the TEMPORARY "register-based memory mode" tag: it is read only by ucond 22, and nothing here
    # uses DEC_EA, an AGU field or CONSUME 9-11, so the descriptor's other bits stay dead). The mode routine is the common RETURN word.
    # ====================================================================================================================
    if not smc_test:
        def chk_hi3(ofs, grp, target, pad0=None, sh=5):
            """trap `target` unless (the byte at window offset ofs) >> 5 == grp (the mode byte's top three bits / DBcc's sub-opcode).
            R-post1: pad0 = fields for the first of the two wait words (a temp write that costs no cycle; its result is not tested)."""
            w(IMM_LD=1, IMM_OFS=ofs, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=sh, SH_OP=6, RES_SEL=RES["SHIFT"])   # R-post3: sh = 6 tests the top two bits
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=grp, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
            if pad0:
                w(**pad0)
                p.append(word())
            else:
                p.extend([word()] * 2)                   # res_q is registered: the branch issues 3 words after the result word
            br(C_ZERO, target, pol=1)

        # R-post1: the `mdd` flag T15 also marks an index-prefix BASE lookup (ea0 / ea1 indexed, movea indexed), set and cleared inside existing
        # wait words (no added cycle), so that a bank-0 [d16[Rn]] base returns from `mdd` (its descriptor has bit 0: routed by the REGDIR branch
        # that no bank-0 entry took before) while [d8[Rn]] / [d32[Rn]] bases (descriptor bit 1) still trap `mode`.
        T15_SET = dict(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
        T15_CLR = dict(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)

        def reg_to_temp(t):                              # T[t] <- R[mode-byte register]
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=t)

        def mar_from_temp(t):
            w(TADDR=t, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])

        def mdr_from_temp(t):
            w(TADDR=t, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])

        def bus(op, size):                               # one bus word; the pipeline then waits for the access (data_busy)
            p.append(word(BUS_OP=op, BUS_SIZE=size))

        def mdr_to_temp(t):                              # read data -> T[t] (consumer must be >= 3 words after the bus word)
            w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=t)

        def inc_reg_from_temp(t, k):                     # R[mode-byte register] <- T[t] + k
            w(TADDR=t, ASEL=A["RT"], BSEL=A["CONST"], RCONST=k, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3)

        # second operand's lookup one byte in (operand 1 is a one-byte specifier): the lookup words repeat IMM_OFS 1 (v2_sim_checks)
        at("mstub1o5")
        p.append(word(NXT=6, IMM_OFS=1, MODM_SEL=MS_B5))

        def operand_o1_b5():
            poll(1, MS_B5)
            call("mstub1o5", IMM_OFS=1, MODM_SEL=MS_B5)

        # ---- MOV.B [Rn+], [Rm+] (09, manual Format II / V2 F1 with both modm bits 1): the first real data-memory read and write ----
        # Both operands are validated before any side effect (R[op2] and R[op1] are read, nothing is written, until both bytes are
        # known to be [Rn+]): lookup op1, lookup op2 (one byte in), then the effects, then a second lookup of op1 so that the
        # mode-byte register is op1's for its autoincrement. Same-register operands ([Rn+],[Rn+]) are not supported / not tested
        # (the references disagree, OI-4). Flags unchanged.
        at("movb")
        p.append(word(CONSUME=2))                        # opcode + format byte
        br(C_F1, "trap_mode", pol=1)                     # only the F1 (two-specifier) form
        operand(MS_B6)
        chk_hi3(0, 4, "trap_mode")                       # op1 must be [Rn+]
        reg_to_temp(1)                                   # T1 = the source address
        operand_o1_b5()
        chk_hi3(1, 4, "trap_mode")                       # op2 must be [Rm+]
        reg_to_temp(3)                                   # T3 = the destination address
        p.extend([word()] * 3)
        inc_reg_from_temp(3, 1)                          # R[op2] += 1 (mode-byte register = op2)
        mar_from_temp(1)
        p.append(word())
        bus(1, 1)                                        # byte read at [T1]
        p.extend([word()] * 4)
        mdr_to_temp(0)                                   # T0 = the byte
        p.extend([word()] * 3)
        mar_from_temp(3)
        mdr_from_temp(0)
        p.extend([word()] * 2)
        bus(2, 3 if mut.get("b1_movb_wr_word") else 1)   # byte write at [T3] (TEST-ONLY mutant: a word store, no lane merging)
        operand(MS_B6)                                   # mode-byte register = op1 again
        inc_reg_from_temp(1, 1)                          # R[op1] += 1
        w(CONSUME=2)                                     # both one-byte specifiers
        jmp("disp")

        # ---- DBR Rn, disp16 (C6, byte 1 = 101 rrrrr; Format VI): Rn <- Rn - 1 (32 bits, as MAME); if Rn != 0: PC <- start + sext(disp16),
        # else PC <- start + 4. Flags unchanged. The other DBcc sub-opcodes (byte 1 bits 7:5 != 101) trap `opcode`.
        at("dbr")
        chk_hi3(1, 5, "trap")
        w(RB_SEL=1, BSEL=A["RB"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)     # Rn <- Rn - 1 (res_q: the new value)
        p.extend([word()] * 2)
        br(C_ZERO, "dbr_fall")
        w(IMM_LD=1, IMM_OFS=2, IMM_SZ=1, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=4)
        p.extend([word()] * 3)                           # the redirect takes effect in E3
        jmp("disp")
        at("dbr_fall")
        w(CONSUME=4)
        jmp("disp")

        # ---- MOVEA.B <src>, [-Rn] (Format II / V2 F1; B1): [-Rn] <- the 32-bit EFFECTIVE ADDRESS of the source (/abs or disp32[PC]), as a word store:
        # R[op2] <- R[op2] - 4 (the autodecrement of a word operand), then a word store at the new R[op2]. Reached from `movea` after
        # CONSUME 2 with the format byte's bit 7 set. Sources Rn / #q / #imm / [Rn+] / [-Rn] and destinations other than [-Rn] trap `mode`
        # before any side effect. Flags unchanged.
        at("movea_f1")
        operand(MS_B6)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        br(22, "movea_f1_rb")
        br(C_TAG, "movea_f1_pc")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)   # T0 <- /abs
        jmp("movea_f1_dst")
        at("movea_f1_pc")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)  # T0 <- ipc + disp32
        jmp("movea_f1_dst")
        at("movea_f1_rb")                                # B2: register-based memory source: T0 <- its effective address (DEC_EA); stepped modes trap
        br(23, "trap_mode" if mut.get("rp3_idx_off") else "movea_f1_idx")   # R-post3: the tagged class -> the /abs(Rx) check (was trap `mode`)
        poll(4, MS_ZERO)
        w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=T0, CONSUME=10)
        at("movea_f1_dst")
        operand(MS_B5)                                   # operand 2 (operand 1's bytes are consumed)
        # B2: any memory destination (the B1 [-Rn] case included): the word store goes through the shared ea1 subroutine (the table's size2 = word)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        call("ea1")
        p.append(word())
        mdr_from_temp(T0)
        p.extend([word()] * 2)
        bus(2, 3)                                        # word store at the destination
        jmp("disp")
        # MOVEA.H / MOVEA.W (42 / 44): only the Format II (F1) form, through the same path; other forms trap `mode`
        at("movea_hw")
        p.append(word(CONSUME=2))
        br(C_F1, "movea_f1")
        jmp("trap_mode")

        # ---- RETIS #q (FA, short format, modm 0): PC <- [SP]; PSW' <- [SP+4]; SP <- SP + 8 + q (the frame-destroy count, 4 bits); then the PSW
        # write, which switches the stack when the stack bank changes (IS 1 -> 0 or, while IS = 0, EL changes): the old SP is saved to its
        # bank register (ISP = privileged 0, L0SP..L3SP = 1..4) and R31 loads the new bank's. Supported: no bank change (PSW bits 28, 25, 24
        # equal), and the boot's ISP -> L0SP switch (old IS = 1, new IS = 0, new EL = 0). Any other bank change traps `mode` BEFORE any
        # architectural effect (the stack reads are the only side effects). Only #q operands (others trap `mode`). Flags: the new PSW.
        at("retis")
        p.append(word(CONSUME=1))                        # opcode; the specifier is byte 0 of the window
        operand(MS_OP0)
        br(C_QUICK, "retis_q")
        jmp("trap_mode")
        at("retis_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15,
          ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)                 # T1 = q
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])          # MAR <- SP
        p.extend([word()] * 2)
        bus(1, 3)                                        # T2 <- the return PC = [SP]
        p.extend([word()] * 4)
        mdr_to_temp(2)
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])   # MAR <- SP + 4
        p.extend([word()] * 2)
        bus(1, 3)                                        # T3 <- the new PSW = [SP+4]
        p.extend([word()] * 4)
        mdr_to_temp(3)
        w(ASEL=A["RT"], TADDR=1, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])         # SP + q
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=8, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)   # T4 = SP + q + 8
        # b = ((PSW >> 24) & 0x13) -> T5 (old: IS, EL), a = ((T3 >> 24) & 0x13) -> T6 (new)
        w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=24, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=0x13, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=5)
        w(ASEL=A["RT"], TADDR=3, BSEL=A["CONST"], RCONST=24, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=0x13, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=6)
        p.extend([word()] * 3)
        # case 1: a == b: no bank change (IS, EL as they are)
        w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])                    # (one temp read port: b through the result bypass)
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=6, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        p.append(word())
        p.append(word())
        br(C_ZERO, "retis_same")
        # case 2: old IS = 1 and a == 0 (new IS = 0, EL = 0): ((b >> 4) ^ 1) | a == 0
        w(ASEL=A["RT"], TADDR=5, BSEL=A["CONST"], RCONST=4, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=7)
        p.extend([word()] * 3)
        w(ASEL=A["RT"], TADDR=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=6, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])
        p.append(word())
        p.append(word())
        br(C_ZERO, "retis_isp")
        jmp("trap_mode")
        at("retis_same")
        w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)       # SP <- SP + 8 + q
        jmp("retis_fin")
        at("retis_isp")
        w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PRIV"], WR_ASEL=0, RCONST=0)        # ISP <- SP + 8 + q
        w(ASEL=A["RP"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=8)                  # T8 = L0SP
        p.extend([word()] * 3)
        w(ASEL=A["RT"], TADDR=8, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)        # SP <- L0SP
        at("retis_fin")
        w(ASEL=A["RT"], TADDR=3, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])                              # PSW <- the popped PSW
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)                                    # PC <- the popped PC
        p.extend([word()] * 3)                           # the redirect takes effect in E3
        jmp("disp")

        # ---- JSR /abs (E8 F3 a0 a1 a2 a3; short format, modm 0): SP <- SP - 4; word store of the return address (start + 6) at [SP]; PC <- the
        # little-endian word at start + 2. Other operand modes (Rn, #q / #imm, disp32[PC], everything else) trap `mode` before any effect.
        # Flags unchanged.
        at("jsr")
        p.append(word(CONSUME=1))                        # opcode; the specifier is byte 0 of the window
        # B3: the modm-0 indirect modes [d8 / d16 / d32[Rn]] (mode bytes 0x80..0xDF, byte >> 5 = 4 / 5 / 6; the table routine `mdd` returns only while T15 = 1):
        # target = the word at Rn + disp
        poll(0, MS_ZERO)
        chk_hi3(0, 4, "jsr_n1")
        w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
        jmp("jsr_ig0")
        at("jsr_n1")
        chk_hi3(0, 5, "jsr_n2")
        w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
        jmp("jsr_ig1")
        at("jsr_n2")
        chk_hi3(0, 6, "jsr_normal")
        w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
        jmp("jsr_ig2")
        at("jsr_normal")
        operand(MS_OP0)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        br(22, "jsr_rb")                                 # B3: register-based memory (d8 / d16 / d32[Rn], [Rn]): the target is its effective address
        br(C_TAG, "trap_mode" if mut.get("pl_jsr_tag_off") else "jsr_pc")                              # R-play1: disp32[PC] (was trap `mode`)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)   # T1 = the target
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=6, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)             # T0 = ipc + 6
        at("jsr_tail")
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])          # MAR <- SP - 4
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)  # SP <- SP - 4 (bypass)
        p.append(word())
        mdr_from_temp(T0)
        p.extend([word()] * 2)
        bus(2, 3)                                        # word store of the return address at [SP]
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)       # PC <- the target
        p.extend([word()] * 3)                           # the redirect takes effect in E3
        jmp("disp")
        at("jsr_rb")                                     # modm-0 register-based target; stepped modes (also tagged 23) trap; T0 = ipc + 1 + header length by the mode group
        br(23, "trap_mode")
        poll(4, MS_ZERO)
        chk_hi3(0, 0, "jsr_g1")
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=3, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # d8[Rn]: 1 + 2
        jmp("jsr_rb2")
        at("jsr_g1")
        chk_hi3(0, 1, "jsr_g2")
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # d16[Rn]: 1 + 3
        jmp("jsr_rb2")
        at("jsr_g2")
        chk_hi3(0, 2, "jsr_g3")
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=6, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # d32[Rn]: 1 + 5
        jmp("jsr_rb2")
        at("jsr_g3")
        if not mut.get("sm_jsr_no_g7_guard"):
            # SM1: group 7 (d8 / d16[PC], bit 4) is not [Rn]: its return address differs -> trap. SP3-C: group 7 goes to sp3_jsr_g7 (below), which
            # gives F0 / F1 their own return addresses (the Level 1 census executes jsr d16[PC]).
            chk_hi3(0, 3, "trap_mode" if mut.get("sp3c_jsr_off") else "sp3_jsr_g7")
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=2, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # [Rn]: 1 + 1
        jmp("jsr_rb2")
        if not mut.get("sm_jsr_no_g7_guard") and not mut.get("sp3c_jsr_off"):
            # ---- SP3-C: JSR d8[PC] / d16[PC] (E8 F0 / F1; MAME opJSR: target = ipc + sext(disp) (ReadAMAddress), return address = ipc + 1 + the specifier
            # length = ipc + 3 / ipc + 4). Only F0 / F1 (mode byte >> 3 == 0x1E; F8 / F9 [d8 / d16[PC]] keep trapping here); bit 0 = d16. The target comes from
            # jsr_rb2's DEC_EA word (descriptor base ipc, as every d8 / d16[PC] consumer).
            at("sp3_jsr_g7")
            chk_hi3(0, 0x1E, "trap_mode", sh=3)
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            p.extend([word()] * 2)
            br(C_ZERO, "sp3_jsr_p16", pol=1)
            w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=4 if mut.get("sp3c_jsr_len") else 3, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)   # d8[PC]: 1 + 2
            jmp("jsr_rb2")
            at("sp3_jsr_p16")
            w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=3 if mut.get("sp3c_jsr_len") else 4, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)   # d16[PC]: 1 + 3
            jmp("jsr_rb2")
        for g_, (isz_, dlen_) in enumerate(((0, 3), (1, 4), (2, 6))):            # d8 / d16 / d32 indirect: instruction length 3 / 4 / 6
            at(f"jsr_ig{g_}")
            p.extend([word()] * 3)
            operand(MS_OP0)                              # the lookup returns through `mdd` (T15 = 1) with mreg = the base register
            w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
            poll(4, MS_ZERO)
            w(IMM_LD=1, IMM_OFS=1, IMM_SZ=isz_, IMM_SX=1, RA_SEL=3, ASEL=A["RA"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
            w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=dlen_, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
            p.extend([word()] * 2)
            p.append(word(BUS_OP=1, BUS_SIZE=3))
            p.extend([word()] * 4)
            w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(CONSUME=dlen_ - 1)
            p.extend([word()] * 3)
            jmp("jsr_tail")
        at("jsr_rb2")
        w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=1, CONSUME=10)     # T1 = the target
        p.extend([word()] * 3)
        jmp("jsr_tail")

        # ---- PUSH #q (EE, short format, modm 0): SP <- SP - 4; word store of q (4 bits, zero-extended) at [SP]. Flags unchanged.
        # Other operands trap `mode` (EF = modm 1 is not dispatched).
        at("push")
        p.append(word(CONSUME=1))                        # opcode; the specifier is byte 0 of the window
        operand(MS_OP0)
        br(C_QUICK, "push_q")
        jmp("push_x")                                    # R-post1: was trap `mode`; push_x accepts #imm32 only
        at("push_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15,
          ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        at("push_tail")
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])          # MAR <- SP - 4
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)  # SP <- SP - 4 (bypass)
        p.append(word())                                 # T0 is readable four words after its write
        mdr_from_temp(T0)
        p.extend([word()] * 2)
        bus(2, 3)                                        # word store at [SP]
        jmp("disp")


        # ====================================================================================================================
        # B2 (CPU batch 2; production builds only). Exploratory standard (docs/ga2-bringup-roadmap.md): the S0 lock-step is the check.
        # Descriptor tags (both TEMPORARY): bit 4 (ucond 22) = register-based memory (d8/d16/d32[Rn], [Rn], [Rn+], [-Rn]); bit 11 (ucond 23) =
        # disp32[PC] (0x0F2, R4b) or, together with bit 4, a stepped mode ([Rn+] = 0xA10, [-Rn] = 0xC10). Operand sizes come from the dispatch
        # table (SIZE_SEL 0 = size1, 1 = size2; BUS_SIZE 0 = the word's size), so one routine serves b / h / w.
        # Subroutines: ea0 / ea1 (the memory operand's effective address -> MAR via DEC_EA, header consumed, [Rn+] / [-Rn] written back),
        # src0 (operand 1 value -> T0, zero-extended: register, #quick, #imm, memory). Same-register stepped operands: not supported (OI-4).
        # ====================================================================================================================
        def pad(n):
            p.extend([word()] * n)

        at("mstub0")                                      # B2: a lookup in the modm-0 table regardless of the instruction (the indexed base specifier)
        p.append(word(NXT=6, IMM_OFS=0, MODM_SEL=MS_ZERO))

        def ret(**f):
            p.append(word(NXT=4, **f))

        def ea_sub(s):
            nm = f"ea{s}"
            at(nm)
            br(22, f"{nm}_rb")
            at(f"{nm}_plain")
            poll(4, MS_ZERO)                              # the whole header (<= 5 bytes) is queued
            w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["MAR"], CONSUME=10, SIZE_SEL=s)
            ret()
            at(f"{nm}_rb")
            br(23, f"{nm}_step")
            jmp(f"{nm}_plain")
            at(f"{nm}_step")                              # [Rn+] (mode byte >> 5 = 4) or [-Rn] (5)
            chk_hi3(0, 4, f"{nm}_pre")
            w(DEC_EA=1, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["MAR"], SIZE_SEL=s)                           # post-increment: EA = Rn, then Rn += size
            w(DEC_EA=1, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGUSTEP"], WR_DST=WR["GPR"], WR_ASEL=3, SIZE_SEL=s, CONSUME=10)
            ret()
            at(f"{nm}_pre")
            chk_hi3(0, 5, f"{nm}_idx")                    # the index prefix (mode byte >> 5 = 6) shares the stepped-class tags
            w(DEC_EA=1, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGUSTEP"], WR_DST=WR["MAR"], SIZE_SEL=2 if mut.get("rp_pdec_step") else s)   # pre-decrement: EA = Rn - size
            w(DEC_EA=1, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGUSTEP"], WR_DST=WR["GPR"], WR_ASEL=3, SIZE_SEL=s, CONSUME=10)
            ret()

            # indexed: T5 = Rx << size (AGU fields, not the descriptor: the prefix's mreg is Rx), then the base specifier (modm-0 table; register-based
            # or disp32[PC] / abs; no register direct / immediate / stepped base): T6 = base + disp (DEC_EA), MAR = T6 + T5
            at(f"{nm}_idx")
            chk_hi3(0, 6, f"{nm}_dd", pad0=T15_SET if mut.get("rp_play_t15_set") else None)   # mode byte >> 5 = 0 / 1 / 2: double displacement (bank 1); R-play1: T15 is no longer set (the [d16[Rn]] base is an ordinary indirect lookup, mdd_i)
            w(RB_SEL=3, AGU_BASE=5, AGU_OFF=0, AGU_IDX=1, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=5, CONSUME=1, SIZE_SEL=s)
            operand(MS_ZERO)
            br(C_REGDIR, "trap_mode")                                  # (R-play1: no bank-0 base is register direct any more; the branch word stays, so the cycle count of every other indexed form is unchanged)
            br(C_IMM, "trap_mode")
            br(22, f"{nm}_idx_rb")
            at(f"{nm}_idx_go")
            poll(4, MS_ZERO)
            w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=6, CONSUME=10, SIZE_SEL=s)
            w(**({} if mut.get("rp_idx_no_t15_clr") else T15_CLR))     # R-post1: T15 <- 0 in the first wait word
            pad(2)
            w(ASEL=A["RT"], TADDR=6, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=5, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
            ret()
            at(f"{nm}_idx_rb")
            br(23, "trap_mode")
            jmp(f"{nm}_idx_go")
            # double displacement dd8 / dd16 / dd32 [Rn] (bank 1, mode byte 0x00..0x5F): EA = [Rn + disp1] + disp2, both displacements of the group's size.
            # W1: MAR <- Rn + disp1 (DEC_EA; the mode byte and disp1 consumed); word read of the pointer; W2a: load disp2 (DEC_EA with IMM_OFS 7 = offset 0 after
            # the consume; CONSUME 11 takes its bytes), W2b: MAR <- MDR + imm (the AGU, base MDR, offset = the latched disp2)
            at(f"{nm}_dd")
            poll(7, MS_ZERO)                              # up to 9 header bytes
            w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["MAR"], CONSUME=10, SIZE_SEL=s)
            if mut.get("rp_dd_no_t15_clr"):
                p.append(word())
            else:
                w(**T15_CLR)                              # R-post1: T15 <- 0 (set by the ea_idx check words) in the wait word
            p.append(word(BUS_OP=1, BUS_SIZE=3))
            pad(4)
            w(DEC_EA=1, IMM_LD=1, IMM_OFS=7, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=6, CONSUME=11, SIZE_SEL=s)
            w(AGU_BASE=7, AGU_OFF=1, RES_SEL=RES["AGU"], WR_DST=WR["MAR"])
            ret()

        ea_sub(0)
        ea_sub(1)

        at("src0")                                        # operand 1 (bank by ifl[6]) -> T0, zero-extended to its size
        operand(MS_B6)
        br(C_REGDIR, "src0_reg")
        br(C_QUICK, "src0_q")
        br(C_IMM, "src0_imm")
        call("ea0")
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0, SIZE_SEL=0)
        ret()
        at("src0_reg")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0, CONSUME=1)
        ret()
        at("src0_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"],
          WR_DST=WR["TEMP"], TADDR=T0)
        ret()
        at("src0_imm")
        poll(4, MS_ZERO)                                  # mode byte + up to 4 immediate bytes
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=3, SIZE_SEL=0, CONSUME=1, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        w(IMM_SZ=3, SIZE_SEL=0, CONSUME=9)
        ret()

        at("mdd")                                         # bank-0 0x80..0xDF (memory-indirect modes): a RETURN as it was only inside a movcu length lookup / jsr (T15 = 1)
        w(ASEL=A["RT"], TADDR=15, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "mdd_i")                               # R-play1: T15 = 0, an ordinary operand: [d8[Rn]] / [d16[Rn]] are memory operands (below), [d32[Rn]] traps `mode`
        p.append(word(NXT=4))

        # ---- R-play1: [d8[Rn]] / [d16[Rn]] (bank-0 mode bytes 0x80..0xBF) as ORDINARY memory operands (EA = the word at Rn + sext(disp)): the lookup routine reads the pointer P and
        # leaves MDR = P - sext(disp). The two mode-table descriptors (0x1D4 / 0x1D8: register-based memory, bit 4; AGU base 7 = MDR; displacement size 8 / 16) make every
        # consumer's ea0 / ea1 / movea DEC_EA word compute MDR + sext(disp) = P, and consume the mode byte and the displacement exactly as for d8 / d16[Rn]. Consumers that look up
        # with T15 = 1 (jsr, the movcu length) never get here and ignore the descriptor.
        def mdd_i_body(sz):
            sx = 0 if mut.get("pl_mdd_zx") else 1
            w(IMM_LD=1, IMM_OFS=1, IMM_SZ=sz, IMM_SX=sx, RA_SEL=3, ASEL=A["RA"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])      # MAR = Rn + sext(disp)
            pad(2)
            p.append(word(BUS_OP=1, BUS_SIZE=3))                       # the pointer
            pad(4)
            if mut.get("pl_mdd_nobias"):
                p.append(word())
            else:
                w(IMM_LD=1, IMM_OFS=1, IMM_SZ=sz, IMM_SX=sx, ASEL=A["IMM"], BSEL=A["MDR"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])             # MDR = P - sext(disp) (SUB is b - a)
            pad(3)
            ret()

        at("mdd_i")
        poll(4, MS_ZERO)
        chk_hi3(0, 4, "mdd_i1")
        mdd_i_body(1 if mut.get("pl_mdd_d8_as_d16") else 0)
        at("mdd_i1")
        if not mut.get("pl_mdd_d32"):
            chk_hi3(0, 5, "trap_mode" if mut.get("sm3_ipc_off") else "mpi")      # SP3-B: not [d16[Rn]] -> mpi (was trap `mode`)
        mdd_i_body(0 if mut.get("pl_mdd_d16_as_d8") else 1)

        # ---- SP3-B: [d8[PC]] / [d16[PC]] (bank-0 mode bytes F8 / F9, PC displacement indirect; MAME am1/am2 PCDisplacementIndirect8/16, upstream v60_ref.py
        # decode_am group 7 reg 0x18 / 0x19): EA = the word at ipc + sext(disp). The same scheme as [d8 / d16[Rn]] above, with the base ipc instead of Rn. F8 / F9 use
        # [d8 / d16[Rn]]'s descriptors (0x1D4 / 0x1D8, base MDR) AND its table routine `mdd` (a new routine target would add mode-table address bits: HW-001 /
        # mc_pack.keep_table_bits), so a lookup goes mdd -> mdd_i -> (not group 4 / 5) mpi: only F8 / F9 (mode byte >> 3 == 0x1F; group 6 traps here as before),
        # bit 0 selects the displacement size; the pointer P is read at ipc + sext(disp) and MDR = P - sext(disp), so every consumer's DEC_EA word yields P and
        # consumes the mode byte + displacement. Behind an index prefix the same entries serve [d8 / d16[PC]](Rx).
        # NOTE: a lookup with T15 = 1 returns from `mdd` before this point (movcu register length: a length byte F8 / F9 now reads R24 / R25 as MAME does,
        # lenop & 0x1F; it trapped before). jsr never sets T15 for group 7 (jsr_g3 traps after the pointer read).
        if not mut.get("sm3_ipc_off"):
            at("mpi")
            chk_hi3(0, 0x1F, "trap_mode", sh=3)
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])    # mode-byte bit 0: F9 = d16
            pad(2)
            br(C_ZERO, "mpi16", pol=1)
            for nm_, sz_ in (("mpi8", 0), ("mpi16", 1)):
                at(nm_)
                isz_ = (1 - sz_) if mut.get("sm3_ipc_sizeswap") else sz_
                sx_ = 0 if mut.get("sm3_ipc_zx") else 1
                base_ = dict(RA_SEL=3, ASEL=A["RA"]) if mut.get("sm3_ipc_base_rn") else dict(ASEL=A["PC"])
                w(IMM_LD=1, IMM_OFS=1, IMM_SZ=isz_, IMM_SX=sx_, BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"], **base_)   # MAR = ipc + sext(disp)
                pad(2)
                p.append(word(BUS_OP=1, BUS_SIZE=3))                   # the pointer
                pad(4)
                if mut.get("sm3_ipc_nobias"):
                    p.append(word())
                else:
                    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=isz_, IMM_SX=sx_, ASEL=A["IMM"], BSEL=A["MDR"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])   # MDR = P - sext(disp)
                pad(3)
                ret()

        # ---- C1 (compaction): ONE shared front end for the generic two-operand families. The operation is not in the routine address but in the dispatch-table
        # fmt: fmt[9] (ucond 18) = read-modify-write kind, and the operation id in (ucond 17, 19, 20) = fmt[8], fmt[10], fmt[11] = id bits 0, 1, 2:
        #   write-only (ucond 18 = 0): 0 MOV / MOVZ, 1 NOT, 2 RVBYT;   read-modify-write (ucond 18 = 1): 0 ADD, 1 SUB, 2 CMP, 3 AND, 4 OR, 5 XOR.
        # Only the one-word "execute" slots differ per operation; each slot is a small decision tree on the id bits whose leaves jump straight on.
        GF_WO = {0: ("PASSA", 0, True), 1: ("NOT", 0b0111, True), 2: (None, 0, True)}
        GF_RMW = {0: ("ADD", 0b1111, True), 1: ("SUB", 0b1111, True), 2: ("SUB", 0b1111, False), 3: ("AND", 0b0111, True), 4: ("OR", 0b0111, True), 5: ("XOR", 0b0111, True)}

        def gf_exe(kind, i):
            an, fm, wr = (GF_WO if kind == "wo" else GF_RMW)[i]
            if an is None:                                # RVBYT: a shifter-unit result, no flags
                return dict(SH_OP=5, BSEL=A["CONST"], RCONST=0, FLAG_UPD=0, SIZE_SEL=1, RES_SEL=RES["SHIFT"]), wr
            return dict(ALU_OP=ALU[an], FLAG_UPD=fm, SIZE_SEL=1, RES_SEL=RES["ALU"]), wr

        def gf_tree(kind, site, leaf):
            """emit the decision tree for one execute slot: leaf(exe, writes) -> the fields of the slot's word for that operation; every leaf word jumps to `target`"""
            tag = f"gf_{site}_{kind}"
            ids = sorted((GF_WO if kind == "wo" else GF_RMW))
            if kind == "wo":
                br(19, f"{tag}2")
                br(17, f"{tag}1")
                order = {0: f"{tag}0", 1: f"{tag}1", 2: f"{tag}2"}
                seq = [0, 1, 2]
            else:
                br(20, f"{tag}h")
                br(19, f"{tag}l23")
                br(17, f"{tag}1")
                seq = None
            def emit(i, lab):
                at(lab)
                ex, wr = gf_exe(kind, i)
                f_, tgt = leaf(ex, wr)
                jmp(tgt, **f_)
            if kind == "wo":
                for i in (0, 1, 2):
                    emit(i, f"{tag}{i}")
            else:
                emit(0, f"{tag}0")
                at(f"{tag}l23")
                br(17, f"{tag}3")
                emit(2, f"{tag}2")
                emit(3, f"{tag}3")
                emit(1, f"{tag}1")
                at(f"{tag}h")
                br(17, f"{tag}5")
                emit(4, f"{tag}4")
                emit(5, f"{tag}5")

        at("g_gen")
        p.append(word(CONSUME=2))                         # opcode + format byte
        br(C_F1, "gf_f1")
        br(C_D, "gf_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # F2 D=0: T0 <- R[fld1]
        operand(MS_B6)
        jmp("gf_dst")
        at("gf_d1")                                       # F2 D=1: source specifier -> R[fld1]
        call("src0")
        pad(3)
        br(18, "gf_d1_rmw")
        gf_tree("wo", "a", lambda ex, wr: (dict(TADDR=T0, ASEL=A["RT"], WR_DST=WR["GPR"], WR_ASEL=1, BE_MODE=0, **ex), "disp"))
        at("gf_d1_rmw")
        gf_tree("rmw", "a", lambda ex, wr: (dict(TADDR=T0, ASEL=A["RT"], RB_SEL=1, BSEL=A["RB"], BE_MODE=0, **ex, **({"WR_DST": WR["GPR"], "WR_ASEL": 1} if wr else {})), "disp"))
        at("gf_f1")                                       # F1: source -> T0, then operand 2
        call("src0")
        operand(MS_B5)
        at("gf_dst")
        br(C_REGDIR, "gf_dreg")
        br(C_IMM, "gf_imm")
        call("ea1")
        p.append(word())
        br(18, "gf_mem_rmw")
        gf_tree("wo", "b", lambda ex, wr: (dict(TADDR=T0, ASEL=A["RT"], WR_DST=WR["MDR"], **ex), "gf_wo_wr"))
        at("gf_wo_wr")
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=3 if mut.get("b2_mov_wr_word") else 0, SIZE_SEL=1))   # TEST-ONLY mutant: a word store, no lane merging
        jmp("disp")
        at("gf_mem_rmw")
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))
        pad(4)
        gf_tree("rmw", "b", lambda ex, wr: (dict(TADDR=T0, ASEL=A["RT"], BSEL=A["MDR"], **ex, **({"WR_DST": WR["MDR"]} if wr else {})), "gf_rmw_wr" if wr else "disp"))
        at("gf_rmw_wr")
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=1))
        jmp("disp")
        at("gf_dreg")
        br(18, "gf_dreg_rmw")
        gf_tree("wo", "c", lambda ex, wr: (dict(TADDR=T0, ASEL=A["RT"], WR_DST=WR["GPR"], WR_ASEL=3, BE_MODE=0, CONSUME=1, **ex), "disp"))
        at("gf_dreg_rmw")
        gf_tree("rmw", "c", lambda ex, wr: (dict(TADDR=T0, ASEL=A["RT"], RB_SEL=3, BSEL=A["RB"], BE_MODE=0, CONSUME=1, **ex, **({"WR_DST": WR["GPR"], "WR_ASEL": 3} if wr else {})), "disp"))
        # an immediate / #quick operand 2: a value only for CMP (rmw id 2; both references agree, M1.2), otherwise an immediate destination
        at("gf_imm")
        br(18, "trap_immdest", pol=1)
        br(19, "trap_immdest", pol=1)
        br(17, "trap_immdest")
        br(20, "trap_immdest")
        br(C_QUICK, "gf_dq")
        poll(4, MS_ZERO)
        exc, _ = gf_exe("rmw", 2)
        w(TADDR=T0, ASEL=A["RT"], BSEL=A["IMM"], IMM_LD=1, IMM_OFS=1, IMM_SZ=3, CONSUME=1, **exc)
        w(IMM_SZ=3, SIZE_SEL=1, CONSUME=9)
        jmp("disp")
        at("gf_dq")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        w(TADDR=T0, ASEL=A["RT"], BSEL=A["RES"], FWD_EN=1, **exc)
        jmp("disp")

        # ---- movea: register-based memory source (d8/d16/d32[Rn], [Rn]): Rn <- the effective address (DEC_EA), no bus access. Stepped modes trap.
        at("movea_rb")
        br(23, "movea_c23")
        poll(4, MS_ZERO)
        w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["GPR"], WR_ASEL=1, CONSUME=10)
        jmp("disp")
        # d8/d16/d32[Rn](Rx) (index prefix 0xC0|x in modm 1, then the base specifier in the modm-0 table): Rn <- base + disp + Rx (scale 1: movea.b).
        # [Rn+] / [-Rn] (also tagged 11 + 4) trap `mode`.
        at("movea_c23")
        chk_hi3(0, 6, "movea_dd", pad0=T15_SET if mut.get("rp_play_t15_set") else None)                        # R-post1: not an index prefix -> movea_dd (was trap `mode`); R-play1: T15 is no longer set
        w(RB_SEL=3, AGU_BASE=5, AGU_OFF=0, AGU_IDX=1, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=1, CONSUME=1, SIZE_SEL=0)    # T1 = Rx << size1 (b / h / w); the index byte is consumed
        operand(MS_ZERO)
        br(C_REGDIR, "trap_mode")                        # (R-play1: no bank-0 base is register direct; word kept for the cycle count)
        br(C_IMM, "trap_mode")
        br(22, "movea_c23_rb")                           # register-based base: stepped modes (also tagged 23) trap; abs / d[PC] bases go straight on
        jmp("movea_c23_b")
        at("movea_c23_rb")
        br(23, "trap_mode")
        at("movea_c23_b")
        poll(4, MS_ZERO)
        w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=T0, CONSUME=10)    # T0 = base + disp
        w(**({} if mut.get("rp_c23_no_t15_clr") else T15_CLR))         # R-post1: T15 <- 0 in the first wait word
        pad(2)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
        jmp("disp")
        # R-post1: MOVEA dd16[Rn], Rf (the 0x1018A2 trap): Rf <- the word at (Rn + sext disp1) + sext disp2 (MAME am2DoubleDisplacement16), through the
        # shared ea0 double-displacement path; only the 16-bit group (mode byte >> 5 = 1); dd8 / dd32 and the stepped modes still trap `mode`.
        at("movea_dd")
        w(**T15_CLR)                                     # (set by movea_c23's check words)
        if not mut.get("rp_mdd_anygroup"):
            if mut.get("rp3_dd8_off"):
                chk_hi3(0, 1, "trap_mode")                   # R-post2 behaviour: dd16 only
            elif mut.get("rp3_dd_hi"):
                chk_hi3(0, 0, "trap_mode", sh=7)             # TEST-ONLY mutant: top bit only (dd32 accepted)
            else:
                chk_hi3(0, 0, "trap_mode", sh=6)             # R-post3: mode byte >> 6 == 0, i.e. dd8 (0x00..0x1F) or dd16 (0x20..0x3F); dd32 and the stepped modes trap
        if mut.get("rp_mdd_nodref"):                     # TEST-ONLY: EA = Rn + disp1 (no pointer read, no disp2)
            poll(4, MS_ZERO)
            w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["GPR"], WR_ASEL=1, CONSUME=10)
            w(CONSUME=2)
            jmp("disp")
        else:
            call("ea0")
            pad(3)
            w(ASEL=A["MAR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
            jmp("disp")
        # ---- Bcc disp8 (60-6F except 6A = br8): taken -> ipc + sext(disp8); else the two bytes are consumed. cc = opcode[3:0] (layout 0).
        at("bcc")
        br(1, "bcc_t")
        p.append(word(CONSUME=2))
        jmp("disp")
        at("bcc_t")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=2)
        pad(3)
        jmp("disp")

        # ---- Bcc disp16 (70-7F; 7A = BR): taken -> ipc + sext(disp16); else the three bytes are consumed.
        at("bcc16")
        poll(2, MS_ZERO)
        br(1, "bcc16_t")
        p.append(word(CONSUME=3))
        jmp("disp")
        at("bcc16_t")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=1, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=3)
        pad(3)
        jmp("disp")

        # ---- BSR disp16 (48): SP <- SP - 4; word store of ipc + 3 at [SP]; PC <- ipc + sext(disp16)
        at("bsr")
        poll(2, MS_ZERO)
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=3, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=1, IMM_SX=1, CONSUME=3, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        p.append(word())
        mdr_from_temp(T0)
        pad(2)
        bus(2, 3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
        pad(3)
        jmp("disp")

        # ---- RSR (CA): PC <- [SP]; SP <- SP + 4
        at("rsr")
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"], CONSUME=1)
        pad(2)
        bus(1, 3)
        pad(4)
        mdr_to_temp(T0)
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        pad(2)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
        pad(3)
        jmp("disp")

        # ---- SHL / ROT (count = operand 1, a signed byte: positive left; value = a register operand 2): F2 D=1 only, other forms trap `mode`
        for nm_, shop_ in (("g_shl", 0), ("g_rot", 2)):
            at(nm_)
            p.append(word(CONSUME=2))
            br(C_F1, ("rot_f1" if not mut.get("p3_no_rot_f1") else "trap_mode") if nm_ == "g_rot" else
               ("shl_f1" if not (mut.get("p6_no_shl_f1") or mut.get("sm_shl_nof1")) else "trap_mode"))      # GA2Play3: ROT F1 with a memory operand 2; GA2Play6 / SM1: SHL F1 likewise (U1 merge: one routine, shl_f1)
            br(C_D, f"{nm_}_d1")
            jmp("g_shl_d0" if nm_ == "g_shl" else ("trap_mode" if mut.get("sm_rot_nod0") else "sm_rot_d0"))      # R-post1: SHL F2 D=0 (register count) -> g_shl_d0; SM1: ROT likewise -> sm_rot_d0
            at(f"{nm_}_d1")
            call("src0")
            pad(3)
            # the shifter supplies CY / OV; its Z / S are 32-bit, so S and Z come from a size-masked ALU pass of the shifted value (result bypass)
            w(RA_SEL=1, ASEL=A["RA"], TADDR=T0, BSEL=A["RT"], SH_OP=shop_, RES_SEL=RES["SHIFT"], FLAG_UPD=0b1100, SIZE_SEL=1)
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0011, SIZE_SEL=1, WR_DST=WR["GPR"], WR_ASEL=1, BE_MODE=0)
            jmp("disp")

        # ---- GA2Play3: ROT F1 <count>, <memory> (89 / 8B / 8D; 076B30 rot.h #8, 2E58[R25](R27)): operand 2 (size2) rotated by the signed count, flags as the
        # register form (CY / OV from the shifter, S / Z from the size-masked result). A register operand 2 traps `mode`, an immediate one `immdest`.
        # GA2Play6: SHL F1 <count>, <memory> (A9 / AB / AD; 10B887 shl.b #FF, 73[R20], the same routine at 105302 / 10879F / 10E6C2 for the other characters;
        # the GA2Play5 hardware freeze readout: TRAP trap_mode at 10B887) is the same routine with the logical shifter op (SH_OP 0, as g_shl_d1), emitted as its own copy.
        for f1nm_, f1op_ in (("rot_f1", 0 if mut.get("p3_rot_f1_shl") else 2), ("shl_f1", 2 if mut.get("p6_shl_f1_rot") else 1 if mut.get("p6_shl_f1_sha") else 6 if mut.get("sm_shl_f1_shr") else 0)):
            at(f1nm_)
            call("src0")
            operand(MS_B5)
            br(C_REGDIR, "trap_mode")
            br(C_IMM, "trap_immdest")
            call("ea1")
            p.append(word())
            p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))
            pad(4)
            w(ASEL=A["MDR"], TADDR=T0, BSEL=A["RT"], SH_OP=f1op_, RES_SEL=RES["SHIFT"],
              FLAG_UPD=0 if f1nm_ == "shl_f1" and mut.get("p6_shl_f1_nocyov") else 0b1100, SIZE_SEL=1)
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0 if f1nm_ == "shl_f1" and mut.get("p6_shl_f1_nosz") else 0b0011, SIZE_SEL=1, WR_DST=WR["MDR"])
            pad(2)
            if not (f1nm_ == "shl_f1" and (mut.get("p6_shl_f1_nostore") or mut.get("sm_shl_f1_nostore"))):
                p.append(word(BUS_OP=2, BUS_SIZE=3 if mut.get("p3_rot_f1_wword" if f1nm_ == "rot_f1" else "p6_shl_f1_wword") else 0, SIZE_SEL=1))
            jmp("disp")

        # ---- MOVCU.H (5A, format VIIa): copy min(len1, len2) halfwords from the first operand's address to the second's; R28 <- src + 2 i,
        # R27 <- dst + 2 i (MAME opMOVSTRUH; flags unchanged). Operands: two memory addresses (register-based / abs / d[PC] modes via ea0 / ea1), each
        # followed by a length byte; only immediate lengths (bit 7 clear) are supported, a register length traps `mode` before any effect.
        # T1 = src pointer, T3 = dst pointer, T2 / T4 = the remaining lengths; one halfword per iteration (bus read, then bus write).
        def chk_bit7(target):
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=7, SH_OP=6, RES_SEL=RES["SHIFT"])
            pad(2)
            br(C_ZERO, target, pol=1)

        def lenget(nm, k):
            """T[k] <- the length byte at the window head: an immediate (bit 7 clear), or R[byte & 31] (bit 7 set; the register is found by a
            modm-0 lookup of the byte, whose bank-0 0x80..0x9F entries run `mdd`: a RETURN only while the movcu flag T15 is 1, else trap `mode`)"""
            tag = f"{nm}_L{k}"
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=7, SH_OP=6, RES_SEL=RES["SHIFT"])
            pad(2)
            br(C_ZERO, tag + "_reg", pol=1)
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=k)
            jmp(tag + "_end")
            at(tag + "_reg")
            w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
            pad(3)
            operand(MS_ZERO)
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=k, CONSUME=1)
            w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=15)
            at(tag + "_end")

        at("mc_parse")                                   # MOVCU / MOVCD operand parsing (the sizes come from the dispatch table, so one copy serves .b and .h)
        operand(MS_B6)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        call("ea0")
        pad(3)
        w(ASEL=A["MAR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)           # T1 = src address
        poll(0, MS_ZERO)
        lenget("mcp", 2)
        operand(MS_B5)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        call("ea1")
        pad(3)
        w(ASEL=A["MAR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)           # T3 = dst address
        poll(0, MS_ZERO)
        lenget("mcp", 4)
        pad(2)
        ret()

        def movcu_routine(nm, bsz, step):
            at(nm)
            for sub_, tgt_ in ((8, f"{nm}_u"), (9, f"{nm}_d")):          # sub-opcode = byte 1 & 0x1F: 8 MOVCU, 9 MOVCD; the others (CMPC, MOVCF, MOVCS, ...) trap `opcode`
                w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=0x1F, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
                w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=sub_, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
                pad(2)
                br(C_ZERO, tgt_)
            jmp("skp_chk")                               # R-post1: was trap `opcode`; skp_chk accepts SKPCU.H (5A, sub-opcode 0x1A) only
            for down in (False, True):
                movcu_body(nm + ("_d" if down else "_u"), bsz, step, down)

        def movcu_body(nm, bsz, step, down):
            at(nm)
            p.append(word(CONSUME=2))
            call("mc_parse")                             # T1 = src address, T2 = len1, T3 = dst address, T4 = len2 (shared by the four variants)
            if down:
                # MOVCD (descending copy): len1 == len2 only (else trap `mode` before any effect). T5 / T6 = one past the last element of each
                # string; each iteration pre-decrements both, copies one element and counts T2 down. R28 / R27 <- the first elements - one step
                # (MAME opMOVSTRDH with dest = len: base + (len - dest - 1) * size).
                w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
                w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=4, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
                pad(2)
                br(C_ZERO, f"{nm}_eq")
                jmp("trap_mode")
                at(f"{nm}_eq")
                br(17, f"{nm}_hf")                        # fmt[8] (ucond 17) = halfword elements (MOVCU.H / MOVCD.H): the byte count doubles
                for hf_ in (False, True):
                    if hf_:
                        at(f"{nm}_hf")
                    for tb_, tw_ in ((1, 5), (3, 6)):
                        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
                        if hf_:
                            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=2, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
                        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=tb_, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
                        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=tw_)
                    if not hf_:
                        jmp(f"{nm}_stp")
                at(f"{nm}_stp")
                pad(3)
                at(f"{nm}_loop")
                w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
                pad(2)
                br(C_ZERO, f"{nm}_done")
                for t_ in (5, 6):
                    w(AGU_BASE=6, TADDR=t_, AGU_OFF=4, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], SIZE_SEL=0)         # pointer -= element size
                pad(2)
                mar_from_temp(5)
                p.append(word())
                p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))
                pad(4)
                mar_from_temp(6)
                pad(2)
                p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=0))
                pad(5)
                w(TADDR=2, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
                pad(3)
                jmp(f"{nm}_loop")
                at(f"{nm}_done")
                for tr_, rg_ in ((5, 28), (6, 27)):
                    w(AGU_BASE=6, TADDR=tr_, AGU_OFF=4, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], SIZE_SEL=0)    # base - one element (in place)
                    pad(3)
                    w(ASEL=A["RT"], TADDR=tr_, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=rg_)
                    pad(1)
                jmp("disp")
                return
            at(f"{nm}_loop")
            w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, f"{nm}_done")
            w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, f"{nm}_done")
            mar_from_temp(1)
            p.append(word())
            p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))             # halfword read at the source pointer
            pad(4)
            mar_from_temp(3)
            pad(2)
            p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=0))             # halfword write at the destination pointer (MDR still holds the data)
            pad(5)                                           # a temp read right after a bus word during its data_busy stall returns stale data (found B2)
            for t_ in (1, 3):
                w(AGU_BASE=6, TADDR=t_, AGU_OFF=3, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], SIZE_SEL=0)         # pointer += element size (size1 of the table)
                pad(1)
            for t_ in (2, 4):
                w(TADDR=t_, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
                pad(1)
            pad(1)
            jmp(f"{nm}_loop")
            at(f"{nm}_done")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=28)
            pad(1)
            w(ASEL=A["RT"], TADDR=3, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=27)
            jmp("disp")


        movcu_routine("movcu", 0, 0)                     # MOVCU / MOVCD, .B (58) and .H (5A): one routine, the element size is the table's size1 (fmt[8] marks .H)

        # ---- SETF #cc, <dst byte> (47, Format II): dst <- 1 if condition cc (the operand's low nibble, evaluated on the current flags) else 0.
        # cc = 2 b + k: base condition b (OV, CY, Z, CY|Z, S, true, S^OV, (S^OV)|Z), inverted when k = 1 (as the Bcc condition table). The condition value is
        # built by a binary tree on cc's bits, then the byte goes through the g_mov destination path. Only a #quick cc (others trap `mode`).
        at("g_setf")
        p.append(word(CONSUME=2))
        br(C_F1, "sf_ok1")
        if not mut.get("pl_setf_nod1"):
            br(C_D, "sf_ok1")                                # R-play1: SETF #cc, Rf (F2 D=1; 065DC4 setf #4, R14) -> the condition byte to R[fld1]
        jmp("trap_mode")
        at("sf_ok1")
        operand(MS_B6)
        br(C_QUICK, "sf_ok2")
        jmp("trap_mode")
        at("sf_ok2")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)   # T1 = cc
        pad(3)

        def sf_bit(k, target):                           # branch to `target` when bit k of T1 is set
            w(ASEL=A["RT"], TADDR=1, BSEL=A["CONST"], RCONST=1 << k, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, target, pol=1)

        sf_bit(3, "sf_b4")                               # b = cc >> 1, bits (b2 b1 b0) = cc bits 3..1
        sf_bit(2, "sf_b2")
        sf_bit(1, "sf_b1")
        # b = 0 (OV)
        br(5, "sf_one")
        jmp("sf_zero")
        at("sf_b1")                                      # b = 1 (CY)
        br(3, "sf_one")
        jmp("sf_zero")
        at("sf_b2")
        sf_bit(1, "sf_b3")
        br(2, "sf_one")                                  # b = 2 (Z)
        jmp("sf_zero")
        at("sf_b3")                                      # b = 3 (CY | Z)
        br(3, "sf_one")
        br(2, "sf_one")
        jmp("sf_zero")
        at("sf_b4")
        sf_bit(2, "sf_b6")
        sf_bit(1, "sf_b5")
        br(4, "sf_one")                                  # b = 4 (S)
        jmp("sf_zero")
        at("sf_b5")                                      # b = 5 (true)
        jmp("sf_one")
        at("sf_b6")
        sf_bit(1, "sf_b7")
        br(4, "sf_s1")                                   # b = 6 (S ^ OV)
        br(5, "sf_one")
        jmp("sf_zero")
        at("sf_s1")
        br(5, "sf_zero")
        jmp("sf_one")
        at("sf_b7")                                      # b = 7 ((S ^ OV) | Z)
        br(2, "sf_one")
        br(4, "sf_s1")
        br(5, "sf_one")
        jmp("sf_zero")
        at("sf_one")
        w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
        jmp("sf_fin")
        at("sf_zero")
        w(ASEL=A["CONST"], RCONST=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
        at("sf_fin")
        pad(3)
        w(ASEL=A["RT"], TADDR=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=2, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)          # T0 = the condition byte (0 / 1)
        pad(2)
        br(C_F1, "sf_f1_tail")                           # R-play1: the last wait word became this branch (same cycle count for F1)
        jmp("disp", TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=2 if mut.get("pl_setf_word") else 1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)     # F2 D=1: R[fld1].b <- the condition byte
        at("sf_f1_tail")
        operand(MS_B5)
        jmp("gf_dst")

        # ====================================================================================================================
        # B3 (CPU batch 3): ROTC.W, MULU.W, TEST1, XCHW (production builds only; same exploratory standard).
        # ====================================================================================================================
        # ---- ROTC.W #count, Rn (9D, F2 D=1, register destination only): rotate through carry (33 bits). count > 0: that many left rotations
        # through CY (ADDC v + v + CY: carry out = bit 31); count < 0 with |count| <= 33: 33 - |count| left rotations (the same rotation, MAME's
        # right rotate); other negative counts trap `mode`; count = 0: CY <- 0. OV <- 0, S / Z from the result, CY from the last rotation.
        at("g_rotc")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "rotc_d1")
        jmp("trap_mode")
        at("rotc_d1")
        call("src0")                                     # T0 = the count byte
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)      # T1 = the value (R[fld1])
        pad(3)
        w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "rotc_zero")
        w(IMM_LD=0, ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=7, SH_OP=6, RES_SEL=RES["SHIFT"])        # count >> 7: the sign
        pad(2)
        br(C_ZERO, "rotc_pos")                           # zero: positive
        # negative: m = 256 - count (byte NEG); iterations = 33 - m (must be >= 0)
        br(17, "rotc_negb")                              # R-post1: ROTC.B (fmt[8]): a 9-bit ring, iterations = 9 - m
        w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["NEG"], SIZE_SEL=3, RCONST=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])      # 31 - m
        at("rotc_neg2")
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=2, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])        # + 2
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)    # T2 = 33 - m
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=31, SH_OP=6, RES_SEL=RES["SHIFT"])               # sign of 33 - m
        pad(2)
        br(C_ZERO, "rotc_loop_start", pol=0)             # non-negative: go
        jmp("trap_mode")
        at("rotc_negb")
        w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["NEG"], SIZE_SEL=3, RCONST=0, RES_SEL=RES["ALU"])
        jmp("rotc_neg2", ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=7, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], SIZE_SEL=2, BE_MODE=1)      # 7 - m (+ 2 = 9 - m)
        at("rotc_pos")
        w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)    # T2 = count
        pad(3)
        at("rotc_loop_start")
        pad(2)
        at("rotc_loop")
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "rotc_fin")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["ADDC"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"],
          SIZE_SEL=2 if mut.get("rp_rotc_word") else 1)                                                                                      # T1 = 2 T1 + CY; CY <- the top bit (R-post1: size2)
        w(TADDR=2, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(4)
        jmp("rotc_loop")
        at("rotc_fin")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1, SIZE_SEL=1, BE_MODE=0)   # R <- T1; OV 0, S, Z (R-post1: size2 lanes)
        jmp("disp")
        at("rotc_zero")                                  # count 0: CY <- 0, S / Z / OV from the (unchanged) value
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b1111, RES_SEL=RES["ALU"], SIZE_SEL=1)
        jmp("disp")

        # ---- MULU.W <src>, Rn (95, F2 D=1, register destination): Rn <- low 32 bits of Rn * src (unsigned); Z, S from the low word, OV = (the high
        # word != 0); CY unchanged. Other forms trap `mode`.
        def mulu_tail(tag, rb_sel, wr_asel, consume, signed=0):
            w(ASEL=A["RT"], TADDR=T0, RB_SEL=rb_sel, BSEL=A["RB"], MD_START=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], **({"MD_SIGNED": 1} if signed else {}))
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the multiplier is busy
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
            pad(3)
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=wr_asel, CONSUME=consume)
            w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, "disp")                           # high word zero: OV stays 0
            w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
            jmp("disp")

        if _S1:
            # ==== V60 S1 (from P1.1 / P1.2): the multiply family on the generic operand machinery =====================================
            # One routine for MUL / MULU .B .H .W in every operand form (F2 D=1, F2 D=0, F1; register or memory destination). The operand handling is
            # g_gen's (src0, the specifier lookup, ea1, the memory read and gf_rmw_wr's store); one shared core does the multiply and the flags.
            # Dispatch fmt: sizes as usual; fmt[8] (ucond 17) = byte, fmt[10] (ucond 19) = word, fmt[11] (ucond 20) = signed.
            # MAME opMULB / H / W, opMULUB / UH / UW: operand 2 <- the low size bits of the product of the size-extended operands; Z S from it;
            # OV = (the product above the size) != 0 (b / h: bits 31..size of the 32-bit product; w: the high word); CY unchanged.
            at("g_mulg")
            p.append(word(CONSUME=2))
            br(C_F1, "mg_f1")
            br(C_D, "mg_d1")
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # F2 D=0: T0 = R[fld1] (the multiplier)
            operand(MS_B6)
            at("mg_dst")
            br(C_REGDIR, "mg_dreg")
            br(C_IMM, "trap_immdest")
            call("ea1")
            p.append(word())
            p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))                                                    # read operand 2
            pad(4)
            mdr_to_temp(1)
            call("ar_core")
            jmp("gf_rmw_wr", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=1, WR_DST=WR["MDR"])
            at("mg_dreg")
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            call("ar_core")
            jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            at("mg_d1")
            call("src0")
            pad(3)
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            call("ar_core")
            jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)
            at("mg_f1")
            call("src0")
            operand(MS_B5)
            jmp("mg_dst")
            # the cores: in T0 = operand 1, T1 = operand 2 (both raw); out T1 = the result (its low size bits are written by the caller), Z S OV set
            at("ar_core")
            br(18, "dv_core")                                                                                   # fmt[9]: the divide / remainder family
            at("mg_core")
            br(20, "mg_sg")
            for sg_ in (0, 1):
                if sg_:
                    at("mg_sg")
                ex_ = ALU["SEXT" if sg_ else "ZEXT"]
                w(ASEL=A["RT"], TADDR=T0, ALU_OP=ex_, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
                pad(3)
                w(ASEL=A["RT"], TADDR=1, ALU_OP=ex_, SIZE_SEL=0, RES_SEL=RES["ALU"])
                w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=sg_, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
                if not sg_:
                    jmp("mg_wait")
            at("mg_wait")
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))                                               # wait while the multiplier is busy
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
            pad(3)
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"])      # Z S from the sized result, OV <- 0
            br(19, "mg_ovw")
            br(17, "mg_ovb")
            jmp("mg_ovt", ASEL=A["RT"], TADDR=1, BSEL=A["CONST"], RCONST=16, SH_OP=6, RES_SEL=RES["SHIFT"], SIZE_SEL=2, BE_MODE=1)
            at("mg_ovb")
            jmp("mg_ovt", ASEL=A["RT"], TADDR=1, BSEL=A["CONST"], RCONST=8, SH_OP=6, RES_SEL=RES["SHIFT"], SIZE_SEL=2, BE_MODE=1)
            at("mg_ovw")
            w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            at("mg_ovt")
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, "mg_ret")
            w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])  # OV <- 1
            at("mg_ret")
            ret()
            # ---- P1.1 divide / remainder core. Dispatch fmt: fmt[9] (ucond 18) = this family, fmt[8] (ucond 17) = REM, fmt[11] (ucond 20) = signed.
            # MAME opDIVB/H/W, opDIVUB/UH/UW, opREMB/H/W, opREMUB/UH/UW: the result starts as operand 2; a nonzero divisor replaces it by the size-wide
            # quotient / remainder; signed DIV of the most negative value by -1 leaves it and sets OV; Z S from the result; OV = 0 otherwise; CY unchanged.
            # Signed DIV by -1 is done as a sized NEG: it gives the quotient and exactly that OV, without a size-dependent constant.
            at("dv_core")
            br(20, "dv_sg")
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
            pad(2)
            br(C_ZERO, "dv_keep")                                                                               # divisor 0
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
            w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=0, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            jmp("dv_wait")
            at("dv_sg")
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
            pad(2)
            br(C_ZERO, "dv_keep")
            br(17, "dv_sgo")                                                                                    # REM: no -1 case (MAME: OV = 0)
            w(BSEL=A["RT"], TADDR=T0, ALU_OP=ALU["INC"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, "dv_m1")                                                                                 # divisor == -1
            at("dv_sgo")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
            w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            at("dv_wait")
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))                                               # wait while the divider is busy
            br(17, "dv_rem")
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            jmp("dv_fl")
            at("dv_rem")
            w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            at("dv_fl")
            pad(3)
            at("dv_keep")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"])      # Z S from the sized result, OV <- 0
            ret()
            at("dv_m1")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["NEG"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
            pad(2)
            ret()

        at("g_mulu")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "mulu_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # F2 D=0: T0 = R[fld1]; the specifier is the register destination
        operand(MS_B6)
        br(C_REGDIR, "mulu_d0_reg")
        jmp("trap_mode")
        at("mulu_d0_reg")
        mulu_tail("d0", 3, 3, 1)
        at("mulu_d1")
        call("src0")
        pad(3)
        mulu_tail("d1", 1, 1, 0)

        # ---- TEST1 <bit>, <value> (87, F1): CY <- bit (op1 mod 32) of op2, Z <- not CY; other flags unchanged. Operand 2: register or memory word.
        at("g_test1")
        p.append(word(CONSUME=2))
        br(C_F1, "t1_f1")
        br(C_D, "t1_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)      # F2 D=0: the bit number is R[fld1], operand 2 the specifier
        operand(MS_B6)
        jmp("t1_lk")
        at("t1_d1")                                      # F2 D=1: the bit number is the specifier, operand 2 is R[fld1]
        call("src0")
        pad(2)
        w(ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])     # mod 32
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        jmp("t1_go")
        at("t1_f1")
        call("src0")                                     # T0 = the bit number
        operand(MS_B5)
        at("t1_lk")
        w(ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])     # mod 32 (x86 / upstream shift semantics)
        br(C_REGDIR, "t1_reg")
        br(C_IMM, "trap_mode")
        call("ea1")
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, SIZE_SEL=1)
        jmp("t1_go")
        at("t1_reg")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, CONSUME=1)
        at("t1_go")
        pad(3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, SH_OP=6, RES_SEL=RES["SHIFT"])        # value >> bit
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], FLAG_UPD=0b0001, RES_SEL=RES["ALU"])     # Z <- (bit == 0)
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["ZERO"], ALU_OP=ALU["SUB"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"])               # CY <- bit (0 - bit borrows)
        jmp("disp")

        # ---- XCHW Rf, <reg | memory> (45, F2 D=0 or 1): the register field and the specifier swap words. Other forms trap `mode`.
        # GA2Play3: XCH.B (41, 063E64 xch.b R0, 90[R20]) shares the routine (dispatch size1 = byte): the register writes are size1-masked (BE_MODE 0, SIZE_SEL 0; for
        # .W size1 = word, so every lane is written as before), so the upper bytes of the registers stay (MAME SETREG8).
        xb_wr = {} if mut.get("p3_xchb_fullwr") else dict(SIZE_SEL=0, BE_MODE=0)
        at("g_xchw")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # T0 = R[fld1]
        operand(MS_B6)
        br(C_REGDIR, "xchw_reg")
        at("xchw_mem")                                   # GA2Play8: XCH.H with a memory operand enters here (xchh_mem), T0 = R[fld1] already set
        br(C_IMM, "trap_mode")
        call("ea0")
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)             # T1 = the memory word
        w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=0))                                                  # memory <- R[fld1]
        pad(5)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1, **xb_wr)    # R[fld1] <- the old memory word
        jmp("disp")
        at("xchw_reg")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)     # T1 = R[mreg]
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1, **xb_wr)    # R[mreg] <- T0
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1, **xb_wr)               # R[fld1] <- T1
        jmp("disp")

        # ---- IRQ entry (B3): reached from the per-routine dispatch stubs (see the end of build) when the interrupt line is pending with PSW.IE set; ipc is the
        # interrupted (not yet started) instruction. As upstream s32_v60 S_EXC_PUSH1.. / MAME v60_do_irq: newpsw = PSW with EL 0, bits 29, 27, 18, 17, 16
        # cleared and 31, 28 (IS) set; the stack switches to the interrupt stack when IS was 0 (EL must be 0; IS 0 with EL != 0 traps `mode`); push the old PSW,
        # push the return PC (ipc); PC <- the word at (SBR & ~0xFFF) + 4 * (irq_vector + 0x40). NMI and IRQs at EL != 0 are not handled.
        at("irq_entry")
        p.append(word(IRQ_ACK=1))                        # latches vector = irq_vector + 0x40 as early as possible (the stub's branch word saw the request)
        pad(4)                                           # the previous instruction's writes settle
        w(ASEL=A["PSW"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=8)                          # T8 = the old PSW
        w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=24, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=0x13, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=5)    # T5 = IS << 4 | EL
        pad(3)
        w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "irq_sw")                             # IS 0, EL 0: switch to the interrupt stack
        w(ASEL=A["RT"], TADDR=5, BSEL=A["CONST"], RCONST=4, SH_OP=6, RES_SEL=RES["SHIFT"])
        pad(2)
        br(C_ZERO, "trap_mode")                          # IS 0 with EL != 0: unsupported
        jmp("irq_core")
        at("irq_sw")
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PRIV"], WR_ASEL=0, RCONST=1)               # L0SP <- SP
        w(ASEL=A["RP"], RCONST=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=6)               # T6 = ISP
        pad(3)
        w(ASEL=A["RT"], TADDR=6, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)      # SP <- ISP
        pad(4)
        at("irq_core")
        w(ASEL=A["CONST"], RCONST=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=16, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=1)      # 7 << 16
        w(ASEL=A["CONST"], RCONST=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=27, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=2)      # 5 << 27
        w(ASEL=A["CONST"], RCONST=9, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=28, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=4)      # 9 << 28
        pad(3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=2, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)                # T1 = the clear mask
        pad(3)
        w(ASEL=A["PSW"], BSEL=A["RT"], TADDR=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["PSW"], ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)   # T3 = PSW with the bits cleared
        pad(3)
        w(ASEL=A["RT"], TADDR=3, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=7)                # T7 = the new PSW
        pad(3)
        w(ASEL=A["RT"], TADDR=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])
        pad(4)
        for src_ in ("T8", "PC"):                        # push the old PSW, then the return PC (ipc)
            if src_ == "PC":
                w(ASEL=A["PC"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=9)
                pad(3)
            w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
            p.append(word())
            mdr_from_temp(8 if src_ == "T8" else 9)
            pad(2)
            bus(2, 3)
            pad(5)
        w(ASEL=A["RP"], RCONST=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=10)               # T10 = SBR
        pad(3)
        w(ASEL=A["RT"], TADDR=10, BSEL=A["CONST"], RCONST=12, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=12, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=10)   # SBR & ~0xFFF
        pad(3)
        w(AGU_BASE=6, TADDR=10, AGU_OFF=7, RES_SEL=RES["AGU"], WR_DST=WR["MAR"])                                      # + 4 * vector
        p.append(word())
        bus(1, 3)
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
        pad(3)
        jmp("disp")

        # ---- src1 (B3): operand 2 (bank by ifl[5]) as a VALUE -> T1, zero-extended to its size (the src0 body with size2 / ea1)
        at("src1")
        operand(MS_B5)
        br(C_REGDIR, "src1_reg")
        br(C_QUICK, "src1_q")
        br(C_IMM, "src1_imm")
        call("ea1")
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, SIZE_SEL=1)
        ret()
        at("src1_reg")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=1, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, CONSUME=1)
        ret()
        at("src1_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"],
          WR_DST=WR["TEMP"], TADDR=1)
        ret()
        at("src1_imm")
        poll(4, MS_ZERO)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=3, SIZE_SEL=1, CONSUME=1, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        w(IMM_SZ=3, SIZE_SEL=1, CONSUME=9)
        ret()

        # ---- UPDPSW.W (13) / UPDPSW.H (4A), F1: PSW <- (PSW & ~m) | (v & m), v = operand 1, m = operand 2 & 0xFFFFFF (W) / 0xFFFF (H) (MAME op12.hxx).
        # Only the low 24 bits move, so no stack bank changes. Other formats trap `mode`.
        for nm_, sh_ in (("g_updpsw", 0),):                                   # C1: one routine; fmt[8] (ucond 17) = the halfword form (UPDPSW.H)
            at(nm_)
            p.append(word(CONSUME=2))
            br(C_F1, nm_ + "_f1")
            jmp("trap_mode")
            at(nm_ + "_f1")
            call("src0")
            call("src1")
            br(17, nm_ + "_h")
            w(ASEL=A["ZERO"], ALU_OP=ALU["NOT"], RES_SEL=RES["ALU"])
            jmp(nm_ + "_j", SIZE_SEL=2, BE_MODE=1, ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=8, SH_OP=6, RES_SEL=RES["SHIFT"])       # 0x00FFFFFF
            at(nm_ + "_h")
            w(ASEL=A["ZERO"], ALU_OP=ALU["NOT"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=16, SH_OP=6, RES_SEL=RES["SHIFT"])                                          # 0x0000FFFF
            at(nm_ + "_j")
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)       # T2 = m & mask
            pad(3)
            w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=2, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)       # T0 = v & m'
            w(ASEL=A["PSW"], BSEL=A["RT"], TADDR=2, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["PSW"], ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)       # T3 = PSW & ~m'
            pad(3)
            w(ASEL=A["RT"], TADDR=3, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])
            pad(4)
            jmp("disp")

        # ---- SET1 (97) / CLR1 (A7) / NOT1 (B7), F1: CY <- bit n of operand 2, Z <- not CY, then the bit is set / cleared / inverted in operand 2 (register or
        # memory word). n = operand 1 (a word value, taken mod 32 by the shifter). Other formats trap `mode`. (TEST1 is the read-only sibling above.)
        for nm_, kind_ in (("g_bit", "tree"),):                   # C1: one routine; fmt[8] (ucond 17) = CLR1, fmt[10] (ucond 19) = NOT1, neither = SET1
            at(nm_)
            p.append(word(CONSUME=2))
            br(C_F1, nm_ + "_f1")
            jmp("g_bit_f2")                              # R-post1: was trap `mode`; g_bit_f2 accepts CLR1 F2 D=1 only (R-play1: and SET1 / CLR1 F2 D=0)
            at(nm_ + "_f1")
            call("src0")
            pad(2)
            w(ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])     # bit number mod 32
            operand(MS_B5)
            at(nm_ + "_lk")
            br(C_REGDIR, nm_ + "_reg")
            br(C_IMM, "trap_mode")
            call("ea1")
            p.append(word())
            p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))
            pad(4)
            w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, SIZE_SEL=1)
            jmp(nm_ + "_go")
            at(nm_ + "_reg")
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            at(nm_ + "_go")
            pad(3)
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, SH_OP=6, RES_SEL=RES["SHIFT"])                           # old >> n
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], FLAG_UPD=0b0001, RES_SEL=RES["ALU"])     # Z <- (bit == 0)
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["ZERO"], ALU_OP=ALU["SUB"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"])                # CY <- bit
            w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"])         # T0 = 1 << n
            pad(3)
            br(19, nm_ + "_nt")
            br(17, nm_ + "_cl")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            jmp(nm_ + "_t5", SIZE_SEL=2, BE_MODE=1, ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])        # SET1
            at(nm_ + "_nt")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            jmp(nm_ + "_t5", SIZE_SEL=2, BE_MODE=1, ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])       # NOT1
            at(nm_ + "_cl")                                                                                               # CLR1: old & ~mask = old ^ (old & mask)
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
            at(nm_ + "_t5")
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=5)             # T5 = the new value
            pad(3)
            # destination: the specifier was a register (mreg) or memory (MAR); the lookup descriptor still tells which
            br(C_REGDIR, nm_ + "_wreg")
            w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
            pad(2)
            p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=1))
            jmp("disp")
            at(nm_ + "_wreg")
            w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            jmp("disp")

        # ---- PUSHM (EC / ED) / POPM (E4 / E5) <mask>: general register-list push / pop, by mask iteration with the register index taken from a temp (RIDX).
        # Order as MAME op3.hxx and upstream S_PUSHM / S_POPM (they agree): PUSHM pushes PSW first when mask bit 31 is set (SP -= 4, the full PSW word), then the
        # registers R30 down to R0 for each set bit 30..0 (SP -= 4, the register word). POPM pops R0 up to R30 for each set bit 0..30 (register <- [SP], SP += 4), then,
        # for bit 31, PSW <- (PSW & 0xFFFF0000) | ([SP] & 0xFFFF), SP += 4. Mask bit 31 names the PSW, never R31 (SP), so SP is never pushed or popped. The mask is
        # a register, #quick or #imm32 operand (a memory mask traps `mode`). T0 = mask, T1 = the register counter.
        def pm_mask(t):
            p.append(word(CONSUME=1))
            operand(MS_OP0)
            br(C_QUICK, t + "_q")
            br(C_IMM, t + "_i")
            br(C_REGDIR, t + "_r")
            jmp("trap_mode")
            at(t + "_q")
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)
            jmp(t + "_go")
            at(t + "_i")
            poll(4, MS_ZERO)
            w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)
            jmp(t + "_go")
            at(t + "_r")
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0, CONSUME=1)
            at(t + "_go")

        def pm_bit(k_const, target_clear):
            """branch to target_clear when bit k_const (a constant, 0..31) of T0 is clear"""
            w(ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=k_const, SH_OP=6, RES_SEL=RES["SHIFT"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, target_clear)

        def pm_push(valw):
            w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
            valw()
            pad(2)
            bus(2, 3)
            pad(5)

        at("g_pushm")
        pm_mask("pushm")
        pad(3)
        pm_bit(31, "pushm_nopsw")
        pm_push(lambda: w(ASEL=A["PSW"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"]))
        at("pushm_nopsw")
        w(ASEL=A["CONST"], RCONST=30, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(3)
        at("pushm_loop")
        w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "pushm_next")

        def pm_regval():
            w(TADDR=1, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])                                    # rt_data <- T1 (the index)
            w(TADDR=1, RIDX=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])          # MDR <- R[T1]
        pm_push(pm_regval)
        at("pushm_next")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "pm_done")
        w(TADDR=1, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        jmp("pushm_loop")
        at("pm_done")
        jmp("disp")

        at("g_popm")
        pm_mask("popm")
        w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(3)
        at("popm_loop")
        w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "popm_next")
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        pad(2)
        bus(1, 3)
        pad(4)
        w(TADDR=1, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(TADDR=1, RIDX=1, ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"])           # R[T1] <- the popped word
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        pad(4)
        at("popm_next")
        w(ASEL=A["CONST"], RCONST=30, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "popm_psw")
        w(TADDR=1, BSEL=A["RT"], ALU_OP=ALU["INC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        jmp("popm_loop")
        at("popm_psw")
        pm_bit(31, "pm_done")
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        pad(2)
        bus(1, 3)
        pad(4)
        w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=16, SH_OP=6, RES_SEL=RES["SHIFT"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=16, SH_OP=0, RES_SEL=RES["SHIFT"])                    # PSW & 0xFFFF0000
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
        w(ASEL=A["MDR"], BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["ZEXT"], SIZE_SEL=3, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)
        pad(3)
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=3, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        pad(4)
        jmp("disp")

        # ---- TB Rn, disp16 (C7, byte 1 = 101 rrrrr; MAME opTB): if Rn == 0: PC <- start + sext(disp16) else PC <- start + 4. Flags unchanged. Other C7 sub-opcodes trap `opcode`.
        at("g_tb")
        chk_hi3(1, 5, "g_dbne")                          # R-post1: was trap `opcode`; g_dbne accepts sub-opcode 2 (DBNE) only
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "tb_taken")
        w(CONSUME=4)
        jmp("disp")
        at("tb_taken")
        w(IMM_LD=1, IMM_OFS=2, IMM_SZ=1, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=4)
        pad(3)
        jmp("disp")

        # ---- DIV.W <src>, Rn (A5, F2 D=1, register destination; MAME opDIVW): Rn <- Rn / src (signed, truncating); src = 0: Rn unchanged; Rn = 0x80000000 and src = -1:
        # Rn unchanged and OV = 1; Z, S from the result; CY unchanged. Other forms trap `mode`.
        at("g_div")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "div_d1")
        jmp("div_d0")                                    # GA2Play3: F2 D=0 with a register specifier (was trap `mode`); P1.0 R1 (release union): GA2Play3 over SP3-E sp3_divw_d0
        at("div_d1")
        call("src0")

        def div_body(sx, rs, c1):
            """the division after T0 = divisor; the dividend / destination is R[fld1] (rs 1, F2 D=1) or R[mreg] (rs 3, F2 D=0 register); c1: consume the specifier"""
            w(RA_SEL=rs, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, **({"CONSUME": 1} if c1 else {}))     # T1 = the dividend
            pad(3)
            w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, f"div_noop{sx}")                  # divisor 0
            w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=31, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=2)     # T2 = 0x80000000
            pad(3)
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=2, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, f"div_chk2{sx}")                  # dividend == 0x80000000
            at(f"div_go{sx}")
            w(ASEL=A["RT"], TADDR=0, RB_SEL=rs, BSEL=A["RB"], MD_START=1, MD_SIGNED=1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1 if mut.get("p3_div_d0_wrf") else rs)
            jmp("disp")
            at(f"div_chk2{sx}")
            w(BSEL=A["RT"], TADDR=0, ALU_OP=ALU["INC"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, f"div_ov{sx}")                    # divisor == -1
            jmp(f"div_go{sx}")
            at(f"div_ov{sx}")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"])
            pad(4)
            w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
            jmp("disp")
            at(f"div_noop{sx}")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"])
            jmp("disp")
        div_body("", 1, False)
        # GA2Play3: DIV.W Rf, Rn (F2 D=0, register specifier; 1346ED div.w R12, R0): Rn <- Rn / Rf, same rules (MAME opDIVW, F12DecodeOperands op1 = Rf, op2 = Rn).
        # A memory or immediate specifier traps `mode`.
        at("div_d0")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = the divisor R[fld1]
        operand(MS_B6)
        if not mut.get("p3_div_d0_anyspec"):
            br(C_REGDIR, "div_d0r")
            jmp("trap_mode")
        at("div_d0r")
        div_body("_d0", 1 if mut.get("p3_div_d0_rf") else 3, True)

        # ---- INC (D9/DB/DD, modm 1) and TEST (F1/F3/F5): register operand only (others trap `mode`)
        # C1: one routine for INC / DEC / TEST, the operation in the table's fmt: neither bit = INC, fmt[8] (ucond 17) = DEC, fmt[10] (ucond 19) = TEST
        at("g_i1")
        p.append(word(CONSUME=1))
        operand(MS_OP0)
        br(C_REGDIR, "gi_reg")
        br(C_IMM, "trap_immdest" if mut.get("sm_test_noimm") else "gi_imm")   # SM1: TEST #q is a read-only operand (088C9F test.b #1); INC / DEC #imm stay `immdest`
        call("ea0")                                      # memory operand: read, operate, (INC / DEC) write back
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))
        pad(4)
        br(19, "gi_mt")
        br(17, "gi_md")
        jmp("gi_wr", SIZE_SEL=0, BE_MODE=1, BSEL=A["MDR"], ALU_OP=ALU["INC"], FLAG_UPD=0b1111, RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        at("gi_md")
        jmp("gi_wr", SIZE_SEL=0, BE_MODE=1, BSEL=A["MDR"], ALU_OP=ALU["DEC"], FLAG_UPD=0b1111, RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        at("gi_mt")
        jmp("disp", SIZE_SEL=0, BE_MODE=1, ASEL=A["MDR"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b1111, RES_SEL=RES["ALU"])
        at("gi_wr")
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=0))
        jmp("disp")
        at("gi_reg")
        br(19, "gi_rt")
        br(17, "gi_rd")
        jmp("disp", RA_SEL=3, ASEL=A["RA"], RB_SEL=3, BSEL=A["RB"], ALU_OP=ALU["INC"], FLAG_UPD=0b1111, SIZE_SEL=0, RES_SEL=RES["ALU"], BE_MODE=0, CONSUME=1, WR_DST=WR["GPR"], WR_ASEL=3)
        at("gi_rd")
        jmp("disp", RA_SEL=3, ASEL=A["RA"], RB_SEL=3, BSEL=A["RB"], ALU_OP=ALU["DEC"], FLAG_UPD=0b1111, SIZE_SEL=0, RES_SEL=RES["ALU"], BE_MODE=0, CONSUME=1, WR_DST=WR["GPR"], WR_ASEL=3)
        at("gi_rt")
        jmp("disp", RA_SEL=3, ASEL=A["RA"], RB_SEL=3, BSEL=A["RB"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b1111, SIZE_SEL=0, RES_SEL=RES["ALU"], BE_MODE=0, CONSUME=1)
        if not mut.get("sm_test_noimm"):
            # SM1: TEST #q (MAME opTESTB/H/W: Z S from the value, CY = OV = 0, as the register form's PASSA); #imm and INC / DEC #imm trap `immdest`
            at("gi_imm")
            br(19, "gi_imm_t")
            jmp("trap_immdest")
            at("gi_imm_t")
            br(C_QUICK, "gi_tq")
            jmp("trap_immdest")
            at("gi_tq")
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=31 if mut.get("sm_test_mask31") else 15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0, CONSUME=1)
            pad(3)
            jmp("disp", ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], FLAG_UPD=0b1111, SIZE_SEL=0, RES_SEL=RES["ALU"], BE_MODE=0)

        # ====================================================================================================================
        # R-post1 (experiments/MC-post1/README.md; exploratory standard, MAME vectors experiments/MC-post1): the instruction forms GA2 executes after the US warning
        # screen through MAME frame ~540 that trapped (experiments/MC-post1/classify_forms.py). Every routine below is reached only from a dispatch entry
        # that trapped before or from a trap branch that is retargeted, so no form that V2 already executed changes its words or its cycle count.
        # ====================================================================================================================
        # ---- DIVUX #q, Rn (B6, F2 D=1; 130C1A divux #A, R0): the 64-bit R[n+1]:R[n] divided by q (unsigned); R[n] <- the low 32 bits of the quotient,
        # R[n+1] <- the remainder; S, Z from R[n]; OV, CY unchanged. MAME (as run here, arm64 build): q = 0 gives quotient 0 and remainder = R[n].
        # Restoring division over all 64 dividend bits (the divider's 64/32 mode is unreachable from microcode: its `wide` select needs ALU_OP 3 while a
        # divide needs ALU_OP 2). PSW CY is the loop's carry scratch; the PSW is saved in T8 and restored. Only the #q divisor (others trap `mode`); n = 31
        # (MAME would write PC) traps `mode`. T1 r, T2 hi, T3 lo, T4 q, T5 divisor, T6 count, T7 n + 1, T8 PSW.
        at("g_divux")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])     # n = format byte & 31
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=7)
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "trap_mode", pol=1)
        operand(MS_B6)
        br(C_QUICK, "dvx_q")
        jmp("trap_mode")
        at("dvx_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=5)
        w(ASEL=A["RT"], TADDR=7, BSEL=A["CONST"], RCONST=5, SH_OP=6, RES_SEL=RES["SHIFT"])                                   # (n + 1) >> 5
        pad(2)
        br(C_ZERO, "trap_mode", pol=1)                   # n = 31
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2 if mut.get("rp_dvx_hilo") else 3)   # lo = R[n]
        w(TADDR=7, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(TADDR=7, RIDX=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])                           # MDR <- R[n + 1]
        w(ASEL=A["PSW"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=8)
        w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)
        w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3 if mut.get("rp_dvx_hilo") else 2)   # hi = R[n + 1]
        w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=6, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=6)       # 64 iterations
        w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "dvx_z")                              # divisor 0
        pad(2)
        at("dvx_loop")
        for t_ in (3, 2, 1):                             # (r : hi : lo) <<= 1 through CY: lo + lo, hi + hi + CY, r + r + CY (CY <- r's bit 31 = bit 32 of 2r)
            w(ASEL=A["RT"], TADDR=t_, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=t_, ALU_OP=ALU["ADD" if t_ == 3 else "ADDC"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        br(3, "dvx_sub")                                 # bit 32 of 2r + bit set: 2r + bit > d
        w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["SUB"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"])          # r - d (compare); CY = borrow
        pad(3)
        br(3, "dvx_q0")                                  # r < d: quotient bit 0
        at("dvx_sub")
        w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])   # r <- r - d (mod 2^32)
        at("dvx_q1")
        w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=4, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)   # q <- 2q + 1
        jmp("dvx_next")
        at("dvx_q0")
        w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=4, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])       # q <- 2q
        at("dvx_next")
        w(TADDR=6, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(2)
        br(C_ZERO, "dvx_done")
        jmp("dvx_loop")
        at("dvx_z")                                      # MAME: quotient 0, remainder = R[n]
        w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)
        w(ASEL=A["RT"], TADDR=3, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(3)
        at("dvx_done")
        qt_, rt_ = (1, 4) if mut.get("rp_dvx_swap") else (4, 1)
        w(ASEL=A["RT"], TADDR=rt_, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])                    # MDR <- remainder (before the GPR write:
        w(ASEL=A["RT"], TADDR=qt_, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)        #  the B port's R0 read rule), R[n] <- quotient
        pad(3)
        w(TADDR=7, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(TADDR=7, RIDX=1, ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"])           # R[n + 1] <- remainder
        if not mut.get("rp_dvx_no_psw_restore"):
            w(ASEL=A["RT"], TADDR=8, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])                  # the PSW as it was (OV, CY)
        pad(3)
        w(ASEL=A["RT"], TADDR=rt_ if mut.get("rp_dvx_flags_rem") else qt_, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0011, RES_SEL=RES["ALU"])   # S, Z from the quotient
        jmp("disp")

        # ---- MOVS.BH / MOVS.BW / MOVS.HW <src>, Rn (0A / 0C / 1C, F2 D=1 only; 130461, 133C46, 130C6E, 1329A8, 132DC7): Rn <- the source sign-extended
        # from size1, written at size2 (MAME opMOVSBH/BW/HW). Flags unchanged. Other formats trap `mode`.
        at("g_movs")
        p.append(word(CONSUME=2))
        br(C_F1, "movs_f1")                              # GA2Play3: F1 with a memory destination (was trap `mode`)
        br(C_D, "movs_d1")
        jmp("movs_d0")                                   # R-post3: F2 D=0 (was trap `mode`), .BH / .HW only
        at("movs_d1")
        call("src0")
        pad(3)
        w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["ZEXT" if mut.get("rp_movs_zext") else "SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)
        jmp("disp")

        # ---- NEG.B / NEG.W (39 / 3D; 130401 neg.w #1, R2 (F2 D=1), 13043D neg.b #1, 3203[R25] (F1, memory)): operand 2 <- 0 - operand 1; Z, S, OV, CY as
        # SUB 0 - x (CY = result != 0, MAME opNEGB/W). F2 D=0, an F1 register or immediate operand 2 trap (`mode` / `immdest`); NEG.H (3B) stays undispatched.
        neg_f = dict(ALU_OP=ALU["NEG"], FLAG_UPD=0b0111 if mut.get("rp_neg_nocy") else 0b1111, SIZE_SEL=1, RES_SEL=RES["ALU"])
        at("g_neg")
        p.append(word(CONSUME=2))
        br(C_F1, "neg_f1")
        br(C_D, "neg_d1")
        jmp("neg_d0")                                    # R-play1: F2 D=0 (was trap `mode`)
        at("neg_d1")
        call("src0")
        pad(3)
        w(TADDR=T0, ASEL=A["RT"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1, **neg_f)
        jmp("disp")
        at("neg_f1")
        call("src0")
        operand(MS_B5)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_immdest")
        call("ea1")
        p.append(word())
        w(TADDR=T0, ASEL=A["RT"], WR_DST=WR["MDR"], **neg_f)
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=1))
        jmp("disp")

        # ---- MUL.W Rf, Rn (85, F2 D=0 with a register specifier; 132DDD mul.w R8, R2): Rn <- low 32 bits of the SIGNED product; Z, S from the low word,
        # OV = (the high word of the 64-bit two's-complement product != 0) as MAME opMULW (so any negative product sets OV); CY unchanged.
        at("g_mul")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "trap_mode" if mut.get("p3_no_mul_d1") or mut.get("sp4m_off") else "mul_d1")          # GA2Play3: F2 D=1 (063481 mul.w #14, R1; 0634D2 mul.w #A, R0), was trap `mode`; release union R7: GA2Play3 over SP4-M sp4_mul_d1
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        operand(MS_B6)
        br(C_REGDIR, "mul_d0_reg")
        jmp("trap_mode")
        at("mul_d0_reg")
        mulu_tail("mul", 3, 3, 1, signed=0 if mut.get("rp_mul_unsigned") else 1)
        at("mul_d1")                                     # GA2Play3: R[fld1] <- R[fld1] * src (signed, MAME opMULW), any source
        call("src0")
        pad(3)
        mulu_tail("muld1", 1, 1, 0, signed=0 if mut.get("p3_mul_d1_unsigned") else 1)

        # ---- DBNE Rn, disp16 (C7, byte 1 = 010 rrrrr; 130C26): Rn <- Rn - 1; if Rn != 0 and Z = 0: PC <- start + sext(disp16) else PC <- start + 4 (MAME opDBNE).
        # Flags unchanged. Reached from g_tb's sub-opcode check; the other C7 sub-opcodes still trap `opcode`.
        at("g_dbne")
        chk_hi3(1, 2, "trap")
        w(RB_SEL=1, BSEL=A["RB"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
        pad(2)
        br(C_ZERO, "dbne_fall")
        if not mut.get("rp_dbne_noz"):
            br(2, "dbne_fall")                           # Z set: no branch
        w(IMM_LD=1, IMM_OFS=2, IMM_SZ=1, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=4)
        pad(3)
        jmp("disp")
        at("dbne_fall")
        w(CONSUME=4)
        jmp("disp")

        # ---- SKPCU.H <addr>, <len>, <value> (5A, sub-opcode 0x1A, format VIIb; 132983 skpcu.h [R15], R14, #0): i = the number of leading halfwords at
        # addr equal to value (at most len); R28 <- addr + 2 i, R27 <- i, Z <- (i == len); other flags unchanged (MAME opSEARCHUH(0)). The value must be #q
        # (others trap `mode`); the length is #imm or a register (lenget). Reached from the movcu sub-opcode checks; SKPCU.B (58) and the other sub-opcodes
        # trap `opcode`. T1 pointer, T2 remaining, T3 value, T4 i.
        at("skp_chk")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=0x1F, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=0x1A, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "sch_chk", pol=1)                     # R-play1: was trap `opcode`; sch_chk accepts SCHCU.B (58, sub-opcode 0x18) only
        br(17, "skp_h")                                  # fmt[8]: the .H table entry (5A)
        jmp("trap")
        at("skp_h")
        p.append(word(CONSUME=2))
        operand(MS_B6)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        call("ea0")
        pad(3)
        w(ASEL=A["MAR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        poll(0, MS_ZERO)
        lenget("skp", 2)
        operand(MS_B5)
        br(C_QUICK, "skp_q")
        jmp("trap_mode")
        at("skp_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)
        w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)
        pad(3)
        at("skp_loop")
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "skp_done")
        mar_from_temp(1)
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))        # the halfword at the pointer
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=3, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "skp_done", pol=0 if mut.get("rp_skp_inv") else 1)   # differs: stop
        w(TADDR=4, BSEL=A["RT"], ALU_OP=ALU["INC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        w(ASEL=A["CONST"], RCONST=2, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        w(TADDR=2, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        jmp("skp_loop")
        at("skp_done")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=27 if mut.get("rp_skp_swap") else 28)
        w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=28 if mut.get("rp_skp_swap") else 27)
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0001)                            # Z <- (remaining == 0)
        jmp("disp")


        # ---- SCHCU.B <addr>, <len>, <value> (58, sub-opcode 0x18, format VIIb; 072D92 schcu.b 2E58[R25], #8, R0; 072D9C schcu.b 2E58[R25], #8, #0): i = the number of leading
        # bytes at addr different from value's low byte (at most len); R28 <- addr + i, R27 <- i, Z <- (i == len); other flags unchanged (MAME opSEARCHUB(1)). The value is #q
        # or a register; the length is #imm or a register (lenget). SCHCU.H (5A sub 0x18) and every other sub-opcode keep trapping `opcode`. T1 pointer, T2 remaining, T3 value, T4 i.
        at("sch_chk")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=0x1F, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=0x18, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "trap", pol=1)
        br(17, "trap")                                   # the .H entry (5A) with sub-opcode 0x18 is SCHCU.H
        p.append(word(CONSUME=2))
        operand(MS_B6)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_mode")
        call("ea0")
        pad(3)
        w(ASEL=A["MAR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        poll(0, MS_ZERO)
        lenget("sch", 2)
        operand(MS_B5)
        br(C_QUICK, "sch_q")
        if not mut.get("pl_sch_noreg"):
            br(C_REGDIR, "sch_r")
        if not mut.get("p3_sch_noimm"):
            br(C_IMM, "sch_i")                           # GA2Play3: #imm8 value (135367 schcu.b 95[R15], R2, #FF; was trap `mode`)
        jmp("trap_mode")
        at("sch_i")                                      # the value is a byte (SCHCU.B only): mode byte F4 + one immediate byte
        poll(2, MS_ZERO)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2 if mut.get("p3_sch_imm_t2") else 3)
        jmp("sch_init", CONSUME=1)
        at("sch_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)
        jmp("sch_init")
        at("sch_r")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3, CONSUME=1)
        at("sch_init")
        w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)
        pad(3)
        at("sch_loop")
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "sch_done")
        mar_from_temp(1)
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=0))        # the byte at the pointer
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=3, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "sch_done" if not mut.get("rp_sch_inv") else "sch_loop_x")      # equal: stop
        w(TADDR=4, BSEL=A["RT"], ALU_OP=ALU["INC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        w(ASEL=A["CONST"], RCONST=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        w(TADDR=2, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        jmp("sch_loop")
        at("sch_loop_x")
        jmp("sch_loop")
        at("sch_done")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=27 if mut.get("pl_sch_swap") else 28)
        w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=28 if mut.get("pl_sch_swap") else 27)
        w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0 if mut.get("pl_sch_noz") else 0b0001)                            # Z <- (remaining == 0)
        jmp("disp")

        # ---- PUSH #imm32 (EE F4; 133756 push #1336B6): SP <- SP - 4; word store of the immediate at [SP]. Reached from push's #q check; other modes trap `mode`.
        at("push_x")
        br(C_IMM, "push_i")
        jmp("trap_mode" if mut.get("p8_push_mem_trap") else "push_m")        # GA2Play8: memory operand -> push_m (was trap `mode`); P1.0 R5: GA2Play8 over SP3-D sp3_push_m
        at("push_i")
        poll(4, MS_ZERO)
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        w(ASEL=A["CONST"], RCONST=8 if mut.get("rp_push_sp8") else 4, BSEL=A["SP"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        p.append(word())
        mdr_from_temp(T0)
        p.extend([word()] * 2)
        bus(2, 3)
        jmp("disp")
        # ---- SP3-D: PUSH <mem> (EE, modm 0, any bank-0 memory operand; census: push [Rn], push d8[Rn]) and POP <mem> (E6, modm 0). MAME opPUSH: the operand
        # word is read (its address from the registers BEFORE SP changes), SP <- SP - 4, word store at [SP]. MAME opPOP: the word at [SP] is read, SP <- SP + 4,
        # THEN the destination address is formed (so an SP-based destination sees the new SP) and the word is stored. Flags unchanged. Immediates: push keeps
        # push_i; a pop to an immediate traps `immdest`; E7 (modm 1) stays pop_r (register only).
        if not mut.get("sp3d_push_off"):
            at("sp3_push_m")
            call("ea0")                                  # MAR <- the operand address (the specifier consumed)
            p.append(word())
            bus(1, 3)                                    # the operand word
            pad(4)
            mdr_to_temp(T0)
            jmp("push_tail")
        if not mut.get("sp3d_pop_off"):
            at("sp3_pop_m")
            p.append(word(CONSUME=1))                    # opcode; the specifier is byte 0 of the window
            operand(MS_OP0)
            br(C_REGDIR, "trap_mode")
            br(C_IMM, "trap_immdest")
            w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
            pad(2)
            bus(1, 3)                                    # the word at [SP]
            pad(4)
            mdr_to_temp(T0)
            if not mut.get("sp3d_pop_nosp"):
                w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
                w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)   # SP <- SP + 4
            pad(3)                                       # the new SP is readable by the address computation
            if not mut.get("sp3d_pop_nolookup2"):
                # the operand is looked up AGAIN now: an indirect destination ([d8 / d16[Rn]], [d8 / d16[PC]]) has its pointer read by the mode routine,
                # which leaves the bias in MDR, and the [SP] read above overwrote MDR; the second lookup also reads the pointer with the new SP (MAME's order)
                operand(MS_OP0)
            call("ea0")                                  # MAR <- the destination address (with the new SP)
            p.append(word())
            mdr_from_temp(T0)
            pad(2)
            bus(2, 3)                                    # word store
            jmp("disp")

        # ---- SHL.W Rf, Rn (AD, F2 D=0, register specifier; 132DE3 shl.w R7, R2): count = the low byte of Rf (signed: < 0 shifts right), value Rn; the same
        # shifter word and flags as the #count form (g_shl_d1). Only the .W table entry carries fmt[8]; SHL.B / .H and memory operands trap `mode`.
        at("g_shl_d0")
        br(17, "shl_d0_ok")
        jmp("trap_mode")
        at("shl_d0_ok")
        operand(MS_B6)
        br(C_REGDIR, "shl_d0_reg")
        jmp("trap_mode")
        at("shl_d0_reg")
        w(RA_SEL=3, ASEL=A["RA"], RB_SEL=0 if mut.get("rp_shl_rb0") else 1, RCONST=0, BSEL=A["RB"], SH_OP=0, RES_SEL=RES["SHIFT"], FLAG_UPD=0b1100, SIZE_SEL=1)
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0011, SIZE_SEL=1, WR_DST=WR["GPR"], WR_ASEL=3, BE_MODE=0, CONSUME=1)
        jmp("disp")

        # ---- CLR1 #q, Rf (A7, F2 D=1; 1381EA clr1 #0, R0): CY <- bit (q mod 32) of Rf, Z <- not CY, then the bit is cleared (MAME opCLR1). Reached from g_bit's
        # format check; SET1 / NOT1 F2, CLR1 F2 D=0 and a non-#q bit number trap `mode`.
        at("g_bit_f2")
        br(C_D, "bf2_dd1")
        # R-play1: SET1 / CLR1 Rf, <register | memory> (F2 D=0; 060259 set1 R1, 2C3E[R25], 060248 clr1 R1, 2C3E[R25]): the bit number is R[fld1] (mod 32), operand 2 the specifier;
        # the F1 path from its operand-2 lookup on. NOT1 stays a trap.
        if not mut.get("pl_bit_f2_not1"):
            br(19, "trap_mode")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)
        operand(MS_B6)
        if mut.get("pl_bit_f2_nomod"):
            p.append(word())
        else:
            w(ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        jmp("g_bit_lk")
        at("bf2_dd1")
        br(17, "bf2_clr")
        # SM1 / U1 merge: SET1 / NOT1 #q, Rf (06947C set1 #3, R0; 06A967 not1 #6, R0; GA2Play4 087C08 set1 #7, R7) take the generalised CLR1 path; the final
        # operation is chosen at bf2_op. GA2Play4's separate SET1 copy (bf2_set) is retired; p4_no_set1_q traps the path as sm_bit_clronly does.
        jmp("trap_mode" if (mut.get("sm_bit_clronly") or mut.get("p4_no_set1_q")) else "bf2_clr")
        at("bf2_clr")
        br(C_D, "bf2_d1")
        jmp("trap_mode")
        at("bf2_d1")
        operand(MS_B6)
        br(C_QUICK, "bf2_q")
        if not mut.get("sm_bit_noimm"):
            br(C_IMM, "bf2_imm")                          # SM1: #imm32 bit number (0896D6 clr1 #1F, R19), mod 32
        jmp("trap_mode")
        at("bf2_imm")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15 if mut.get("sm_bit_imm_mod16") else 31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)
        jmp("bf2_n")
        at("bf2_q")
        w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)
        at("bf2_n")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, SH_OP=6, RES_SEL=RES["SHIFT"])                           # old >> n
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["AND"], FLAG_UPD=0 if mut.get("p4_set1_q_noflags") else 0b0001, RES_SEL=RES["ALU"])     # Z <- (bit == 0)
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["ZERO"], ALU_OP=ALU["SUB"], FLAG_UPD=0 if mut.get("p4_set1_q_noflags") else 0b1000, RES_SEL=RES["ALU"])                # CY <- bit
        w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"])         # T0 = 1 << n
        pad(3)
        if not mut.get("sm_bit_clronly"):
            br(17, "bf2_opclr")                          # SM1: CLR1 (fmt[8]) / NOT1 (fmt[10]) / SET1 (neither): MAME opSET1 / opCLR1 / opNOT1 (same CY / Z)
            br(19, "bf2_opnot")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["AND" if mut.get("sm_set1_and") else "XOR" if mut.get("p4_set1_q_xor") else "OR"],
              RES_SEL=RES["ALU"])   # old | mask (U1: + GA2Play4's p4_set1_q_xor)
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
            jmp("disp")
            at("bf2_opnot")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["OR" if mut.get("sm_not1_or") else "XOR"], RES_SEL=RES["ALU"])     # old ^ mask
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
            jmp("disp")
            at("bf2_opclr")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        if mut.get("rp_clr1_set"):
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"])
            p.append(word())
        else:
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=0, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])               # old & ~mask
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
        jmp("disp")

        # (U1 merge: GA2Play4's SET1 #q copy `bf2_set` removed: SET1 / NOT1 / CLR1 share SpidPlay1's generalised path above.)

        # ---- MOVT.HB <src>, <mem> (19, F1 only; R-post2: movt.hb 8[R11], [R10+] (13857C, MAME frame 715) and movt.hb [R11+], [R10] (130960, frame 717);
        # MAME opMOVTHB): the halfword source is sign-extended, then truncated to a byte stored at the memory destination (the mov.b F1 memory path); the
        # ALU's TRUNC op sets OV when the discarded bits are not the sign extension. Only OV is updated (MAME vectors: S, Z, CY unchanged). F2 (either
        # direction) traps `mode`; an F1 register destination traps `mode`, an immediate destination `immdest`.
        at("g_movt")
        p.append(word(CONSUME=2))
        br(C_F1, "movt_f1")
        jmp("trap_mode")
        at("movt_f1")
        call("src0")
        operand(MS_B5)
        if not mut.get("rp_movt_nodstchk"):
            br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_immdest")
        call("ea1")
        p.append(word())
        w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["ZEXT" if mut.get("rp_movt_zext") else "SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["TRUNC"], FLAG_UPD=0 if mut.get("rp_movt_noov") else 0b1111 if mut.get("rp_movt_allflags") else 0b0100,
          SIZE_SEL=1, RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=3 if mut.get("rp_movt_word") else 0, SIZE_SEL=1))
        jmp("disp")

        # ====================================================================================================================
        # R-post3 (experiments/MC-post1/README.md; exploratory standard): the forms the R-post2 census found trapping at MAME frames 715 / 1544 / 1545. Each is
        # reached only from a dispatch entry or a trap branch that trapped before; only the formats / directions / operand kinds GA2 executes are accepted.
        # ====================================================================================================================
        # ---- MOVEA.B dd8[Rn], Rf (F2 D=1; 138599 movea.b -8[4[R11]], R11): movea_dd's group check now accepts the dd8 group (mode byte >> 6 == 0); the ea0
        # double-displacement path already handled dd8 (movz.bw). No new words.
        # ---- MOVEA.W /abs(Rx), <mem> (F1; 1371B0 movea.w 15EBAC(R0), 6[R20]): movea_f1_rb's tagged-class branch lands here. Index prefix only (byte >> 5 == 6);
        # size1 must be word; the base must be /abs (immediates, register-based, disp32[PC] and the indirect bases trap `mode`); T0 = Rx << size1, T1 = abs, T0 <- T0 + T1, then the
        # common movea_f1 destination path (any memory destination through ea1, as the B2 forms).
        at("movea_f1_idx")
        if mut.get("sm_mvf1x_wonly") or mut.get("p8_idx_size_trap"):   # TEST-ONLY mutants: the pre-GA2Play8 / pre-SM1 MOVEA.W-only check
            br(13, "trap_mode", pol=1)
        # GA2Play8 / SM1 (U1 merge: SpidPlay1's form): every size (the dispatch's size1 = the operand dimension = the index scale: movea.b 8[R10](R2), 10[R21]
        # at 06636B; 09B27E movea.b 15EBAC(R0), 6[R20]); was MOVEA.W only. GA2Play8's placeholder empty word is gone (addresses move with the merge anyway).
        chk_hi3(0, 6, "trap_mode")
        w(RB_SEL=3, AGU_BASE=5, AGU_OFF=0, AGU_IDX=1, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=T0, CONSUME=1, SIZE_SEL=1 if mut.get("rp3_idx_size2") else 0)   # T0 = Rx << size1; the prefix is consumed
        operand(MS_ZERO)
        if not mut.get("rp3_idx_anybase"):
            br(C_IMM, "trap_mode")
            br(22, "trap_mode" if mut.get("sm_mvf1x_norb") else "sm_mvf1x_rb")   # SM1: register-based bases (d8/d16/d32[Rn], [Rn]) -> DEC_EA; stepped trap
            br(C_TAG, "trap_mode" if mut.get("pl_movea_pcx_off") else "movea_f1_idx_b")                  # R-play1: disp32[PC](Rx) is accepted (the DEC_EA word below adds ipc; was trap `mode`); the word is kept for the cycle count
        if not mut.get("rp3_idx_anybase"):
            jmp("movea_f1_idx_b")
            at("sm_mvf1x_rb")                            # SM1: [Rn+] / [-Rn] bases (tagged 23 with bit 4) keep trapping
            br(23, "trap_mode")
            if not mut.get("sm_mvf1x_noind"):
                # SM1: only groups 0-3 (d8 / d16 / d32[Rn], [Rn]); the indirect [d8/d16[Rn]] bases (bit 4, base MDR) trap.
                # SP3-A (SpidPlay2 hardware freeze, movea.w F1 d16[PC](Rx), d8[Rn]): the d8 / d16[PC] bases F0 / F1 also carry bit 4 (AGU base 2 = ipc) and
                # were rejected by this top-bit test; a set top bit now goes on to sm3_mvf1x_g7, which accepts exactly them.
                chk_hi3(0, 0, "trap_mode" if mut.get("sm3_pcx_off") else "sm3_mvf1x_g7", sh=7)
        at("movea_f1_idx_b")
        poll(4, MS_ZERO)
        w(DEC_EA=1, IMM_LD=1, IMM_OFS=0, RA_SEL=3, ASEL=A["RA"], RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=1, CONSUME=10)    # T1 = abs
        pad(3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA" if mut.get("rp3_idx_noadd") else "ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])         # T0 = T1 + T0
        jmp("movea_f1_dst")
        if not mut.get("rp3_idx_anybase") and not mut.get("sm_mvf1x_noind") and not mut.get("sm3_pcx_off"):
            # SP3-A: a bit-4 base whose mode byte has the top bit set. Among bit-4 bank-0 entries only F0 / F1 (d8 / d16[PC]) are in group 7, so
            # mode byte >> 3 == 0x1E (F0..F7) accepts exactly them; the indirect bases 80..BF ([d8 / d16[Rn]]) still trap `mode`.
            # TEST-ONLY mutants: sm3_pcx_g7any = the whole group 7 (>> 5 == 7), sm3_pcx_topany = any top-bit byte (indirect bases accepted),
            # sm3_pcx_f8 = F8..FF instead of F0..F7
            # SP3-B: with F8 / F9 ([d8 / d16[PC]], bit 4) in the table the accepted set is F0 / F1 / F8 / F9 = mode byte >> 4 == 0xF (F2..F7 and FA..FF are not
            # bit-4 entries and never reach here); sm3_ipc_noidx keeps the SP3-A test, so [d8 / d16[PC]](Rx) traps in this path only
            sh_, grp_ = (3, 0x1E) if mut.get("sm3_ipc_off") or mut.get("sm3_ipc_noidx") else (4, 0xF)
            if mut.get("sm3_pcx_g7any"):
                sh_, grp_ = 5, 7
            elif mut.get("sm3_pcx_topany"):
                sh_, grp_ = 7, 1
            elif mut.get("sm3_pcx_f8"):
                sh_, grp_ = 3, 0x1F
            at("sm3_mvf1x_g7")
            chk_hi3(0, grp_, "trap_mode", sh=sh_)
            jmp("movea_f1_idx_b")

        # ---- DIVU.W Rf, Rn (B5, F2 D=0, register specifier; 13723A divu.w R0, R2): Rn <- Rn / Rf (unsigned, truncating); divisor 0: Rn unchanged; Z, S from the
        # result, OV cleared, CY unchanged (MAME opDIVUW, vectors). F1, D=1 and non-register operands trap `mode`; DIV.W is unchanged.
        at("g_divu")
        p.append(word(CONSUME=2))
        if not mut.get("rp3_divu_anyf1"):
            br(C_F1, "trap_mode")
        if not mut.get("rp3_divu_anyd"):
            br(C_D, "trap_mode" if mut.get("sp4_off") else "sp4_divu_d1")     # SP4: F2 D = 1 (was trap `mode`), see sp4_divu_d1
        operand(MS_B6)
        br(C_REGDIR, "trap_mode", pol=1)
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # T0 = the divisor Rf
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        if not mut.get("rp3_divu_noz"):
            br(C_ZERO, "divu_z")
        w(ASEL=A["RT"], TADDR=T0, RB_SEL=1 if mut.get("rp3_divu_swap") else 3, BSEL=A["RB"], MD_START=1, MD_SIGNED=1 if mut.get("rp3_divu_signed") else 0, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
        pad(5)
        here_ = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
        w(ASEL=A["MDHI" if mut.get("rp3_divu_rem") else "MDLO"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0011 if mut.get("rp3_divu_noov") else 0b0111, RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
        jmp("disp")
        at("divu_z")                                      # divisor 0: Rn unchanged, Z / S / OV from Rn
        jmp("disp", RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"], CONSUME=1)
        # ---- SP4 (SpidAudio1-4P Pocket freeze, 2026-11-18): DIVU.W <src>, Rf (B5, F2 D = 1, register destination; the game executes it when a
        # player's CONTINUE countdown expires): the source is any operand the general source routine src0 reads (#q, #imm32, register, memory), as
        # DIV.W's div_d1. MAME 0.289 op12.hxx opDIVUW: OV = 0; a nonzero divisor gives Rf <- Rf / src (unsigned, truncating), divisor 0 leaves Rf;
        # Z S from Rf afterwards; CY unchanged. Reached only from g_divu's D = 1 branch, which trapped `mode`: no executing form changes.
        if not mut.get("sp4_off"):
            at("sp4_divu_d1")
            call("src0")                                  # T0 = the divisor (source operand)
            pad(3)
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp4_noz"):
                br(C_ZERO, "sp4_divu_d1_z")
            w(ASEL=A["RT"], TADDR=T0, RB_SEL=3 if mut.get("sp4_rn") else 1, BSEL=A["RB"], MD_START=1, MD_SIGNED=1 if mut.get("sp4_signed") else 0,
              ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])     # dividend Rf (B), divisor T0 (A)
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
            w(ASEL=A["MDHI" if mut.get("sp4_rem") else "MDLO"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0011 if mut.get("sp4_noov") else 0b0111,
              RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=1)
            jmp("disp")
            at("sp4_divu_d1_z")                           # divisor 0: Rf unchanged, Z / S from it, OV 0
            jmp("disp", RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"])
        # ---- SP4-M (SP4 census, after the second Venom encounter): MUL.W <src>, Rf (85, F2 D = 1; census mul.w #q, Rf and mul.w #imm32, Rf): the source is
        # any src0 operand, as MULU.W's mulu_d1. MAME 0.289 op12.hxx opMULW: Rf <- low 32 bits of the SIGNED product; Z S from it; OV = (the high word of
        # the 64-bit product != 0); CY unchanged: mulu_tail with the signed multiplier. Reached only from g_mul's D = 1 branch, which trapped `mode`.
        if not mut.get("sp4m_off"):
            at("sp4_mul_d1")
            call("src0")                                  # T0 = the multiplier (source operand)
            pad(3)
            mulu_tail("sp4m", 3 if mut.get("sp4m_rn") else 1, 1, 0, signed=0 if mut.get("sp4m_unsigned") else 1)

        # ---- SHA.W <count>, Rf (BD, F2 D=1; 132E43 sha.w #F2, R2; GA2Play7 0605A4 sha.w #8, R1 and any count operand): the SHL.W count form with the arithmetic shifter op (SH_OP 1).
        # Positive count: left, negative: arithmetic right; flags as the shifter / size-masked pass (g_shl_d1). F1 and D=0 trap `mode` (until GA2Play7 every
        # count operand but #imm8 did too); SHA.B stays undispatched (SHA.H: g_shah).
        at("g_sha")
        p.append(word(CONSUME=2))
        if not mut.get("rp3_sha_anyf1"):
            br(C_F1, "trap_mode")
        br(C_D, "trap_mode", pol=1)
        operand(MS_B6)
        # GA2Play7: the #q count is accepted (0605A4 / 0605BC / 0605D4 sha.w #8, R1, the map-camera keyframe step of the stage-1 -> stage-2 map sequence;
        # src0's #q path gives T0 = q, 0..15, a positive = left count as MAME's (int8)(modval & 0x0F)). The trap branch is replaced by an empty word so
        # that every micro-address keeps its place (the parity note below). The sibling count operands go through the same src0 call and are accepted too:
        # a register count (src0_reg: its low byte, read as the signed byte count, as MAME's (int8) operand 1) and a memory count (a byte read); the
        # immediate-only branch becomes a branch to the next word (not a second empty word: tail,zero packing would pool two adjacent empty words and move every
        # later micro-address, flipping dispatch-table uaddr[0]). F1 (memory operand 2) and D=0 still trap `mode`; SHA.B stays undispatched.
        if mut.get("p7_sha_q_trap"):
            br(C_QUICK, "trap_mode")
        else:
            p.append(word())
        br(C_IMM, "trap_mode" if mut.get("p7_sha_regmem_trap") else "sha_count", pol=1)
        at("sha_count")
        call("src0")
        pad(4 if not mut.get("rp3_no_parity_pad") else 3)   # one wait word more than needed: keeps the address parity of the stub block, so dispatch-table uaddr[0] stays constant 1 (HW-001 caution)
        # OV: MAME (and the upstream RTL) set OV when the top `count` bits are not all the sign bit, which is the shifter unit's OV for count - 1 (the unit
        # compares count with the number of leading sign bits below the sign bit); a first pass with count - 1 (result bypass from a DEC word) updates OV only,
        # the second pass with count updates CY only, the size-masked pass S and Z.
        if not mut.get("rp3_sha_ov_native"):
            w(TADDR=T0, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"])
            w(RA_SEL=1, ASEL=A["RA"], BSEL=A["RES"], FWD_EN=1, SH_OP=1, RES_SEL=RES["SHIFT"], FLAG_UPD=0b0100, SIZE_SEL=1)
        w(RA_SEL=1, ASEL=A["RA"], TADDR=T0, BSEL=A["RT"], SH_OP=0 if mut.get("rp3_sha_shl") else 1, RES_SEL=RES["SHIFT"],
          FLAG_UPD=0b1100 if mut.get("rp3_sha_ov_native") else 0b1000, SIZE_SEL=1)
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0011, SIZE_SEL=1, WR_DST=WR["GPR"], WR_ASEL=1, BE_MODE=0)
        jmp("disp")

        # ---- MOVS.BH / MOVS.HW Rf, Rn (0A / 1C, F2 D=0, register specifier; 132E3D movs.hw R2, R2, 136D74 movs.bh R0, R0): Rn <- Rf sign-extended from size1,
        # written at size2. Only the two table entries that carry fmt[8] (ucond 17); MOVS.BW D=0, F1 and non-register operands trap `mode`.
        at("movs_d0")
        if not mut.get("rp3_movs_d0_anysize"):
            br(17, "trap_mode", pol=1)
        operand(MS_B6)
        if not mut.get("rp3_movs_d0_anyreg"):
            br(C_REGDIR, "trap_mode", pol=1)
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT" if mut.get("rp3_movs_d0_zext") else "SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        jmp("disp", ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=2 if mut.get("rp3_movs_d0_word") else 1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1 if mut.get("rp3_movs_d0_wrf") else 3, CONSUME=1)


        # ====================================================================================================================
        # R-play1 (experiments/MC-post1/README.md): the forms the MAME gameplay-entry path (frames 1-5600: coin, start, character select, stage 1, death /
        # continue) executes that still trapped after R-post3 (cls/trap_forms_5600). Each is reached only from a dispatch entry or a trap branch that trapped
        # before, or is one of the zero-cycle retargets documented in place (mdd_i, jsr/movea tag branches, the setf tail branch).
        # ====================================================================================================================
        # ---- JSR disp32[PC] (E8 F2 d32; 061458 jsr 61461[PC]): target = ipc + disp32, return address = ipc + 6 (jsr_normal's tagged-descriptor branch).
        at("jsr_pc")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["ZERO" if mut.get("pl_jsr_pc_noadd") else "PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)       # T1 = ipc + disp32
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=7 if mut.get("pl_jsr_pc_ret7") else 6, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)                                 # T0 = ipc + 6
        jmp("jsr_tail")

        # ---- JSR disp32[PC](Rx) (E9, bank 1: index prefix 0xC0|x, then the base byte 0xF2; 100FF8 jsr 102700[PC](R2)): target = ipc + disp32 + Rx (the operand dimension of
        # JSR is a byte: MAME moddim 0, scale 1), return address = ipc + 7. Anything but the index prefix / a disp32[PC] base traps `mode`.
        at("jsr_x")
        p.append(word(CONSUME=1))                        # opcode
        operand(MS_OP0)
        chk_hi3(0, 6, "trap_mode")                       # the index prefix only
        w(RB_SEL=3, AGU_BASE=5, AGU_OFF=0, AGU_IDX=1, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"], TADDR=5, CONSUME=1, SIZE_SEL=0)       # T5 = Rx (size1 = byte); the prefix is consumed
        operand(MS_ZERO)
        if not mut.get("pl_jsrx_anybase"):
            br(C_TAG, "jsr_xb")
            jmp("trap_mode")
        at("jsr_xb")
        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)       # T1 = ipc + disp32
        w(ASEL=A["PC"], BSEL=A["CONST"], RCONST=6 if mut.get("pl_jsrx_ret6") else 7, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)                                 # T0 = ipc + 7
        pad(2)
        w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA" if mut.get("pl_jsrx_noidx") else "ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])                    # T1 += Rx
        pad(3)
        jmp("jsr_tail")

        # ---- PUSH Rn (EF, bank 1; 064915 push R0): SP <- SP - 4; word store of R[n] (the value before the SP update) at [SP]. Other bank-1 operands trap `mode`.
        at("push_r")
        p.append(word(CONSUME=1))
        operand(MS_OP0)
        br(C_REGDIR, "push_rr")
        jmp("trap_mode")
        at("push_rr")
        jmp("push_tail", RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0, CONSUME=1, SIZE_SEL=2, BE_MODE=1)

        # ---- POP Rn (E7, bank 1; 06492E pop R0): R[n] <- the word at [SP]; SP <- SP + 4 first (MAME: a pop into SP leaves the popped value). Other operands trap `mode`.
        at("pop_r")
        p.append(word(CONSUME=1))
        operand(MS_OP0)
        br(C_REGDIR, "pop_rr")
        jmp("trap_mode")
        at("pop_rr")
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        pad(2)
        bus(1, 3)
        pad(4)
        mdr_to_temp(T0)
        w(ASEL=A["CONST"], RCONST=8 if mut.get("pl_pop_sp8") else 4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        if mut.get("pl_pop_nosp"):
            p.append(word())
        else:
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        pad(1)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
        jmp("disp")

        # ---- GA2Play3: MOVS F1 <src>, <memory> (0A / 0C / 1C; 064425 movs.bh #F5, 84[R20], 064435 movs.bh #A, 84[R20]: PLAYER SELECT cursor): operand 2 (size2) <- the
        # source sign-extended from size1 (MAME opMOVSBH / BW / HW, F12WriteSecondOperand). A register destination traps `mode`, an immediate one `immdest`.
        at("movs_f1")
        call("src0")
        operand(MS_B5)
        if not mut.get("p3_movs_f1_anydst"):
            br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_immdest")
        call("ea1")
        p.append(word())
        w(TADDR=T0, ASEL=A["RT"], ALU_OP=ALU["ZEXT" if mut.get("p3_movs_f1_zext") else "SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=0 if mut.get("p3_movs_f1_wr1") else 1))
        jmp("disp")

        # ---- NEG F2 D=0 (39 / 3B / 3D Rf, <register | memory>; 101DEB neg.b R0, 72[R20], 064863 neg.w R0, R0, 105719 neg.h R0, 48[R20]): operand 2 <- 0 - Rf (size2).
        at("neg_d0")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        operand(MS_B6)
        br(C_REGDIR, "neg_d0r")
        br(C_IMM, "trap_immdest")
        call("ea1")
        p.append(word())
        w(TADDR=T0, ASEL=A["RT"], WR_DST=WR["MDR"], **neg_f)
        pad(2)
        p.append(word(BUS_OP=2, BUS_SIZE=3 if mut.get("pl_neg_d0_word") else 0, SIZE_SEL=1))
        jmp("disp")
        at("neg_d0r")
        jmp("disp", RA_SEL=1, ASEL=A["RA"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1 if mut.get("pl_neg_d0_wrf") else 3, CONSUME=1, **neg_f)

        # ---- ADDC.W Rf, Rn (94, F2 D=0, register specifier; 1304D5 addc.w R3, R3): Rn <- Rn + Rf + CY; Z S OV CY (MAME ADDL with the carry in).
        at("g_addc")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        if not mut.get("pl_addc_d1"):
            br(C_D, "trap_mode")
        operand(MS_B6)
        br(C_REGDIR, "addc_r")
        jmp("trap_mode")
        at("addc_r")
        jmp("disp", RA_SEL=1, ASEL=A["RA"], RB_SEL=3, BSEL=A["RB"], ALU_OP=ALU["ADD" if mut.get("pl_addc_add") else "ADDC"], FLAG_UPD=0b1111, RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1, SIZE_SEL=2, BE_MODE=1)

        # ---- XCH.H Rf, Rn (43, F2 D=0, register specifier; 133C86 xch.h R3, R4): the low halves swap, the upper halves stay. Flags unchanged.
        at("g_xchh")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        if mut.get("p8_xchh_d1_trap"):
            br(C_D, "trap_mode")
        else:
            p.append(word())                             # GA2Play8: D=1 accepted (the exchange is symmetric); an empty word keeps the layout
        operand(MS_B6)
        br(C_REGDIR, "xchh_r")
        jmp("trap_mode" if mut.get("p8_xchh_mem_trap") else "xchh_mem")      # GA2Play8: memory operand -> xchh_mem (was trap `mode`)
        at("xchh_r")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(2)
        # SP3-F: the B bus of these two PASSA words is unused; its default select (0 = RA, RA_SEL 0 = R0) made v2_sim_checks report a read of R0 while the
        # first word's write of Rn = R0 was pending (xch.h R1, R0, Level 1 census). Not a datapath defect (PASSA ignores B); B now selects the constant zero (15).
        xb_ = {} if mut.get("sp3f_xchh_bra") else dict(BSEL=A["ZERO"])
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=2 if mut.get("pl_xchh_word") else 1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=3, **xb_)
        jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], SIZE_SEL=2 if mut.get("pl_xchh_word") else 1, BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1, CONSUME=1, **xb_)

        # ---- MUL.B (81) / MUL.H (83): operand 2 <- the low byte / halfword of the SIGNED product, Z S from it, OV = (the 32-bit product >> 8 / 16) != 0 (so any negative
        # product sets OV, MAME opMULB / opMULH), CY unchanged. F2 D=0 with a register specifier (064290 mul.h R1, R0, 064C1A mul.b R1, R0) and F2 D=1 with any source
        # (130D9B mul.h #3, R4). fmt[8] (ucond 17) = the byte entry. F1 and D=0 non-register operands trap `mode`.
        at("g_mulhb")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "mulhb_d1")
        mext = ALU["ZEXT" if mut.get("pl_mul_zx") else "SEXT"]
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=mext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = sext(Rf)
        operand(MS_B6)
        br(C_REGDIR, "mulhb_d0r")
        jmp("trap_mode")
        at("mulhb_d0r")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=mext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        jmp("mulhb_w")
        at("mulhb_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=mext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=mext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        at("mulhb_w")
        pad(5)
        here_ = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the multiplier is busy
        w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(3)
        br(C_D, "mulhb_wd1")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=2 if mut.get("pl_mul_wsize") else 1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1 if mut.get("pl_mul_d0_wrf") else 3, CONSUME=1)
        jmp("mulhb_ov")
        at("mulhb_wd1")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)
        at("mulhb_ov")
        br(17, "mulhb_ovb")
        jmp("mulhb_ovt", ASEL=A["RT"], TADDR=1, BSEL=A["CONST"], RCONST=8 if mut.get("pl_mul_ovb8") else 16, SH_OP=6, RES_SEL=RES["SHIFT"], SIZE_SEL=2, BE_MODE=1)
        at("mulhb_ovb")
        w(ASEL=A["RT"], TADDR=1, BSEL=A["CONST"], RCONST=16 if mut.get("pl_mul_ovb16") else 8, SH_OP=6, RES_SEL=RES["SHIFT"])
        at("mulhb_ovt")
        w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "disp") if not mut.get("pl_mul_noov") else jmp("disp")
        w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
        jmp("disp")

        # ---- DIV.B (A1) / DIV.H (A3) / REM.B (50) <src>, Rf (F2 D=1; 0649CA div.b #A, R1, 063F74 div.h #50, R0, 0649D8 rem.b #A, R0): signed, truncating, the size-wide
        # quotient / remainder written to R[fld1]'s low byte / halfword; Z S from it, CY unchanged. DIV: divisor 0: Rf unchanged, OV = 0; the most negative value / -1: Rf
        # unchanged, OV = 1 (MAME). REM: divisor 0: Rf unchanged; OV = 0. fmt[8] (ucond 17) = REM. Other formats trap `mode`.
        at("g_divhb")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "dvhb_d1")
        jmp("trap_mode" if mut.get("p8_divhb_d0_trap") or mut.get("sp3e_d0_off") or mut.get("sp3e_off") else "sp3_dvs_d0")   # P1.0 R2 (release union): SP3-E sp3_dvs_d0 over GA2Play8 dvhb_d0
        at("dvhb_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        if not mut.get("pl_dv_noz"):
            br(C_ZERO, "dvhb_z")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=0 if mut.get("pl_dv_unsigned") else 1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
        pad(5)
        here_ = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
        w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
        pad(3)
        if not mut.get("pl_dv_remq"):
            br(17, "dvhb_rem")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["SEXT"], SIZE_SEL=1, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        if not mut.get("pl_dv_nochk"):
            br(C_ZERO, "dvhb_ov", pol=1)                     # the quotient does not fit the size: -MIN / -1
        jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)
        at("dvhb_rem")
        jmp("disp", ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)
        at("dvhb_ov")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"])
        pad(3)
        if not mut.get("pl_dv_noovset"):
            w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
        jmp("disp")
        at("dvhb_z")
        jmp("disp", RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"])

        # ---- GA2Play4: MULU.H (93) <src>, Rf (F2 D=1, any source; 13404A mulu.h 6C[R20], R2: the stage-1 boss health bar) and Rf, Rn (F2 D=0, register specifier):
        # operand 2 <- the low halfword of the UNSIGNED product of the two halfwords; Z S from it, OV = (the 32-bit product >> 16) != 0, CY unchanged (MAME opMULUH;
        # vectors). The MUL.H path (g_mulhb) with zero-extension and the unsigned multiplier; the write / OV tail is MUL.H's (dispatch fmt[8] = 0: halfword).
        # F1 and a D=0 non-register specifier trap `mode`.
        muext = ALU["SEXT" if mut.get("p4_muluh_sext") else "ZEXT"]
        musg = 1 if mut.get("p4_muluh_signed") else 0
        at("g_muluh")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "muluh_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=muext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = zext(Rf)
        operand(MS_B6)
        br(C_REGDIR, "muluh_d0r")
        jmp("trap_mode")
        at("muluh_d0r")
        w(RA_SEL=1 if mut.get("p4_muluh_d0_rf") else 3, ASEL=A["RA"], ALU_OP=muext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=musg, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        jmp("mulhb_w")
        at("muluh_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=muext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=muext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=musg, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        jmp("mulhb_w")

        # ---- GA2Play4: DIVU.H (B3) <src>, Rf (F2 D=1, any source; 134045 divu.h 10C[R20], R2: the stage-1 boss health bar) and Rf, Rn (F2 D=0, register specifier;
        # 065699 divu.h R6, R5: end of stage 1): operand 2 <- its halfword / the halfword divisor, UNSIGNED, truncating; written as a halfword (upper register bytes kept);
        # Z S from it, OV = 0, CY unchanged; divisor 0: operand 2 unchanged, Z S from it, OV = 0 (MAME opDIVUH; vectors). F1 and a D=0 non-register specifier trap `mode`.
        def dvuh_body(sx, rs, c1, rem=False):
            pad(3)
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("p4_divuh_noz"):
                br(C_ZERO, f"dvuh_z{sx}")
            w(RA_SEL=rs, ASEL=A["RA"], ALU_OP=ALU["SEXT" if mut.get("p4_divuh_signed") else "ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
            w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=1 if mut.get("p4_divuh_signed") else 0, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
            w(ASEL=A["MDHI" if (mut.get("p4_divuh_rem") if not rem else not mut.get("p8_remub_quot")) else "MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            pad(3)
            jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=2 if mut.get("p4_divuh_word") else 1, RES_SEL=RES["ALU"], BE_MODE=0,
                WR_DST=WR["GPR"], WR_ASEL=rs, **({"CONSUME": 1} if c1 else {}))
            at(f"dvuh_z{sx}")
            jmp("disp", RA_SEL=rs, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], **({"CONSUME": 1} if c1 else {}))
        at("g_divuh")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "dvuh_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = the divisor zext(Rf)
        operand(MS_B6)
        br(C_REGDIR, "dvuh_d0r")
        jmp("trap_mode")
        at("dvuh_d0r")
        dvuh_body("_d0", 1 if mut.get("p4_divuh_d0_rf") else 3, True)
        at("dvuh_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        dvuh_body("_d1", 1, False)

        # ---- SHA.H <count>, Rf (BB, F2 D=1; 130D9E sha.h #FE, R4, 063FBB sha.h #2, R1) and SHA.H #imm8, <mem> (F1; 073F9A sha.h #FF, 48[R20]): the SHA.W scheme (g_sha) on the
        # halfword: T0 = count (any src0 operand), T1 = the value; OV from a pass with count - 1, CY from the pass with count, Z S from the size-masked result.
        at("g_shah")
        p.append(word(CONSUME=2))
        br(C_F1, "shah_f1")
        br(C_D, "shah_d1")
        jmp("trap_mode")
        at("shah_d1")
        call("src0")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["SEXT"], SIZE_SEL=1, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        pad(3)
        at("shah_core")
        w(TADDR=T0, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=T0, SH_OP=1, RES_SEL=RES["SHIFT"], FLAG_UPD=0 if mut.get("pl_shah_noov") else 0b0100, SIZE_SEL=1)
        w(TADDR=T0, BSEL=A["RT"], ALU_OP=ALU["INC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=T0, SH_OP=0 if mut.get("pl_shah_shl") else 1, RES_SEL=RES["SHIFT"], FLAG_UPD=0b1000, SIZE_SEL=1, WR_DST=WR["TEMP"])
        pad(3)
        br(C_F1, "shah_mem")
        jmp("disp", ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0011, SIZE_SEL=2 if mut.get("pl_shah_word") else 1, WR_DST=WR["GPR"], WR_ASEL=1, BE_MODE=0)
        at("shah_mem")
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0011, SIZE_SEL=1, WR_DST=WR["MDR"])
        pad(2)
        if not mut.get("pl_shah_nostore"):
            p.append(word(BUS_OP=2, BUS_SIZE=0, SIZE_SEL=1))
        jmp("disp")
        at("shah_f1")
        call("src0")
        operand(MS_B5)
        br(C_REGDIR, "trap_mode")
        br(C_IMM, "trap_immdest")
        call("ea1")
        p.append(word())
        p.append(word(BUS_OP=1, BUS_SIZE=0, SIZE_SEL=1))
        pad(4)
        w(ASEL=A["MDR"], ALU_OP=ALU["SEXT"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1, SIZE_SEL=1)
        pad(3)
        jmp("shah_core")

        # ---- GA2Play8: DIV.B (A1) / DIV.H (A3) / REM.B (50) Rf, Rn (F2 D=0, register specifier; 0606AF div.h R0, R1: the stage-2 -> stage-3 map script, the GA2Play7
        # hardware freeze readout P0606AF TRP U618): the divisor is Rf, the dividend and the destination Rn; otherwise the D=1 routine word for word (signed,
        # truncating, size-wide quotient / remainder, Z S from it, CY unchanged; divisor 0: Rn unchanged, OV 0; the most negative value / -1: Rn unchanged, OV 1).
        # A D=0 non-register specifier traps `mode`. Emitted here, after every existing routine, and entered through the existing one-word jump in g_divhb.
        drs = 1 if mut.get("p8_divhb_d0_rf") else 3                 # the dividend register (Rn)
        dws = 1 if mut.get("p8_divhb_d0_wrf") else 3                # the destination register (Rn)
        at("dvhb_d0")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = the divisor sext(Rf)
        operand(MS_B6)
        br(C_REGDIR, "dvhb_d0r")
        jmp("trap_mode")
        at("dvhb_d0r")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        if not mut.get("p8_divhb_d0_noz"):
            br(C_ZERO, "dvhb_z0")
        w(RA_SEL=drs, ASEL=A["RA"], ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
        pad(5)
        here_ = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
        w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
        pad(3)
        br(17, "dvhb_rem0")
        w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["SEXT"], SIZE_SEL=1, RES_SEL=RES["ALU"])
        w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
        pad(2)
        br(C_ZERO, "dvhb_ov0", pol=1)                      # the quotient does not fit the size: -MIN / -1
        jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=dws, CONSUME=1)
        at("dvhb_rem0")
        jmp("disp", ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=dws, CONSUME=1)
        at("dvhb_ov0")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"])
        pad(3)
        w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
        jmp("disp", CONSUME=1)
        at("dvhb_z0")
        jmp("disp", RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], CONSUME=1)

        # ---- GA2Play8 (static coverage of the code reachable from everything MAME executed): XCH.H Rf, <memory> (43, F2 D=0 / D=1; 064D3D xch.h R13, 16[R20],
        # 064D41 xch.h R14, 8E[R20], 134EF0 xch.h R7, 24[R20]): T0 = R[fld1], then XCH.W's memory path (sizes from the dispatch entry: halfword).
        at("xchh_mem")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # T0 = R[fld1]
        jmp("xchw_mem")

        # ---- GA2Play8: PUSH <memory> (EE, bank 0; 0742A0 push 34[R20]): the word at the operand is read first, then SP <- SP - 4 and the word stored at [SP]
        # (MAME opPUSH: ReadAM, then the stack write).
        at("push_m")
        call("ea0")
        p.append(word())
        bus(1, 3)                                        # word read at the operand
        pad(4)
        mdr_to_temp(T0)
        pad(1)
        jmp("push_tail")

        # ---- GA2Play8: POP <memory> (E6, bank 0; 0742B2 pop 34[R20]): the word at [SP] is read, SP <- SP + 4, then the word is stored at the operand (its
        # address computed after the SP update, as MAME opPOP). An immediate operand traps `immdest`.
        at("pop_m")
        p.append(word(CONSUME=1))
        operand(MS_OP0)
        br(C_IMM, "trap_immdest")
        w(ASEL=A["SP"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        pad(2)
        bus(1, 3)
        pad(4)
        mdr_to_temp(T0)
        w(ASEL=A["CONST"], RCONST=4, BSEL=A["SP"], ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        if not mut.get("p8_pop_m_nosp"):
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=0, RCONST=31)
        else:
            p.append(word())
        pad(1)
        operand(MS_OP0)                                  # the specifier is looked up again (the stack read above used the bus): ea0 decodes it
        call("ea0")
        p.append(word())
        mdr_from_temp(T0)
        p.extend([word()] * 2)
        bus(2, 3 if not mut.get("p8_pop_m_byte") else 0)        # word store at the operand
        jmp("disp")

        # ---- GA2Play8: REMU.B (51) <src>, Rf (F2 D=1, any source; 130C40 remu.b #A, R1) and Rf, Rn (F2 D=0, register): DIVU.B's routine (g_divuh, dispatched
        # with byte sizes, 130C3D divu.b #A, R0) with the remainder written. Divisor 0: the operand unchanged, Z S from it, OV 0.
        at("g_remub")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "remub_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = the divisor zext(Rf)
        operand(MS_B6)
        br(C_REGDIR, "remub_d0r")
        jmp("trap_mode")
        at("remub_d0r")
        dvuh_body("_rd0", 3, True, rem=True)
        at("remub_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        dvuh_body("_rd1", 1, False, rem=True)

        # ====================================================================================================================
        # SM1 (Spider-Man, sm/spidman): the unsigned twins of MUL.B / MUL.H and REM.B, appended here so that no earlier routine changes.
        # SHA.B needs no routine: g_shah takes both sizes from the dispatch format (dtab[0xB9] below).
        # `sm_*` knobs are TEST-ONLY mutants (experiments/SM1-spidman/sm_mutants.py); never set by default.
        # ====================================================================================================================
        # ---- MULU.B (91) / MULU.H (93): MAME opMULUB / opMULUH: operand 2 <- the low byte / halfword of the UNSIGNED product of the zero-extended operands,
        # Z S from it, OV = (product >> 8 / 16) != 0, CY unchanged. F2 D=0 with a register specifier (mulu.h R2, R2) and F2 D=1 with any source
        # (mulu.h #384, R0; mulu.b #5, R10). fmt[8] (ucond 17) = byte. F1 / D=0 non-register trap `mode`. The tail (wait, write, OV) is g_mulhb's.
        uext = ALU["SEXT" if mut.get("sm_mulu_sext") else "ZEXT"]
        usg = 1 if mut.get("sm_mulu_signed") else 0
        at("g_muluhb")
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "muluhb_d1")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=uext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)     # T0 = zext(Rf)
        operand(MS_B6)
        br(C_REGDIR, "muluhb_d0r")
        jmp("trap_mode")
        at("muluhb_d0r")
        w(RA_SEL=3, ASEL=A["RA"], ALU_OP=uext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=usg, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        jmp("mulhb_w")
        at("muluhb_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=uext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=uext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=usg, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
        jmp("mulhb_w")

        # (U1 merge: SpidPlay1's REMU.B D=1 routine `g_remub` removed; GA2Play8's g_remub, D=1 any source and D=0 register, serves opcode 51.)
        # ---- REMU.B (51) <src>, Rf (F2 D=1; remu.b #3, R0): MAME opREMUB: OV = 0; if the divisor (byte) is nonzero, Rf's low byte <- (unsigned) Rf.b % divisor;
        # Z S from Rf's low byte either way; CY unchanged. Other formats trap `mode`. Divisor 0 and the remainder write are g_divhb's (dvhb_z, dvhb_rem).
        rext = ALU["SEXT" if mut.get("sm_remu_sext") else "ZEXT"]
        at("g_remub_sp")   # release union: SP3's REMU routine, renamed (P1.0 kept it beside GA2Play8's g_remub)
        p.append(word(CONSUME=2))
        br(C_F1, "trap_mode")
        br(C_D, "rmub_d1")
        jmp("trap_mode" if mut.get("sp3e_d0_off") or mut.get("sp3e_off") else "sp3_dvu_d0")                # SP3-E: F2 D = 0 with a register operand (was trap `mode`)
        at("rmub_d1")
        call("src0")
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=rext, SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
        pad(3)
        w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
        pad(2)
        if not mut.get("sm_remu_noz"):
            br(C_ZERO, "dvhb_z")
        w(RA_SEL=1, ASEL=A["RA"], ALU_OP=rext, SIZE_SEL=0, RES_SEL=RES["ALU"])
        w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=1 if mut.get("sm_remu_signed") else 0, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
        pad(5)
        here_ = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))     # wait while the divider is busy
        w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
        w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
        pad(3)
        if mut.get("sm_remu_quot"):                      # TEST-ONLY mutant: writes the quotient
            jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)
        else:
            jmp("dvhb_rem")

        # ====================================================================================================================
        # SP3-E (Level 1 census; MAME 0.289 op12.hxx opDIVB / H / W, opDIVUB / UH, opREMUB / UH, opDIVX): divide / remainder forms the census executes that trapped.
        # Every routine is reached only from a dispatch entry that trapped (B1 / B3 / 53 / A6) or from a D = 0 branch that trapped (g_div, g_divhb, g_remub), so
        # no form that already executed changes its words or its cycle count.
        # ====================================================================================================================
        if not mut.get("sp3e_off"):
            dsg_ = 1 if mut.get("sp3e_dvu_signed") else 0
            # ---- DIVU.B (B1) / DIVU.H (B3) <src>, Rf (F2 D = 1; census divu.h #q / #imm16, Rf, divu.b #q, Rf): OV = 0; a nonzero divisor (size) gives
            # Rf.size <- Rf.size / divisor (unsigned), the upper bits kept; Z S from Rf.size either way; CY unchanged. g_remub's words, writing the quotient.
            at("g_divub")
            p.append(word(CONSUME=2))
            br(C_F1, "trap_mode")
            br(C_D, "dvub_d1")
            jmp("sp3_dvu_d0")
            at("dvub_d1")
            call("src0")
            pad(3)
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
            pad(3)
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp3e_noz"):
                br(C_ZERO, "dvhb_z")                     # divisor 0: Rf unchanged, Z S from it, OV 0
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])
            w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=dsg_, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
            pad(3)
            jmp("disp", ASEL=A["RT"], TADDR=2 if mut.get("sp3e_dvu_rem") else 1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=1)

            # ---- unsigned F2 D = 0 Rf, Rn (register specifier; census divu.h Rf, Rn): divisor = Rf.size, dividend / destination = Rn.size; fmt[8] (ucond 17) = REMU
            # (remu.b / remu.h through g_remub). Memory operands trap `mode`.
            at("sp3_dvu_d0")
            operand(MS_B6)
            br(C_REGDIR, "dvu0_r")
            jmp("trap_mode")
            at("dvu0_r")
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # T0 = the divisor Rf
            pad(3)
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp3e_noz"):
                br(C_ZERO, "dvu0_z")
            w(RA_SEL=1 if mut.get("sp3e_d0_swap") else 3, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])   # the dividend Rn
            w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=dsg_, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
            pad(3)
            br(17, "dvu0_rem")
            jmp("disp", ASEL=A["RT"], TADDR=2 if mut.get("sp3e_dvu_rem") else 1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            at("dvu0_rem")
            jmp("disp", ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            at("dvu0_z")
            jmp("disp", RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], CONSUME=1)

            # ---- signed F2 D = 0 Rf, Rn for DIV.B / DIV.H / REM.B (g_divhb; census div.h Rf, Rn): dvhb's words with divisor = Rf, dividend / destination = Rn.
            # MAME opDIVH: OV = (Rn.h == 0x8000 && Rf.h == 0xFFFF); divisor nonzero and no OV: Rn.h <- Rn.h / Rf.h; Z S from Rn.h either way; CY unchanged.
            at("sp3_dvs_d0")
            operand(MS_B6)
            br(C_REGDIR, "dvs0_r")
            jmp("trap_mode")
            at("dvs0_r")
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)      # T0 = sext(Rf)
            pad(3)
            w(ASEL=A["RT"], TADDR=T0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp3e_noz"):
                br(C_ZERO, "dvs0_z")
            w(RA_SEL=1 if mut.get("sp3e_d0_swap") else 3, ASEL=A["RA"], ALU_OP=ALU["SEXT"], SIZE_SEL=0, RES_SEL=RES["ALU"])  # sext(Rn)
            w(ASEL=A["RT"], TADDR=T0, BSEL=A["RES"], FWD_EN=1, MD_START=1, MD_SIGNED=0 if mut.get("sp3e_dvs_unsigned") else 1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))
            w(ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(ASEL=A["MDHI"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)
            pad(3)
            br(17, "dvs0_rem")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["SEXT"], SIZE_SEL=1, RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp3e_noovchk"):
                br(C_ZERO, "dvs0_ov", pol=1)             # the quotient does not fit the size: MIN / -1
            jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            at("dvs0_rem")
            jmp("disp", ASEL=A["RT"], TADDR=2, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], BE_MODE=0, WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            at("dvs0_ov")
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"])
            pad(3)
            w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
            jmp("disp", CONSUME=1)
            at("dvs0_z")
            jmp("disp", RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, SIZE_SEL=1, RES_SEL=RES["ALU"], CONSUME=1)

            # ---- DIV.W Rf, Rn (A5 F2 D = 0, register specifier; census div.w Rf, Rn): g_div's words with divisor = Rf, dividend / destination = Rn. MAME opDIVW:
            # OV = (Rn == 0x80000000 && Rf == -1); divisor nonzero and no OV: Rn <- Rn / Rf (signed, truncating); Z S from Rn either way; CY unchanged.
            at("sp3_divw_d0")
            operand(MS_B6)
            br(C_REGDIR, "dvw0_r")
            jmp("trap_mode")
            at("dvw0_r")
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=0)      # T0 = the divisor Rf
            w(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)      # T1 = the dividend Rn
            pad(3)
            w(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp3e_noz"):
                br(C_ZERO, "dvw0_noop")
            w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=31, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=2)     # T2 = 0x80000000
            pad(3)
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=2, ALU_OP=ALU["XOR"], RES_SEL=RES["ALU"])
            pad(2)
            if not mut.get("sp3e_noovchk"):
                br(C_ZERO, "dvw0_chk2")                  # dividend == 0x80000000
            at("dvw0_go")
            w(ASEL=A["RT"], TADDR=0, RB_SEL=1 if mut.get("sp3e_d0_swap") else 3, BSEL=A["RB"], MD_START=1, MD_SIGNED=0 if mut.get("sp3e_dvs_unsigned") else 1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"])
            pad(5)
            here_ = len(p)
            p.append(word(NXT=2, CPOL=1, JADDR=here_, **cond(9)))
            jmp("disp", ASEL=A["MDLO"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"], WR_DST=WR["GPR"], WR_ASEL=3, CONSUME=1)
            at("dvw0_chk2")
            w(BSEL=A["RT"], TADDR=0, ALU_OP=ALU["INC"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, "dvw0_ov")                        # divisor == -1
            jmp("dvw0_go")
            at("dvw0_ov")
            w(ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"])
            pad(4)
            w(ASEL=A["PSW"], BSEL=A["CONST"], RCONST=4, ALU_OP=ALU["OR"], RES_SEL=RES["ALU"], WR_DST=WR["PSW"])      # OV <- 1
            jmp("disp", CONSUME=1)
            at("dvw0_noop")
            jmp("disp", ASEL=A["RT"], TADDR=1, ALU_OP=ALU["PASSA"], FLAG_UPD=0b0111, RES_SEL=RES["ALU"], CONSUME=1)

            # ---- DIVX #q, Rn (A6, F2 D = 1; census divx #q, Rn): MAME opDIVX: the 64-bit R[n+1]:R[n] divided by q, SIGNED (div_64x32_rem); R[n] <- the low 32 bits
            # of the quotient, R[n+1] <- the remainder (sign of the dividend); S Z from R[n]; OV CY unchanged. g_divux's frame (n = 31 traps, PSW saved / restored,
            # q = 0 -> divux's dvx_z: quotient 0, remainder R[n], as MAME's arm64 build gives) around a copy of its loop on |dividend| (the #q divisor is >= 0),
            # then quotient and remainder negated when the dividend was negative; the results go out through divux's dvx_done. Only #q (others trap `mode`).
            at("g_divx")
            w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=31, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"])     # n = format byte & 31
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=7)
            p.append(word(CONSUME=2))
            br(C_F1, "trap_mode")
            br(C_D, "trap_mode", pol=1)
            operand(MS_B6)
            br(C_QUICK, "dsx_q")
            jmp("trap_mode")
            at("dsx_q")
            w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A["IMM"], BSEL=A["CONST"], RCONST=15, ALU_OP=ALU["AND"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=5)
            w(ASEL=A["RT"], TADDR=7, BSEL=A["CONST"], RCONST=5, SH_OP=6, RES_SEL=RES["SHIFT"])                                   # (n + 1) >> 5
            pad(2)
            br(C_ZERO, "trap_mode", pol=1)               # n = 31
            w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)                     # lo = R[n]
            w(TADDR=7, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(TADDR=7, RIDX=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])                           # MDR <- R[n + 1]
            w(ASEL=A["PSW"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=8)
            w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=1)
            w(ASEL=A["ZERO"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)
            w(ASEL=A["MDR"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)                                 # hi = R[n + 1]
            w(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=6, SH_OP=0, RES_SEL=RES["SHIFT"], WR_DST=WR["TEMP"], TADDR=6)       # 64 iterations
            w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, "dvx_z")                          # divisor 0 (divux's path)
            w(ASEL=A["RT"], TADDR=2, BSEL=A["CONST"], RCONST=31, SH_OP=6, RES_SEL=RES["SHIFT"])                                   # the dividend's sign
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=9)                       # T9 = it
            pad(2)
            if not mut.get("sp3e_divx_unsigned"):
                br(C_ZERO, "dsx_loop")
                w(ASEL=A["RT"], TADDR=3, ALU_OP=ALU["NOT"], RES_SEL=RES["ALU"])
                w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["ADD"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=3)   # lo <- ~lo + 1 (CY = carry)
                w(ASEL=A["RT"], TADDR=2, ALU_OP=ALU["NOT"], RES_SEL=RES["ALU"])
                w(ASEL=A["RES"], FWD_EN=1, BSEL=A["ZERO"], ALU_OP=ALU["ADDC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=2)    # hi <- ~hi + CY
                pad(3)
            at("dsx_loop")
            for t_ in (3, 2, 1):                             # (r : hi : lo) <<= 1 through CY: lo + lo, hi + hi + CY, r + r + CY (CY <- r's bit 31 = bit 32 of 2r)
                w(ASEL=A["RT"], TADDR=t_, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
                w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=t_, ALU_OP=ALU["ADD" if t_ == 3 else "ADDC"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
            pad(3)
            br(3, "dsx_sub")                                 # bit 32 of 2r + bit set: 2r + bit > d
            w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["SUB"], FLAG_UPD=0b1000, RES_SEL=RES["ALU"])          # r - d (compare); CY = borrow
            pad(3)
            br(3, "dsx_q0")                                  # r < d: quotient bit 0
            at("dsx_sub")
            w(ASEL=A["RT"], TADDR=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])   # r <- r - d (mod 2^32)
            at("dsx_q1")
            w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=4, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["CONST"], RCONST=1, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=4)   # q <- 2q + 1
            jmp("dsx_next")
            at("dsx_q0")
            w(ASEL=A["RT"], TADDR=4, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            w(ASEL=A["RES"], FWD_EN=1, BSEL=A["RT"], TADDR=4, ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])       # q <- 2q
            at("dsx_next")
            w(TADDR=6, BSEL=A["RT"], ALU_OP=ALU["DEC"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])
            pad(2)
            br(C_ZERO, "dsx_fix")
            jmp("dsx_loop")
            at("dsx_fix")
            w(ASEL=A["RT"], TADDR=9, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
            pad(2)
            br(C_ZERO, "dvx_done")                       # dividend >= 0
            if not mut.get("sp3e_divx_noneg_rem"):
                w(ASEL=A["RT"], TADDR=1, BSEL=A["ZERO"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])     # r <- 0 - r (SUB is b - a)
            w(ASEL=A["RT"], TADDR=4, BSEL=A["ZERO"], ALU_OP=ALU["SUB"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"])         # q <- 0 - q
            pad(4)
            jmp("dvx_done")

        # ---- SM1 round 2 (experiments/SM1-spidman/README.md: the dynamic classification of every Spider-Man PC through MAME frame 7200) ----
        # ROT Rf, Rn (8B / 89 / 8D F2 D=0, register specifier; 060F1F rot.b R1, R0): count = the low byte of Rf (signed: < 0 rotates right), value Rn;
        # the g_shl_d0 words with the rotator (SH_OP 2), as the #count form g_rot_d1 does. Memory operands trap `mode`.
        if not mut.get("sm_rot_nod0"):
            at("sm_rot_d0")
            operand(MS_B6)
            br(C_REGDIR, "sm_rot_d0r")
            jmp("trap_mode")
            at("sm_rot_d0r")
            if not mut.get("sm_rot_cnt0_keepcy"):
                # MAME opROTB/H/W: a zero count clears CY (and OV); Z S from the value. The rotator leaves CY unchanged at count 0.
                w(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["ZEXT"], SIZE_SEL=3, RCONST=0, RES_SEL=RES["ALU"])         # the count byte
                pad(2)
                br(C_ZERO, "sm_rot_d0z")
            w(RA_SEL=3, ASEL=A["RA"], RB_SEL=1, RCONST=0, BSEL=A["RB"], SH_OP=0 if mut.get("sm_rot_as_shl") else 2, RES_SEL=RES["SHIFT"], FLAG_UPD=0b1100, SIZE_SEL=1)
            w(ASEL=A["RES"], FWD_EN=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], FLAG_UPD=0b0011, SIZE_SEL=1, WR_DST=WR["GPR"], WR_ASEL=3, BE_MODE=0, CONSUME=1)
            jmp("disp")
            if not mut.get("sm_rot_cnt0_keepcy"):
                at("sm_rot_d0z")
                jmp("disp", RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], FLAG_UPD=0b1111, SIZE_SEL=1, RES_SEL=RES["ALU"], CONSUME=1)   # value unchanged; CY = OV = 0
        # (U1 merge: SpidPlay1's SHL F1 <count>, <memory> routine `sm_shl_f1` removed; GA2Play6's shl_f1 serves the form, the g_shl F1 branch
        #  goes there for both lines; sm_shl_nof1 / sm_shl_f1_nostore / sm_shl_f1_shr act on shl_f1.)

    for i, name in fix:                                 # resolve jump targets
        p[i] |= sym[name] << 8

    dtab = {op: sym["trap"] for op in range(256)}
    dtab[0x00] = sym["halt"]
    dtab[0xCD] = sym["nop"]
    fmt_movw = 2 | (2 << 2) | (0 << 4) | (1 << 8) | (1 << 10)   # W, W; layout 0; 2 operands, op2 written
    dtab[0x2D] = (fmt_movw << 12) | sym["movw"]
    dtab[0x6A] = sym["br8"]
    for n, (opc, aop, writes, fmask) in ALU_OPS.items():
        fmt = 2 | (2 << 2) | (0 << 4) | (1 << 8) | ((1 << 10) if writes else 0)
        dtab[opc] = (fmt << 12) | sym[n]
    dtab[0xF7] = sym["getpsw"]
    # R2b: LDPR, with the dispatch-table privileged flag (fmt bit 7 -> dflags[0] -> ucond 16)
    dtab[0x12] = (((1 << 7) if not mut.get("dtab_flag_cleared") else 0) << 12) | sym["nop" if mut.get("dtab_wrong_routine") else "ldpr"]
    if not smc_test:
        dtab[0xD6] = sym["jmp"]                         # D7 (modm 1) too: B3, register-based / indexed operands through ea0
        dtab[0xD7] = sym["jmp"]
    # R3b: MOVEA.B (0x40) in production builds only; 0x42 / 0x44 stay trap. Dispatch layout: the movw layout (the routine reads
    # fld1 = byte 1 [4:0]; the size and flag bits are not consumed, docs/r3-movea-plan.md section 11 ASSUMED 2)
    if not smc_test or mut.get("mv_smc_dispatch"):
        dtab[0x40] = (fmt_movw << 12) | sym["nop" if mut.get("mv_dtab40_wrong") else "movea"]
    if mut.get("mv_dtab40_trap"):
        dtab[0x40] = sym["trap"]
    if mut.get("mv_dtab42"):
        dtab[0x42] = (fmt_movw << 12) | sym["movea"]
    if mut.get("mv_dtab44"):
        dtab[0x44] = (fmt_movw << 12) | sym["movea"]

    if not smc_test:
        dtab[0x09] = sym["movb"]                        # B1: MOV.B [Rn+], [Rm+] (sizes: the routine uses explicit sizes)
        dtab[0xE8] = sym["jsr"]                         # B1: JSR /abs (E9, modm 1, stays trap)
        dtab[0xFA] = sym["retis"]                        # B1: RETIS #q (FB, modm 1, stays trap)
        dtab[0xEE] = sym["push"]                        # B1: PUSH #q (EF, modm 1, stays trap)
        dtab[0xC6] = sym["dbr"]                         # B1: DBR (layout 0: fld1 = byte 1 [4:0]); other DBcc sub-opcodes trap
        # B2: the generic families (sizes from the table: b = 0, h = 1, w = 2) replace the M1 word-only MOV.W / ADD.W / SUB.W / CMP.W / AND.W entries
        def gfmt(s1, s2, writes=True):
            return s1 | (s2 << 2)

        def famfmt(s1, s2, rmw, i):                       # C1: the operation id travels in fmt[11:8] (ucond 17..20)
            return s1 | (s2 << 2) | ((i & 1) << 8) | (rmw << 9) | (((i >> 1) & 1) << 10) | (((i >> 2) & 1) << 11)
        for rmw_, i_, opcs in ((0, 1, (0x38, 0x3A, 0x3C)), (1, 0, (0x80, 0x82, 0x84)), (1, 1, (0xA8, 0xAA, 0xAC)), (1, 2, (0xB8, 0xBA, 0xBC)),
                               (1, 3, (0xA0, 0xA2, 0xA4)), (1, 4, (0x88, 0x8A, 0x8C)), (1, 5, (0xB0, 0xB2, 0xB4)), (0, 0, (0x09, 0x1B, 0x2D))):
            for sz_, opc_ in enumerate(opcs):
                dtab[opc_] = (famfmt(sz_, sz_, rmw_, i_) << 12) | sym["g_gen"]
        dtab[0x0B] = (famfmt(0, 1, 0, 0) << 12) | sym["g_gen"]      # MOVZ.BH
        dtab[0x0D] = (famfmt(0, 2, 0, 0) << 12) | sym["g_gen"]      # MOVZ.BW (zero-extended source, word destination)
        dtab[0x1D] = (famfmt(1, 2, 0, 0) << 12) | sym["g_gen"]      # MOVZ.HW
        dtab[0x2C] = (famfmt(2, 2, 0, 2) << 12) | sym["g_gen"]      # RVBYT
        for op_ in range(0x60, 0x70):
            if op_ != 0x6A:
                dtab[op_] = sym["bcc"]
        for sz_, (o_shl, o_rot) in enumerate(((0xA9, 0x89), (0xAB, 0x8B), (0xAD, 0x8D))):
            dtab[o_shl] = ((gfmt(0, sz_) | ((1 << 8) if (sz_ == 2 or (sz_ == 0 and not mut.get("sm_shlb_nomark"))) and not mut.get("rp_shl_nomark") else 0)) << 12) | sym["g_shl"]   # R-post1: fmt[8] on SHL.W = the register-count form; SM1: SHL.B too (060F36 shl.b R1, R0)
            dtab[o_rot] = (gfmt(0, sz_) << 12) | sym["g_rot"]
        for op_ in range(0x70, 0x80):
            dtab[op_] = sym["bcc16"]
        for opc_, sz_ in ((0x40, 0), (0x42, 1), (0x44, 2)):        # MOVEA.B / .H / .W: size1 = the operand dimension (index scale); the result is a word
            dtab[opc_] = ((sz_ | (2 << 2) | (1 << 8) | (1 << 10)) << 12) | sym["movea"]
        dtab[0x47] = (gfmt(0, 0) << 12) | sym["g_setf"]
        dtab[0x9D] = (gfmt(0, 2) << 12) | sym["g_rotc"]
        dtab[0x95] = (gfmt(2, 2) << 12) | sym["g_mulu"]
        dtab[0x87] = (gfmt(2, 2, False) << 12) | sym["g_test1"]
        dtab[0x45] = (gfmt(2, 2) << 12) | sym["g_xchw"]
        if not mut.get("p3_no_xchb"):
            dtab[0x41] = (gfmt(0, 0) << 12) | sym["g_xchw"]                           # GA2Play3: XCH.B
        dtab[0x13] = (gfmt(2, 2, False) << 12) | sym["g_updpsw"]
        dtab[0x4A] = ((gfmt(1, 1, False) | (1 << 8)) << 12) | sym["g_updpsw"]
        dtab[0x97] = (gfmt(2, 2, False) << 12) | sym["g_bit"]                    # SET1
        dtab[0xA7] = ((gfmt(2, 2, False) | (1 << 8)) << 12) | sym["g_bit"]       # CLR1 (ucond 17)
        dtab[0xB7] = ((gfmt(2, 2, False) | (1 << 10)) << 12) | sym["g_bit"]      # NOT1 (ucond 19)
        for op_ in (0xEC, 0xED):
            dtab[op_] = sym["g_pushm"]
        for op_ in (0xE4, 0xE5):
            dtab[op_] = sym["g_popm"]
        dtab[0xC7] = sym["g_tb"]
        dtab[0xA5] = (gfmt(2, 2) << 12) | sym["g_div"]
        dtab[0x5A] = ((gfmt(1, 1, False) | (1 << 8)) << 12) | sym["movcu"]
        dtab[0x58] = (gfmt(0, 0, False) << 12) | sym["movcu"]
        dtab[0x48] = sym["bsr"]
        dtab[0xCA] = sym["rsr"]
        for sz_, (o_inc, o_test, o_dec) in enumerate(((0xD9, 0xF1, 0xD1), (0xDB, 0xF3, 0xD3), (0xDD, 0xF5, 0xD5))):
            for d_ in (0, 1):                           # opcode bit 0 = modm: D8 / D9 ..., F0 / F1 ...
                dtab[o_inc - d_] = (sz_ << 12) | sym["g_i1"]
                dtab[o_test - d_] = ((sz_ | (1 << 10)) << 12) | sym["g_i1"]          # fmt[10] (ucond 19) = TEST
                dtab[o_dec - d_] = ((sz_ | (1 << 8)) << 12) | sym["g_i1"]            # fmt[8] (ucond 17) = DEC
        # R-post1 (experiments/MC-post1): the new dispatch entries
        dtab[0xB6] = (gfmt(2, 2) << 12) | sym["g_divux"]                        # DIVUX
        dtab[0x0A] = (gfmt(0, 1) << 12) | sym["g_movs"]                         # MOVS.BH
        dtab[0x0C] = ((gfmt(0, 2) | (1 << 8)) << 12) | sym["g_movs"]            # MOVS.BW (R-play1: fmt[8] = F2 D=0 allowed, as .BH / .HW)
        dtab[0x1C] = (gfmt(1, 2) << 12) | sym["g_movs"]                         # MOVS.HW
        dtab[0x39] = (gfmt(0, 0) << 12) | sym["g_neg"]                          # NEG.B
        dtab[0x3D] = (gfmt(2, 2) << 12) | sym["g_neg"]                          # NEG.W
        dtab[0x99] = ((gfmt(0, 0) | (1 << 8)) << 12) | sym["g_rotc"]           # ROTC.B (fmt[8]: the 9-bit ring for negative counts)
        dtab[0x85] = (gfmt(2, 2) << 12) | sym["g_mul"]                          # MUL.W
        dtab[0x19] = (gfmt(1, 0) << 12) | sym["g_movt"]                         # R-post2: MOVT.HB (F1, memory destination only)
        # R-post3: NEG.H, DIVU.W, SHA.W (new entries, F2 D=0 register / D=1 #imm8 only); MOVS.BH / .HW get fmt[8] (ucond 17) = "F2 D=0 allowed" (MOVS.BW does not)
        dtab[0x3B] = ((gfmt(2, 2) if mut.get("pl_negh_word") else gfmt(1, 1)) << 12) | sym["g_neg"]                                  # R-play1: NEG.H shares g_neg (F1, F2 D=1, F2 D=0)
        # R-play1
        dtab[0x2B] = (gfmt(2, 0 if mut.get("pl_movtwh_byte") else 1) << 12) | sym["g_movt"]  # MOVT.WH
        dtab[0x81] = ((gfmt(0, 0) | (1 << 8)) << 12) | sym["g_mulhb"]                   # MUL.B (fmt[8] = byte)
        dtab[0x83] = (gfmt(1, 1) << 12) | sym["g_mulhb"]                                # MUL.H
        dtab[0xA1] = (gfmt(0, 0) << 12) | sym["g_divhb"]                                # DIV.B
        dtab[0xA3] = (gfmt(1, 1) << 12) | sym["g_divhb"]                                # DIV.H
        dtab[0x50] = ((gfmt(0, 0) | (1 << 8)) << 12) | sym["g_divhb"]                   # REM.B (fmt[8] = REM)
        dtab[0x94] = (gfmt(2, 2) << 12) | sym["g_addc"]                                 # ADDC.W
        dtab[0x43] = (gfmt(1, 1) << 12) | sym["g_xchh"]                                 # XCH.H
        if not mut.get("p8_no_new_dispatch"):
            dtab[0xB1] = (gfmt(0, 0) << 12) | sym["g_divuh"]                            # GA2Play8: DIVU.B (the DIVU.H routine at byte sizes)
            dtab[0x51] = (gfmt(0, 0) << 12) | sym["g_remub"]                            # GA2Play8: REMU.B
            dtab[0xE6] = sym["pop_m"]                                                   # GA2Play8: POP <memory> (bank 0)
        dtab[0xBB] = (gfmt(0, 1) << 12) | sym["g_shah"]                                 # SHA.H
        dtab[0xE9] = sym["jsr_x"]                                                       # JSR disp32[PC](Rx) (bank 1)
        dtab[0xEF] = sym["push_r"]                                                      # PUSH Rn
        dtab[0xE7] = sym["pop_r"]                                                       # POP Rn
        dtab[0xB5] = (gfmt(2, 2) << 12) | sym["g_divu"]
        dtab[0xBD] = (gfmt(0, 2) << 12) | sym["g_sha"]
        dtab[0x0A] = ((gfmt(0, 1) | (1 << 8)) << 12) | sym["g_movs"]
        dtab[0x1C] = ((gfmt(1, 2) | (1 << 8)) << 12) | sym["g_movs"]
        # GA2Play4: MULU.H, DIVU.H (new entries; F2 D=1 any source, F2 D=0 register)
        if not mut.get("p4_no_muluh"):
            dtab[0x93] = (gfmt(1, 1) << 12) | sym["g_muluh"]
        if not mut.get("p4_no_divuh"):
            dtab[0xB3] = (gfmt(1, 1) << 12) | sym["g_divuh"]
        # SM1 (Spider-Man): MULU.B (fmt[8] = byte, as MUL.B) and SHA.B (g_shah with size2 = byte). U1 merge: MULU.H stays GA2Play4's g_muluh (above),
        # REMU.B GA2Play8's g_remub (its own dispatch entry above); sm_no_new_ops still removes all four SM1 opcodes (the mutant's meaning).
        if not mut.get("sm_no_new_ops"):
            dtab[0x91] = ((gfmt(0, 0) | (1 << 8)) << 12) | sym["g_muluhb"]
            if not mut.get("sp3e_off"):                                                 # SP3-E
                dtab[0x53] = (gfmt(1, 1) << 12) | sym["g_remub"]                        # REMU.H: GA2Play8's routine at halfword size (P0 experiment E1; P1.0 R3)
                dtab[0xA6] = (gfmt(2, 2) << 12) | sym["g_divx"]                         # DIVX
            # P1.0 R4: DIVU.B / DIVU.H stay GA2Play4/8's g_divuh (above); R6: POP <mem> stays GA2Play8's pop_m (above)
            if not mut.get("sp4r_off"):                                                 # SP4-R: REMU.W on SP3's REMU routine at word size (census remu.w d16[PC](Rx), Rf)
                dtab[0x55] = ((gfmt(2, 2) | (0 if mut.get("sp4r_nofmt8") else (1 << 8))) << 12) | sym["g_remub_sp"]   # release union: SP4 validated SP3's routine (g_remub_sp here)
            dtab[0xB9] = (gfmt(0, 1 if mut.get("sm_shab_half") else 0) << 12) | sym["g_shah"]
        else:
            for op_ in (0x91, 0x93, 0x51, 0xB9):
                dtab[op_] = sym["trap"]

    mtab = {i: sym["trap_mode"] for i in range(512)}
    for r in range(32):                                 # modm 1: register direct
        mtab[0x100 | 0x60 | r] = (1 << 12) | sym["mret"]
    for n in range(16):                                 # modm 0: immediate quick (desc: immediate, no bytes)
        mtab[0xE0 | n] = ((1 << 1) << 12) | sym["mret"]
    mtab[0xF4] = (((1 << 1) | (3 << 2)) << 12) | sym["mret"]   # modm 0: immediate with bytes
    if smc_test:                                        # TEST-ONLY: modm 0 register indirect
        for r in range(32):
            mtab[0x60 | r] = sym["mret"]
    else:                                               # R1b: direct address (modm 0, group 7 index 0x13)
        # not register, not immediate; 32-bit offset; AGU base 5 (zero); no index/step; not deferred
        mtab[0x0F3] = (0x14C << 12) | sym["mret"]
        # R4b (S-D): disp32[PC] = base 2 (ipc), disp32, not register, not immediate, bit 11 = the temporary PC-relative tag (ucond 23)
        mtab[0x0F2] = (0x88C << 12) | sym["mret"]
        # SM1 (Spider-Man): d8[PC] (F0) / d16[PC] (F1). Not tagged (bit 11 stays the disp32[PC]-only S-D tag, so every tag-branching PC-relative
        # routine keeps its disp32 encoding); instead bit 4 (ucond 22, the register-based class) routes them to the existing DEC_EA words, which take
        # the AGU base (2 = ipc) and the displacement size (1 / 2) from the descriptor: ea0 / ea1 (any memory operand, indexed bases included),
        # movea (movea_rb, movea_c23_b), movea [-Rn] (movea_f1_rb), jmp (jmp_ea). jsr guards group 7 (see jsr_g3).
        pc8_, pc16_ = (0x094, 0x098) if not mut.get("sm_pc_sizeswap") else (0x098, 0x094)     # TEST-ONLY mutants: sizes swapped / AGU base 0 (register)
        if mut.get("sm_pc_base0"):
            pc8_, pc16_ = pc8_ & ~0x1C0, pc16_ & ~0x1C0
        mtab[0x0F0] = (pc8_ << 12) | sym["mret"]
        mtab[0x0F1] = (pc16_ << 12) | sym["mret"]
        if not mut.get("sm3_ipc_off"):                  # SP3-B: [d8[PC]] (F8) / [d16[PC]] (F9): [d8 / d16[Rn]]'s descriptors and routine (mdd -> mpi)
            mtab[0x0F8] = (0x1D4 << 12) | sym["mdd"]
            mtab[0x0F9] = (0x1D8 << 12) | sym["mdd"]
        for r in range(32):                             # B1/B2: modm 1 [Rn+] (0x80|r) and [-Rn] (0xA0|r); bit 4 = register-based memory, bit 11 = stepped
            mtab[0x100 | 0x80 | r] = (0xA10 << 12) | sym["mret"]
            mtab[0x100 | 0xA0 | r] = (0xC10 << 12) | sym["mret"]
            # B2 modm 0: d8[Rn] (0x00|r), d16[Rn] (0x20|r), d32[Rn] (0x40|r), [Rn] (0x60|r); base 0 = the A bus (register), bit 4
            mtab[0x00 | r] = (0x014 << 12) | sym["mret"]
            mtab[0x20 | r] = (0x018 << 12) | sym["mret"]
            mtab[0x40 | r] = (0x01C << 12) | sym["mret"]
            mtab[0x60 | r] = (0x010 << 12) | sym["mret"]
            for g_ in (0x80, 0xA0, 0xC0):                           # B2/B3 modm 0 0x80..0xDF (indirect modes, unsupported as operands): see `mdd` (movcu register length, jsr indirect)
                # R-post1: descriptors (read only after `mdd` returned, i.e. while T15 = 1, and there only by the index-base branches): [d16[Rn]] bit 0
                # (-> the [d16[Rn]](Rx) handlers), [d8[Rn]] / [d32[Rn]] bit 1 (-> trap `mode`). jsr indirect and the movcu register length ignore them.
                mtab[g_ | r] = ({0x80: 0x1D4, 0xA0: 0x1D8, 0xC0: 2}[g_] << 12) | sym["mdd"]     # R-play1: [d8[Rn]] / [d16[Rn]] = register-based memory, AGU base MDR (see mdd_i); [d32[Rn]] (immediate bit) traps
            mtab[0x100 | 0x00 | r] = (0x814 << 12) | sym["mret"]    # B3 modm 1: dd8[Rn] / dd16[Rn] / dd32[Rn] (tags 11 + 4 as the stepped class; told apart by the mode byte)
            mtab[0x100 | 0x20 | r] = (0x818 << 12) | sym["mret"]
            mtab[0x100 | 0x40 | r] = (0x81C << 12) | sym["mret"]
            mtab[0x100 | 0xC0 | r] = (0x830 << 12) | sym["mret"]   # B2 modm 1: the index prefix (Rx = r): bits 11 + 4 (the stepped-class tag pair, told apart by the mode byte) + 5 (index)
    for k, v in (mut.get("mv_mtab") or {}).items():       # TEST-ONLY mode-table mutants (structural)
        mtab[k] = (v & ~0xFFF) | sym["mret"]
    check_tables(sym, dtab, mtab, smc_test)
    check_tag(p, sym, mtab, smc_test, mut)
    if not mut:
        check_ldpr(p, sym, dtab, fix, ldpr_start, ldpr_chk_start, ldpr_end)
        check_movea(p, sym, dtab, mtab, fix, movea_start, movea_end, smc_test)
    if not smc_test and not mut.get("no_irq_stubs"):
        # B3: every supported opcode is dispatched through a two-word stub `br(irq_pending, irq_entry); jmp routine`: the dispatch has latched ipc = the
        # instruction's own start, so an interrupt is taken exactly at an instruction boundary and the frame's return PC is ipc.
        if "mul" in _C1GEN and "g_mulg" in sym:                                                              # S1 (P1.1): the six multiplies -> g_mulg
            for op_, sz_, sg_ in ((0x81, 0, 1), (0x83, 1, 1), (0x85, 2, 1), (0x91, 0, 0), (0x93, 1, 0), (0x95, 2, 0)):
                dtab[op_] = ((sz_ | (sz_ << 2) | ((1 << 8) if sz_ == 0 else 0) | ((1 << 10) if sz_ == 2 else 0) | (sg_ << 11)) << 12) | sym["g_mulg"]
        if "div" in _C1GEN and "g_mulg" in sym:                                                              # S1 (P1.1): divide / remainder -> the same routine
            for op_, sz_, sg_, rm_ in ((0xA1, 0, 1, 0), (0xA3, 1, 1, 0), (0xA5, 2, 1, 0), (0xB1, 0, 0, 0), (0xB3, 1, 0, 0), (0xB5, 2, 0, 0),
                                       (0x50, 0, 1, 1), (0x52, 1, 1, 1), (0x54, 2, 1, 1), (0x51, 0, 0, 1), (0x53, 1, 0, 1), (0x55, 2, 0, 1)):
                dtab[op_] = ((sz_ | (sz_ << 2) | (rm_ << 8) | (1 << 9) | (sg_ << 11)) << 12) | sym["g_mulg"]
        stub = {}
        NEWOPS = [0x19, 0x3B, 0xB5, 0xBD, 0x2B, 0x81, 0x83, 0xA1, 0xA3, 0x50, 0x94, 0x43, 0xBB, 0xE9, 0xEF, 0xE7, 0xB1, 0x51, 0xE6]
        for op in [o for o in range(256) if o not in NEWOPS] + NEWOPS:      # R-post2 / 3: the new stubs are allocated last, so no existing stub moves relative to the others
            ua = dtab[op] & 0xFFF
            if ua == sym["trap"]:
                continue
            if ua not in stub:
                stub[ua] = len(p)
                p.append(word(NXT=2, CPOL=0, JADDR=sym["irq_entry"], **cond(10)))
                p.append(word(NXT=1, JADDR=ua))
            dtab[op] = (dtab[op] & ~0xFFF) | stub[ua]
    if not mut.get("no_compact") and not _NO_COMPACT:
        compact(p, sym, dtab, mtab)
        if _PACK:
            if str(HERE) not in sys.path:                  # copies of this builder (mutant tools) rewrite HERE to this directory
                sys.path.insert(0, str(HERE))
            from mc_pack import pack
            pack(p, sym, dtab, mtab, _PACK, keep_bits=not smc_test, ref_key=None if smc_test else ("rn" if reset_native else "prod"))
    return p, sym, dtab, mtab


LAST_COMPACTION = {}


def compact(p, sym, dtab, mtab):
    """B3 control-store reclamation: delete every word that is unreachable by control-flow analysis of THIS configuration's microcode and tables.
    Roots: the reset entry (address 0), `disp`, `irq_entry`, every dispatch-table and mode-table uaddr. Edges: fall-through (NXT 0, 5, 6), jump (1),
    conditional / call (2, 3: the target and the next word; a callee returns to the word after its call). Kept words keep their order; every JADDR,
    symbol and table uaddr is renumbered; deleted labels vanish from `sym`. Asserts: nothing kept refers to a deleted word. b3_reach.py runs the same
    analysis over all 8 configurations; code that is reachable in any configuration is never deleted from that configuration."""
    from microasm import FIELDS
    f = lambda wd, k: (wd >> FIELDS[k][0]) & ((1 << FIELDS[k][1]) - 1)
    N = len(p)
    roots = [0, sym["reset"], sym["disp"]] + ([sym["irq_entry"]] if "irq_entry" in sym else []) + [v & 0xFFF for v in dtab.values()] + [v & 0xFFF for v in mtab.values()]
    seen, work = set(), list(roots)
    while work:
        a = work.pop()
        if a in seen or a >= N:
            continue
        seen.add(a)
        n = f(p[a], "NXT"); j = f(p[a], "JADDR")
        if n in (0, 5, 6):
            work.append(a + 1)
        elif n == 1:
            work.append(j)
        elif n in (2, 3):
            work += [j, a + 1]
    keep = sorted(seen)
    new = {old: i for i, old in enumerate(keep)}
    jlsb, jw = FIELDS["JADDR"]
    q = []
    for old in keep:
        wd = p[old]
        if f(wd, "NXT") in (1, 2, 3):
            assert f(wd, "JADDR") in new, f"word {old} jumps to deleted word {f(wd, 'JADDR')}"
            wd = (wd & ~(((1 << jw) - 1) << jlsb)) | (new[f(wd, "JADDR")] << jlsb)
        q.append(wd)
    deleted = sorted((k for k, v in sym.items() if v not in new), key=lambda k: sym[k])
    nsym = {k: new[v] for k, v in sym.items() if v in new}
    for t in (dtab, mtab):
        for k in list(t):
            ua = t[k] & 0xFFF
            assert ua in new, f"table entry {k} targets deleted word {ua}"
            t[k] = (t[k] & ~0xFFF) | new[ua]
    # label spans of the deleted words, for the record
    starts = sorted(sym.items(), key=lambda kv: kv[1])
    span = {}
    for i, (k, v) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else N
        d = sum(1 for a in range(v, end) if a not in seen)
        if d:
            span[k] = d
    LAST_COMPACTION.clear()
    LAST_COMPACTION.update(words_before=N, words_after=len(q), reachable=len(seen), deleted_words=N - len(q), deleted_label_spans=span, deleted_labels=deleted)
    for k in deleted:
        assert k not in nsym
    p[:] = q
    sym.clear(); sym.update(nsym)


def main():
    global _PACK, _C1GEN, _S1
    for a_ in sys.argv[2:]:
        if a_.startswith("--mc-pack="):
            _PACK = a_.split("=", 1)[1]
        elif a_.startswith("--arith"):
            if a_ != ARITH_ARG:
                raise SystemExit(f"refusing: {a_!r}: the only arithmetic selector is {ARITH_ARG!r}")
            _C1GEN, _S1 = {"mul", "div"}, True
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    self_check()
    p, sym, dtab, mtab = build("--smc-test" in sys.argv, "--smc-merged" in sys.argv, "--reset-native" in sys.argv)
    write_hex(out / "m1_cstore.hex", p, (CW_W + 3) // 4)
    write_hex(out / "m1_dispatch.hex", [dtab[i] for i in range(256)], 6)
    write_hex(out / "m1_mode.hex", [mtab[i] for i in range(512)], 6)
    (out / "m1_symbols.txt").write_text("".join(f"{k}={v}\n" for k, v in sym.items()))
    print(f"m1.1 microcode: {len(p)} words" + (f" (packed: {_PACK})" if _PACK else ""))


if __name__ == "__main__":
    main()
