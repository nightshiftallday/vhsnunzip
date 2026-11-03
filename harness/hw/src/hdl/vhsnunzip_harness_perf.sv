`timescale 1ns / 1ps

// Performance counters observing the co and de ports of the core.
//
// A cycle is "active" while at least one chunk is in flight: from the first co
// handshake of a chunk up to and including the de_last handshake. Idle time
// between chunks, when the host is not feeding the core, is not counted.
// Every active cycle falls into exactly one of these classes:
//   DE_LINES    de handshake (the core delivers a line)
//   DE_BLOCKED  de_valid && !de_ready (output backpressure)
//   CORE_STALL  !de_valid && co_valid && !co_ready (input waiting, core busy)
//   IN_EMPTY    !de_valid && !co_valid (no input offered: starvation or drain)
//   IN_ONLY     !de_valid && co_valid && co_ready (core takes input, no output)
// so DE_LINES + DE_BLOCKED + CORE_STALL + IN_EMPTY + IN_ONLY == CYCLES.
//
// Latency is sampled for isolated chunks only (no other chunk in flight when
// the chunk starts): cycles from its first co handshake to its first de
// handshake. Run with one chunk in flight to sample every chunk.
//
// The counter indices here are the CSR indices in vhsnunzip_harness_csr; keep
// them in sync with harness/python/vhsnunzip_harness/regs.py and
// harness/sw/src/regs.hpp.
module vhsnunzip_harness_perf #(
    parameter int BYTES = 16,
    parameter int CW    = 48 // counter width
) (
    input  logic                       clk,
    input  logic                       rst_n,
    input  logic                       clear,

    input  logic                       co_valid,
    input  logic                       co_ready,
    input  logic [$clog2(BYTES)-1:0]   co_cnt,
    input  logic                       co_last,

    input  logic                       de_valid,
    input  logic                       de_ready,
    input  logic [$clog2(BYTES):0]     de_cnt,
    input  logic                       de_last,

    output logic [CW-1:0]              timestamp,
    output logic [CW-1:0]              cycles,
    output logic [CW-1:0]              chunks_in,
    output logic [CW-1:0]              chunks_out,
    output logic [CW-1:0]              co_lines,
    output logic [CW-1:0]              co_bytes,
    output logic [CW-1:0]              co_stall,
    output logic [CW-1:0]              de_lines,
    output logic [CW-1:0]              de_bytes,
    output logic [CW-1:0]              de_blocked,
    output logic [CW-1:0]              core_stall,
    output logic [CW-1:0]              in_empty,
    output logic [CW-1:0]              in_only,
    output logic [CW-1:0]              lat_count,
    output logic [CW-1:0]              lat_sum,
    output logic [CW-1:0]              lat_max,
    output logic [15:0]                in_flight
);

logic co_fire, de_fire;
assign co_fire = co_valid && co_ready;
assign de_fire = de_valid && de_ready;

// First co / de line of a chunk
logic co_first, de_first;
logic start, finish, active;
assign start  = co_fire && co_first;
assign finish = de_fire && de_last;
assign active = in_flight != 0 || start;

logic [CW-1:0] lat_cur;
logic          lat_run;

always_ff @(posedge clk) begin
    if (!rst_n) begin
        co_first  <= 1'b1;
        de_first  <= 1'b1;
        in_flight <= '0;
        lat_run   <= 1'b0;
        timestamp <= '0;
    end else begin
        timestamp <= timestamp + 1'b1;
        if (co_fire) co_first <= co_last;
        if (de_fire) de_first <= de_last;
        in_flight <= in_flight + start - finish;

        if (start && in_flight == 0) begin
            lat_run <= 1'b1;
            lat_cur <= 1;
        end else if (lat_run) begin
            lat_cur <= lat_cur + 1'b1;
        end
        if (lat_run && de_fire && de_first) begin
            lat_run <= 1'b0;
        end
    end
end

always_ff @(posedge clk) begin
    if (!rst_n || clear) begin
        cycles     <= '0;
        chunks_in  <= '0;
        chunks_out <= '0;
        co_lines   <= '0;
        co_bytes   <= '0;
        co_stall   <= '0;
        de_lines   <= '0;
        de_bytes   <= '0;
        de_blocked <= '0;
        core_stall <= '0;
        in_empty   <= '0;
        in_only    <= '0;
        lat_count  <= '0;
        lat_sum    <= '0;
        lat_max    <= '0;
    end else begin
        if (active) begin
            cycles <= cycles + 1'b1;
            if (co_valid && !co_ready) co_stall <= co_stall + 1'b1;
            if (!de_valid) begin
                if (!co_valid)     in_empty   <= in_empty + 1'b1;
                else if (co_ready) in_only    <= in_only + 1'b1;
                else               core_stall <= core_stall + 1'b1;
            end else if (!de_ready) begin
                de_blocked <= de_blocked + 1'b1;
            end
        end
        if (co_fire) begin
            co_lines <= co_lines + 1'b1;
            co_bytes <= co_bytes + (co_cnt == '0 ? CW'(BYTES) : CW'(co_cnt));
            if (co_last) chunks_in <= chunks_in + 1'b1;
        end
        if (de_fire) begin
            de_lines <= de_lines + 1'b1;
            de_bytes <= de_bytes + CW'(de_cnt);
            if (de_last) chunks_out <= chunks_out + 1'b1;
        end
        if (lat_run && de_fire && de_first) begin
            lat_count <= lat_count + 1'b1;
            lat_sum   <= lat_sum + lat_cur;
            if (lat_cur > lat_max) lat_max <= lat_cur;
        end
    end
end

endmodule
