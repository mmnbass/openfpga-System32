// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_bus: the V60 bus interface, with exactly the logical port contract of
// upstream s32_v60 (bus_req/we/addr/size/wdata/rdata/ack, 32-bit logical,
// size 0=B 1=H 2=W). Misalignment splitting and the PCB bus cadence stay in
// upstream's s32_v60_bus adapter, outside the CPU, as today.
//
//   * data-priority arbiter between microcode data accesses and the fetch
//     unit's 32-bit prefetches; one owner per transaction, held to the ack
//   * read data sign/zero-extended by size into MDR; ack/done to microcode
//   * write data from MDR; a write's address and size go to the fetch unit's
//     self-modifying-code snoop (the size is the same held `size` the adapter
//     takes as c_size; the logical bus port itself is unchanged)

module v2_bus (
    input             clk,
    input             rst,
    // microcode data access
    input       [1:0] op,            // start pulse: 0 none, 1 read, 2 write, 3 RMW
    input       [1:0] size,
    input             sx,            // sign-extend read data
    input      [31:0] mar,
    input      [31:0] mdr_out,
    output reg [31:0] mdr_in,
    output reg        mdr_in_valid,
    output            data_busy,     // stall microcode while the access runs
    // prefetch from v2_fetch
    input             pf_req,
    input      [31:0] pf_addr,
    output     [31:0] pf_rdata,
    output            pf_ack,
    // snoop
    output            dwr_valid,
    output     [31:0] dwr_addr,
    output      [1:0] dwr_size,      // 0=B 1=H 2=W, as bus_size
    // logical bus (to s32_v60_bus)
    output            bus_req,
    output            bus_we,
    output     [31:0] bus_addr,
    output      [1:0] bus_size,
    output     [31:0] bus_wdata,
    input      [31:0] bus_rdata,
    input             bus_ack
);

localparam [1:0] OWN_NONE = 2'd0, OWN_D = 2'd1, OWN_PF = 2'd2;
reg  [1:0] owner;
reg        d_active, d_write_phase, req_d;
reg  [1:0] d_op;

wire want_d  = (op != 2'd0) || d_active;
wire sel_d   = (owner == OWN_D)  || (owner == OWN_NONE && want_d && !req_d);
wire sel_pf  = (owner == OWN_PF) || (owner == OWN_NONE && !want_d && pf_req && !req_d);

wire d_we    = d_active ? d_write_phase : (op == 2'd2);
wire d_rmw   = d_active ? (d_op == 2'd3) : (op == 2'd3);
assign bus_req   = sel_d ? want_d : (sel_pf ? pf_req : 1'b0);
assign bus_we    = sel_d ? d_we : 1'b0;
assign bus_addr  = sel_d ? mar : pf_addr;
assign bus_size  = sel_d ? size : 2'd2;
assign bus_wdata = sel_d ? mdr_out : 32'd0;
assign pf_rdata  = bus_rdata;
assign pf_ack    = bus_ack && (owner == OWN_PF);
assign dwr_valid = bus_ack && (owner == OWN_D) && d_we;
assign dwr_addr  = mar;
assign dwr_size  = size;
assign data_busy = want_d;

always @(posedge clk) begin
    if (rst) begin
        owner <= OWN_NONE; d_active <= 1'b0; d_write_phase <= 1'b0;
        req_d <= 1'b0; mdr_in_valid <= 1'b0; mdr_in <= 32'd0;
    end else begin
        req_d <= bus_req;
        mdr_in_valid <= 1'b0;
        // latch a start pulse at once, even while a prefetch owns the bus
        if (op != 2'd0 && !d_active) begin
            d_active      <= 1'b1;
            d_op          <= op;
            d_write_phase <= (op == 2'd2);
        end
        if (owner == OWN_NONE) begin
            if (sel_d && want_d) owner <= OWN_D;
            else if (sel_pf && pf_req) owner <= OWN_PF;
        end else if (bus_ack) begin
            owner <= OWN_NONE;
            if (owner == OWN_D) begin
                if (!d_write_phase) begin
                    case (size)
                        2'd0: mdr_in <= sx ? {{24{bus_rdata[7]}}, bus_rdata[7:0]}
                                           : {24'd0, bus_rdata[7:0]};
                        2'd1: mdr_in <= sx ? {{16{bus_rdata[15]}}, bus_rdata[15:0]}
                                           : {16'd0, bus_rdata[15:0]};
                        default: mdr_in <= bus_rdata;
                    endcase
                    mdr_in_valid <= 1'b1;
                end
                // RMW: the read phase is followed by a write phase
                if (!d_write_phase && d_rmw) d_write_phase <= 1'b1;
                else d_active <= 1'b0;
            end
        end
    end
end

endmodule
