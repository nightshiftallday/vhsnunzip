`timescale 1ns / 1ps

// Pseudo-random handshake gate for throttling a stream. A cycle is enabled
// when (lfsr & mask) == 0, so mask 0 never throttles, mask 'h3 passes about
// 1 in 4 cycles and mask 'h1f about 1 in 32.
module vhsnunzip_harness_gate #(
    parameter logic [15:0] SEED = 16'hACE1
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [15:0] mask,
    output logic        enable
);

logic [15:0] lfsr;

always_ff @(posedge clk) begin
    if (!rst_n) begin
        lfsr <= SEED;
    end else begin
        lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
    end
end

assign enable = (lfsr & mask) == '0;

endmodule
