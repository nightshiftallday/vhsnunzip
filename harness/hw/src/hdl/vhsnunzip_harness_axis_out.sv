`timescale 1ns / 1ps

// Packs the BYTES-wide vhsnunzip de stream into 64-byte AXI stream beats.
//
// Coyote requires exactly ceil(len/64) beats per write request with tlast on
// the final one. The core's de stream is full lines except the last line of a
// chunk, which can be empty (de_cnt = 0, de_dvalid = 0). If that empty line
// starts a new beat, the packer produces a "ghost" beat (tkeep = 0, tlast = 1).
// The second stage holds each beat until the next one is known and folds a
// ghost into the held beat's tlast. A chunk that decompresses to 0 bytes
// produces no beat at all.
module vhsnunzip_harness_axis_out #(
    parameter int BYTES = 16
) (
    input  logic                       clk,
    input  logic                       rst_n,

    input  logic                       de_valid,
    output logic                       de_ready,
    input  logic                       de_dvalid,
    input  logic [BYTES*8-1:0]         de_data,
    input  logic [$clog2(BYTES):0]     de_cnt,
    input  logic                       de_last,

    output logic [511:0]               m_tdata,
    output logic [63:0]                m_tkeep,
    output logic                       m_tlast,
    output logic                       m_tvalid,
    input  logic                       m_tready
);

localparam int N_LINES = 64 / BYTES;
localparam int LI      = N_LINES > 1 ? $clog2(N_LINES) : 1;

// -- Stage 1: accumulate lines into a beat ------------------------------------
logic [511:0]  acc_data;
logic [63:0]   acc_keep;
logic          acc_last;
logic          acc_full; // acc holds a finished beat
logic [LI-1:0] slot;     // next line slot

logic          p_ready;
logic          acc_ghost;
assign acc_ghost = acc_last && acc_keep == '0;

assign de_ready = !acc_full || p_ready;

logic [BYTES-1:0] line_keep;
always_comb begin
    for (int b = 0; b < BYTES; b++) begin
        line_keep[b] = de_dvalid && b < de_cnt;
    end
end

always_ff @(posedge clk) begin
    if (!rst_n) begin
        acc_data <= '0;
        acc_keep <= '0;
        acc_last <= 1'b0;
        acc_full <= 1'b0;
        slot     <= '0;
    end else begin
        if (acc_full && p_ready) begin
            acc_full <= 1'b0;
        end
        if (de_valid && de_ready) begin
            acc_data[slot*BYTES*8 +: BYTES*8] <= de_data;
            // A new beat starts with all other lanes cleared
            if (slot == '0) begin
                acc_keep <= 64'(line_keep);
            end else begin
                acc_keep[slot*BYTES +: BYTES] <= line_keep;
            end
            if (de_last || slot == LI'(N_LINES - 1)) begin
                acc_full <= 1'b1;
                acc_last <= de_last;
                slot     <= '0;
            end else begin
                slot <= slot + 1'b1;
            end
        end
    end
end

// -- Stage 2: hold one beat to fold ghost beats into tlast -------------------
logic [511:0] hold_data;
logic [63:0]  hold_keep;
logic         hold_last;
logic         hold_valid;

// The held beat goes out once it is known to be last, or the next beat exists
assign m_tvalid = hold_valid && (hold_last || acc_full);
assign m_tdata  = hold_data;
assign m_tkeep  = hold_keep;
assign m_tlast  = hold_last || (acc_full && acc_ghost);

assign p_ready  = !hold_valid || (m_tvalid && m_tready);

always_ff @(posedge clk) begin
    if (!rst_n) begin
        hold_data  <= '0;
        hold_keep  <= '0;
        hold_last  <= 1'b0;
        hold_valid <= 1'b0;
    end else if (acc_full && p_ready && !acc_ghost) begin
        hold_data  <= acc_data;
        hold_keep  <= acc_keep;
        hold_last  <= acc_last;
        hold_valid <= 1'b1;
    end else if (m_tvalid && m_tready) begin
        hold_valid <= 1'b0;
    end
end

endmodule
