`timescale 1ns / 1ps

// Splits 64-byte AXI stream beats into BYTES-wide lines for the vhsnunzip co
// stream. tlast marks the end of a Snappy chunk. Only the tlast beat may be
// partial, and its tkeep must be contiguous from bit 0 (Coyote host streams
// are packed). The result is the normalized stream the core expects: full
// lines, and a last line whose co_cnt gives the valid bytes (0 = full).
// One line per cycle, with no bubble between beats.
module vhsnunzip_harness_axis_in #(
    parameter int BYTES = 16
) (
    input  logic                      clk,
    input  logic                      rst_n,

    input  logic [511:0]              s_tdata,
    input  logic [63:0]               s_tkeep,
    input  logic                      s_tlast,
    input  logic                      s_tvalid,
    output logic                      s_tready,

    output logic                      co_valid,
    input  logic                      co_ready,
    output logic [BYTES*8-1:0]        co_data,
    output logic [$clog2(BYTES)-1:0]  co_cnt,
    output logic                      co_last
);

localparam int N_LINES = 64 / BYTES;
localparam int LI      = N_LINES > 1 ? $clog2(N_LINES) : 1;

initial begin
    if (BYTES < 2 || BYTES > 64 || (BYTES & (BYTES - 1)) != 0) begin
        $fatal(1, "vhsnunzip_harness_axis_in: BYTES must be a power of two in [2, 64]");
    end
end

logic                     beat_valid;
logic [511:0]             beat_data;
logic                     beat_last;
logic [LI-1:0]            beat_last_idx; // index of the last line holding data
logic [$clog2(BYTES)-1:0] beat_last_cnt; // valid bytes in that line, 0 = full
logic [LI-1:0]            idx;

// Last line with data and its byte count, from a contiguous tkeep
logic [LI-1:0]            keep_last_idx;
logic [BYTES-1:0]         keep_last_line;
logic [$clog2(BYTES)-1:0] keep_last_cnt;
always_comb begin
    keep_last_idx = '0;
    for (int unsigned i = 0; i < N_LINES; i++) begin
        if (s_tkeep[i*BYTES]) keep_last_idx = LI'(i);
    end
    keep_last_line = s_tkeep[keep_last_idx*BYTES +: BYTES];
    keep_last_cnt  = '0;
    for (int unsigned b = 1; b < BYTES; b++) begin
        if (keep_last_line[b-1] && !keep_last_line[b]) keep_last_cnt = $clog2(BYTES)'(b);
    end
end

logic at_last_line;
assign at_last_line = idx == beat_last_idx;

assign s_tready = !beat_valid || (co_ready && at_last_line);

assign co_valid = beat_valid;
assign co_data  = beat_data[idx*BYTES*8 +: BYTES*8];
assign co_last  = beat_last && at_last_line;
assign co_cnt   = at_last_line ? beat_last_cnt : '0;

always_ff @(posedge clk) begin
    if (!rst_n) begin
        beat_valid <= 1'b0;
        idx        <= '0;
    end else if (s_tvalid && s_tready) begin
        beat_valid    <= 1'b1;
        beat_data     <= s_tdata;
        beat_last     <= s_tlast;
        // Non-last beats are full, so only the tlast beat can end early
        beat_last_idx <= s_tlast ? keep_last_idx : LI'(N_LINES - 1);
        beat_last_cnt <= s_tlast ? keep_last_cnt : '0;
        idx           <= '0;
    end else if (co_valid && co_ready) begin
        if (at_last_line) begin
            beat_valid <= 1'b0;
        end else begin
            idx <= idx + 1'b1;
        end
    end
end

endmodule
