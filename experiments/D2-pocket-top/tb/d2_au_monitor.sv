// SPDX-License-Identifier: GPL-3.0-or-later
//
// TESTBENCH ONLY (D2 Pocket top, au configuration; exp/audio1, experiments/AUDIO/PLAN.md step A2): observes the sound subsystem
// through hierarchical references (drives nothing) and prints event / per-frame lines for the A2 criteria:
//   AUZ80 RESET   the Z80 reset (s32_soundsys rst | z80_reset = io0 CNT2 low) changes (frame, clk_sys count)
//   AUVSHW        every V60 write to shared RAM (0x700000-0x701FFF): frame, retired-instruction count, byte address, data, be
//   AUVSHR        every V60 read of shared RAM: frame, retired count, byte address, be, the data the V60 received
//   AUZSHR        a Z80 read of shared RAM byte 0xE000 (the command byte) whose value differs from the previous one read there
//   AUZIO         every Z80 I/O write (YM1 80-83, YM2 90-93, banks A0 / B0, IRQ D0-D7, C0-CF): frame, port, data
//   AURF          every RF5C68 register write (Z80 C000-CFFF): frame, register (addr[3:0]), data
//   AUFIRST       the first V60 read of a shared-RAM byte the Z80 has written (risk R5): frame, retired count, address
//   AUF           per frame (vblank of the core video, the C++ bench's frame number): Z80 opcode fetches / memory reads and writes /
//                 WAIT clk_sys / YM1 YM2 data writes / RF register writes / wave writes / wave reads (CPU, voice) / p3 requests /
//                 shared-RAM Z80 writes and reads / V60 writes and reads / audio register updates and min / max
//   AUS           the ce_fm-sampled output stream (what sound_i2s receives): `AUS <ce_fm count> R <l> <r>` whenever the sample register
//                 changes, and `AUS <n> P3 <l> <r>` whenever the PREDICTOR changes = the combinational s32_core audio output 3 clk_sys before
//                 each ce_fm edge (a build whose output path is exactly 3 clk_sys longer must produce the old build's P3 stream as its R stream)
//   AUEND         totals, the wave-RAM client's status (clear done, watchdogs), the RF5C68 handshake counts
//   AULAT         (GA2Audio3) measured request-to-acknowledge latency in clk_sys of the core's p3 port (Z80 sound ROM: sp_sdram in au,
//                 d2_rom_psram in au3), the wave-read port p4 and the wave-write port (d2_wave_psram): count, min, mean, max, histogram
// Frame counting: rising edges of the core's vb while the core is out of reset, the same count as tb_d2.cpp's CF lines.
`define AU_IC  $root.d2_tb.ic
`define AU_SND $root.d2_tb.ic.core.sound
module d2_au_monitor (
    input clk      // clk_sys
);
wire        rst_core = `AU_IC.rst_r;
wire        z_rst    = `AU_SND.rst | `AU_SND.z80_reset;
wire [15:0] za = `AU_SND.z_addr;
wire  [7:0] zdo = `AU_SND.z_dout, zdi = `AU_SND.z_din;
wire        z_mem_rd = `AU_SND.z_mem_rd, z_mem_wr = `AU_SND.z_mem_wr, z_io_wr = `AU_SND.z_io_wr;
wire        z_m1 = !`AU_SND.z_m1_n && !`AU_SND.z_mreq_n && !`AU_SND.z_rd_n;
wire        z_wait = !`AU_SND.z_wait_n;
wire        ce_z80 = `AU_SND.ce_z80;
wire        v_req = `AU_IC.core.m_req, v_we = `AU_IC.core.m_we, v_ack = `AU_IC.core.m_ack;
wire [23:0] v_a = `AU_IC.core.A;
wire  [1:0] v_be = `AU_IC.core.m_be;
wire [15:0] v_wd = `AU_IC.core.m_wdata, v_rd = `AU_IC.core.m_rdata;
wire        v_sh = (`AU_IC.core.A[23:20] == 4'h7);
wire [31:0] retired = `AU_IC.core.v60.core.obs.retired;
wire        rf_reg_we = `AU_SND.rf5c68.cs && `AU_SND.rf5c68.we && !`AU_SND.rf5c68.addr[12];
wire        rf_wave_we = `AU_SND.rf5c68.cs && `AU_SND.rf5c68.we && `AU_SND.rf5c68.addr[12];
wire        rf_wave_rd = `AU_SND.rf5c68.cs && !`AU_SND.rf5c68.we && `AU_SND.rf5c68.addr[12];
wire        wrq = `AU_SND.wave_rd_req, wwq = `AU_SND.wave_wr_req, wrv = `AU_SND.rf5c68.wave_rd_voice;
wire        p3q = `AU_IC.au_p3_req;
wire signed [15:0] aud_l = `AU_IC.aud_l_r, aud_r = `AU_IC.aud_r_r;

integer frame = 0, sys = 0;
reg vb_q = 1'b0, zr_q = 1'b1;
reg m1_q = 0, mrd_q = 0, mwr_q = 0, iow_q = 0, rfw_q = 0, wvw_q = 0, wvr_q = 0, wrq_q = 0, wwq_q = 0, p3q_q = 0, vsh_q = 0, vack_q = 0;
// per-frame and total counters
integer f_m1, f_mrd, f_mwr, f_wait, f_ym1, f_ym2, f_rf, f_ww, f_wr_cpu, f_wr_voice, f_p3, f_zshw, f_zshr, f_vshw, f_vshr, f_aud;
integer t_m1 = 0, t_wait = 0, t_ym1 = 0, t_ym2 = 0, t_rf = 0, t_ww = 0, t_wr_cpu = 0, t_wr_voice = 0, t_p3 = 0, t_vshw = 0, t_vshr = 0, t_aud = 0;
integer t_zshw = 0, t_zshr = 0, z_run_sys = 0;
integer f_aud_min, f_aud_max, t_aud_nz = 0;
reg   [7:0] last_e000 = 8'h00;
reg         e000_seen = 1'b0;
reg   [7:0] zrd_val = 8'h00;
reg  [15:0] zrd_addr = 16'h0;
reg         zwritten [0:8191];
reg         first_done = 1'b0;
reg signed [15:0] aud_lq = 0, aud_rq = 0;
// ce_fm-sampled stream and the 3-clk_sys predictor (history of the combinational core output)
wire signed [15:0] core_l = `AU_IC.core_audio_l, core_r = `AU_IC.core_audio_r;
wire               ce_fm_i = `AU_IC.ce_fm;
reg  signed [15:0] hl1 = 0, hr1 = 0, hl2 = 0, hr2 = 0, hl3 = 0, hr3 = 0, pl = 0, pr = 0, sl = 0, sr = 0;
reg                ce_d = 1'b0;
integer            n_ce = 0, n_s_chg = 0, n_p_chg = 0;
integer i;
// ---- GA2Audio3: request -> acknowledge latency (clk_sys, rising edge of req to rising edge of ack) of p3, p4 and the wave write
wire lat_p3a = `AU_IC.au_p3_ack, lat_p4q = `AU_IC.c_p4_req, lat_p4a = `AU_IC.c_p4_ack, lat_wwq = `AU_IC.c_wave_req, lat_wwa = `AU_IC.c_wave_ack;
reg  lq3 = 0, la3 = 0, lq4 = 0, la4 = 0, lqw = 0, law = 0;
integer lt3 = 0, lt4 = 0, ltw = 0;
longint ln [0:2], lsum [0:2]; integer lmin [0:2], lmax [0:2]; integer lhist [0:2][0:63];
initial for (int c = 0; c < 3; c++) begin ln[c] = 0; lsum[c] = 0; lmin[c] = 1 << 30; lmax[c] = 0; for (int k = 0; k < 64; k++) lhist[c][k] = 0; end
task automatic lat_add(input integer c, input integer v);
    ln[c]++; lsum[c] += v; if (v < lmin[c]) lmin[c] = v; if (v > lmax[c]) lmax[c] = v; lhist[c][(v > 63) ? 63 : v]++;
endtask
always @(posedge clk) begin
    lq3 <= p3q; la3 <= lat_p3a; lq4 <= lat_p4q; la4 <= lat_p4a; lqw <= lat_wwq; law <= lat_wwa;
    if (p3q && !lq3) lt3 = sys; if (lat_p3a && !la3) lat_add(0, sys - lt3);
    if (lat_p4q && !lq4) lt4 = sys; if (lat_p4a && !la4) lat_add(1, sys - lt4);
    if (lat_wwq && !lqw) ltw = sys; if (lat_wwa && !law) lat_add(2, sys - ltw);
end
task automatic clr_frame;
    f_m1 = 0; f_mrd = 0; f_mwr = 0; f_wait = 0; f_ym1 = 0; f_ym2 = 0; f_rf = 0; f_ww = 0; f_wr_cpu = 0; f_wr_voice = 0;
    f_p3 = 0; f_zshw = 0; f_zshr = 0; f_vshw = 0; f_vshr = 0; f_aud = 0; f_aud_min = 32767; f_aud_max = -32768;
endtask
initial begin clr_frame(); for (i = 0; i < 8192; i = i + 1) zwritten[i] = 1'b0; end

always @(posedge clk) begin
    sys <= sys + 1;
    // ---- Z80 reset
    zr_q <= z_rst;
    if (z_rst != zr_q) $display("AUZ80 RESET %s f=%0d sys=%0d (board rst=%0d z80_reset(~io0_cnt2)=%0d)", z_rst ? "ASSERT" : "RELEASE",
                                frame, sys, `AU_SND.rst, `AU_SND.z80_reset);
    if (!z_rst) z_run_sys <= z_run_sys + 1;
    // ---- frames (as tb_d2.cpp's nvbl: vb rising while the core runs)
    if (!rst_core) begin
        vb_q <= `AU_IC.vb;
        if (`AU_IC.vb && !vb_q) begin
            frame = frame + 1;
            $display("AUF %0d m1=%0d mrd=%0d mwr=%0d wait_sys=%0d ym1_w=%0d ym2_w=%0d rf_w=%0d wave_w=%0d wave_rd_cpu=%0d wave_rd_voice=%0d p3=%0d zsh_w=%0d zsh_r=%0d vsh_w=%0d vsh_r=%0d aud_upd=%0d aud_l_min=%0d aud_l_max=%0d",
                     frame, f_m1, f_mrd, f_mwr, f_wait, f_ym1, f_ym2, f_rf, f_ww, f_wr_cpu, f_wr_voice, f_p3, f_zshw, f_zshr, f_vshw, f_vshr, f_aud,
                     f_aud == 0 ? 0 : f_aud_min, f_aud == 0 ? 0 : f_aud_max);
            clr_frame();
        end
    end
    // ---- Z80 bus (edge per bus cycle)
    m1_q <= z_m1; mrd_q <= z_mem_rd; mwr_q <= z_mem_wr; iow_q <= z_io_wr;
    if (z_wait && !z_rst) begin f_wait = f_wait + 1; t_wait = t_wait + 1; end
    if (z_m1 && !m1_q) begin f_m1 = f_m1 + 1; t_m1 = t_m1 + 1; end
    if (z_mem_rd) begin zrd_val <= zdi; zrd_addr <= za; end
    if (!z_mem_rd && mrd_q) begin                       // end of a Z80 memory read: the value it took
        f_mrd = f_mrd + 1;
        if (zrd_addr[15:13] == 3'b111) begin
            f_zshr = f_zshr + 1; t_zshr = t_zshr + 1;
            if (zrd_addr == 16'hE000 && (!e000_seen || zrd_val != last_e000)) begin
                $display("AUZSHR f=%0d sys=%0d addr=e000 val=%02x (was %02x)", frame, sys, zrd_val, last_e000);
                last_e000 <= zrd_val; e000_seen <= 1'b1;
            end
        end
    end
    if (z_mem_wr && !mwr_q) begin
        f_mwr = f_mwr + 1;
        if (za[15:13] == 3'b111) begin f_zshw = f_zshw + 1; t_zshw = t_zshw + 1; zwritten[za[12:0]] = 1'b1; end
    end
    if (z_io_wr && !iow_q) begin
        if (za[7:4] == 4'h8 && za[0]) begin f_ym1 = f_ym1 + 1; t_ym1 = t_ym1 + 1; end
        if (za[7:4] == 4'h9 && za[0]) begin f_ym2 = f_ym2 + 1; t_ym2 = t_ym2 + 1; end
        $display("AUZIO f=%0d port=%02x data=%02x", frame, za[7:0], zdo);
    end
    // ---- RF5C68
    rfw_q <= rf_reg_we; wvw_q <= rf_wave_we; wvr_q <= rf_wave_rd;
    if (rf_reg_we && !rfw_q) begin f_rf = f_rf + 1; t_rf = t_rf + 1; $display("AURF f=%0d reg=%0d data=%02x", frame, `AU_SND.rf5c68.addr[3:0], `AU_SND.rf5c68.wdata); end
    if (rf_wave_we && !wvw_q) begin f_ww = f_ww + 1; t_ww = t_ww + 1; end
    wrq_q <= wrq; wwq_q <= wwq; p3q_q <= p3q;
    if (wrq && !wrq_q) begin if (wrv) begin f_wr_voice = f_wr_voice + 1; t_wr_voice = t_wr_voice + 1; end
                                  else begin f_wr_cpu = f_wr_cpu + 1; t_wr_cpu = t_wr_cpu + 1; end end
    if (p3q && !p3q_q) begin f_p3 = f_p3 + 1; t_p3 = t_p3 + 1; end
    // ---- V60 shared-RAM accesses (one line per bus cycle; the read value is m_rdata at the acknowledge)
    vsh_q <= v_req && v_sh; vack_q <= v_req && v_sh && v_ack;
    if (v_req && v_sh && v_we && !vsh_q) begin
        f_vshw = f_vshw + 1; t_vshw = t_vshw + 1;
        $display("AUVSHW f=%0d i=%0d a=%06x d=%04x be=%0d%0d", frame, retired, {v_a[23:1], 1'b0}, v_wd, v_be[1], v_be[0]);
    end
    if (v_req && v_sh && !v_we && v_ack && !vack_q) begin
        f_vshr = f_vshr + 1; t_vshr = t_vshr + 1;
        $display("AUVSHR f=%0d i=%0d a=%06x be=%0d%0d d=%04x", frame, retired, {v_a[23:1], 1'b0}, v_be[1], v_be[0], v_rd);
        if (!first_done && ((v_be[0] && zwritten[{v_a[12:1], 1'b0}]) || (v_be[1] && zwritten[{v_a[12:1], 1'b1}]))) begin
            first_done <= 1'b1;
            $display("AUFIRST f=%0d i=%0d a=%06x be=%0d%0d d=%04x: first V60 read of a shared-RAM byte the Z80 wrote", frame, retired,
                     {v_a[23:1], 1'b0}, v_be[1], v_be[0], v_rd);
        end
    end
    // ---- ce_fm-sampled stream (R) and the predictor (P3: core output 3 clk_sys before the ce_fm edge)
    hl1 <= core_l; hr1 <= core_r; hl2 <= hl1; hr2 <= hr1; hl3 <= hl2; hr3 <= hr2;
    ce_d <= ce_fm_i;
    if (ce_fm_i) begin
        n_ce = n_ce + 1;
        if (hl3 != pl || hr3 != pr) begin pl <= hl3; pr <= hr3; n_p_chg = n_p_chg + 1; $display("AUS %0d P3 %0d %0d", n_ce, hl3, hr3); end
    end
    if (ce_d && (aud_l != sl || aud_r != sr)) begin sl <= aud_l; sr <= aud_r; n_s_chg = n_s_chg + 1; $display("AUS %0d R %0d %0d", n_ce, aud_l, aud_r); end
    // ---- audio register (the sound_i2s input)
    aud_lq <= aud_l; aud_rq <= aud_r;
    if (aud_l != aud_lq || aud_r != aud_rq) begin
        f_aud = f_aud + 1; t_aud = t_aud + 1;
        if (aud_l != 0 || aud_r != 0) t_aud_nz = t_aud_nz + 1;
        if (aud_l < f_aud_min) f_aud_min = aud_l;
        if (aud_l > f_aud_max) f_aud_max = aud_l;
    end
end
final begin
    $display("AUEND frames=%0d z80_run_sys=%0d m1=%0d wait_sys=%0d ym1_w=%0d ym2_w=%0d rf_w=%0d wave_w=%0d wave_rd_cpu=%0d wave_rd_voice=%0d p3=%0d zsh_w=%0d zsh_r=%0d vsh_w=%0d vsh_r=%0d aud_upd=%0d aud_upd_nonzero=%0d first_v60_read_of_z80_data=%0d",
             frame, z_run_sys, t_m1, t_wait, t_ym1, t_ym2, t_rf, t_ww, t_wr_cpu, t_wr_voice, t_p3, t_zshw, t_zshr, t_vshw, t_vshr, t_aud, t_aud_nz, first_done);
    $display("AUSEND ce_fm=%0d stream_changes=%0d p3_changes=%0d", n_ce, n_s_chg, n_p_chg);
    $display("AUWAVE clear_done=%0d wd_rd=%0d wd_wr=%0d", `AU_IC.wv_clear_done, `AU_IC.wv_wd_rd, `AU_IC.wv_wd_wr);
    for (int c = 0; c < 3; c++) begin
        automatic string h = "";
        for (int k = 0; k < 64; k++) if (lhist[c][k] != 0) h = {h, $sformatf(" %0d:%0d", k, lhist[c][k])};
        $display("AULAT %s n=%0d min=%0d mean=%.3f max=%0d (clk_sys, req rise -> ack rise) hist%s", (c == 0) ? "p3_sound_rom" : (c == 1) ? "p4_wave_read" : "wave_write",
                 ln[c], (ln[c] != 0) ? lmin[c] : 0, (ln[c] != 0) ? real'(lsum[c]) / real'(ln[c]) : 0.0, lmax[c], h);
    end
end
endmodule
`undef AU_IC
`undef AU_SND
