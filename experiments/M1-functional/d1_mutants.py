#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plausible incorrect fetch implementations for the D1 regression.

Each mutant is the current v2_fetch.sv or v2_fetch_byteq.sv with one targeted
change. run_d1.sh requires tb_d1_fetch to fail on every one of them, so the
bench is shown to tell the correct design from these near misses (review
findings C1-6, and C1-2/C1-2w/C1-3 for the SMC snoop), not only from the
original defects.

    d1_mutants.py <rtl-dir> <out-dir>    writes <out-dir>/<name>/ for each
    d1_mutants.py --list                 prints "<name> <variant> <what>"
"""
import os
import shutil
import sys

STD, BQ = "v2_fetch.sv", "v2_fetch_byteq.sv"

# name: (file, what, [(old, new), ...]); every old string must occur once
MUTANTS = {
    "d1b_only": (STD, "D1b reverted: avail underflows after an unaligned redirect", [
        ("assign avail   = (nl == 3'd0) ? 6'd0 : {nl, 3'b000} - {3'b000, head_pc[2:0]};",
         "assign avail   = {nl, 3'b000} - {3'b000, head_pc[2:0]};")]),
    "withdraw": (STD, "D1 reintroduced: a flush withdraws the outstanding request", [
        ("""        end else begin
            stale <= pending;
        end""",
         """        end else begin
            stale <= 1'b0; pending <= 1'b0; half <= 1'b0; if_req <= 1'b0; pf_req <= 1'b0;
        end""")]),
    "no_stale": (STD, "the outstanding request is not marked stale: its data is accepted", [
        ("            stale <= pending;", "            stale <= 1'b0;")]),
    "no_retire": (STD, "an acknowledge in the flush cycle is not retired", [
        ("        if (pending && (if_ack || pf_ack)) begin\n            pending <= 1'b0; half <= 1'b0;",
         "        if (1'b0) begin\n            pending <= 1'b0; half <= 1'b0;")]),
    "skip_line": (STD, "the stale drop also skips the next current line", [
        ("if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end",
         "if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; fetch_line <= fetch_line + 32'd8; end")]),
    "reissue": (STD, "the stale request is re-issued once, as a real second transaction (request low, then raised again at the old address) and dropped", [
        ("reg         stale;", "reg         stale, dup, redo;"),
        ("nl <= 3'd0; stale <= 1'b0; pending <= 1'b0; half <= 1'b0;",
         "nl <= 3'd0; stale <= 1'b0; dup <= 1'b0; redo <= 1'b0; pending <= 1'b0; half <= 1'b0;"),
        ("            stale <= pending;", "            stale <= pending; dup <= pending;"),
        ("if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end",
         "if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; redo <= dup; dup <= 1'b0; end"),
        ("""            pending      <= 1'b1;
            pending_wide <= fast_ifetch && is_rom;
            if (fast_ifetch && is_rom) begin if_req <= 1'b1; if_addr_q <= fetch_line[23:0]; end
            else begin pf_req <= 1'b1; pf_addr <= fetch_line; end""",
         """            pending      <= 1'b1;
            if (redo) begin                  // same port and address as the dropped request
                redo <= 1'b0; stale <= 1'b1;
                if (pending_wide) if_req <= 1'b1; else pf_req <= 1'b1;
            end else begin
            pending_wide <= fast_ifetch && is_rom;
            if (fast_ifetch && is_rom) begin if_req <= 1'b1; if_addr_q <= fetch_line[23:0]; end
            else begin pf_req <= 1'b1; pf_addr <= fetch_line; end
            end""")]),
    "ack_extend": (STD, "after a stale acknowledge the request is kept raised over the held acknowledge (consumed twice)", [
        ("reg         stale;", "reg         stale, dup;"),
        ("nl <= 3'd0; stale <= 1'b0; pending <= 1'b0; half <= 1'b0;",
         "nl <= 3'd0; stale <= 1'b0; dup <= 1'b0; pending <= 1'b0; half <= 1'b0;"),
        ("            stale <= pending;", "            stale <= pending; dup <= pending;"),
        ("if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end",
         "if (!cur && dup) begin dup <= 1'b0; half <= 1'b0;"
         " if (pending_wide) if_req <= 1'b1; else pf_req <= 1'b1; end\n"
         "            else if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end")]),
    "stale_half": (STD, "after a stale first half, the second half is still fetched (and dropped)", [
        ("if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end",
         "if (!cur && (half || pending_wide)) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end\n"
         "            else if (!cur) begin half <= 1'b1; pf_req <= 1'b1; pf_addr <= fetch_line + 32'd4; end")]),
    "shifted": (STD, "both head and fetch pointer resume one byte past the redirect target", [
        ("head_pc    <= redirect ? redirect_pc : head_pc;",
         "head_pc    <= redirect ? redirect_pc + 32'd1 : head_pc;"),
        ("fetch_line <= redirect ? {redirect_pc[31:3], 3'b000} : head_line;",
         "fetch_line <= redirect ? ((redirect_pc + 32'd1) & ~32'd7) : head_line;")]),
    "smc_no_half": (STD, "SMC snoop ignores the captured first half of the line being filled (C1-2)", [
        ("((pending && half && !stale) ? 6'd4 : 6'd0)", "6'd0")]),
    "smc_nonmodular": (STD, "SMC snoop width-aware but not modular across 0xFFFFFFFF -> 0 (C1-2w)", [
        ("((w_off < {26'd0, q_len}) || (q_off < {29'd0, w_len}))",
         "((dwr_addr + {29'd0, w_len} > head_line) && (dwr_addr < head_line + {26'd0, q_len}))")]),
    "smc_no_width": (STD, "SMC snoop tests the write's start address only (C1-3)", [
        ("(q_off < {29'd0, w_len})", "1'b0")]),
    "bq_smc_no_width": (BQ, "byte queue: SMC snoop tests the write's start address only (C1-3)", [
        ("(snoop_back < {29'd0, snoop_len})", "1'b0")]),
    "bq_withdraw": (BQ, "D1 reintroduced in the byte queue", [
        ("""        end else begin
            stale <= pending;
        end""",
         """        end else begin
            stale <= 1'b0; pending <= 1'b0; if_req <= 1'b0; pf_req <= 1'b0;
        end""")]),
    "bq_no_stale": (BQ, "byte queue: the outstanding request is not marked stale", [
        ("            stale <= pending;", "            stale <= 1'b0;")]),
}


def main():
    if sys.argv[1:] == ["--list"]:
        for name, (f, what, _) in MUTANTS.items():
            print(name, "byteq" if f == BQ else "std", what)
        return
    rtl, out = sys.argv[1], sys.argv[2]
    for name, (f, _, reps) in MUTANTS.items():
        d = os.path.join(out, name)
        os.makedirs(d, exist_ok=True)
        for g in (STD, BQ, "v2_bus.sv"):
            shutil.copy(os.path.join(rtl, g), d)
        p = os.path.join(d, f)
        s = open(p).read()
        for old, new in reps:
            if s.count(old) != 1:
                sys.exit(f"mutant {name}: pattern occurs {s.count(old)} times in {f}: {old[:60]!r}")
            s = s.replace(old, new)
        open(p, "w").write(s)


if __name__ == "__main__":
    main()
