// vhsnunzip benchmark harness (vFPGA top).
//
// axis_host_recv[0] -> reg -> input gate -> 64 B beats to lines -> vhsnunzip
//   -> lines to 64 B beats -> output gate -> reg -> axis_host_send[0]
//
// The host sends one raw Snappy chunk per LOCAL_TRANSFER (tlast = end of
// chunk) and receives exactly the decompressed bytes. Counters on the core's
// ports are read through the CSRs below (cThread::getCSR index):
//
//   0  CTRL        W   bit 0: clear the counters (reads 0)
//   1  THROTTLE    RW  [15:0] input gate mask, [31:16] output gate mask (0 = off)
//   2  INFO        R   [7:0] C_BYTES, [8] DUAL_ISSUE, [9] LONG_CHUNKS,
//                      [10] RAM_BLOCK, [63:32] magic 0x56535A31
//   3  TIMESTAMP   R   free-running cycle counter (not cleared)
//   4  CYCLES      R   active cycles (at least one chunk in flight)
//   5  CHUNKS_IN   R   6  CHUNKS_OUT
//   7  CO_LINES    R   8  CO_BYTES   9  CO_STALL (co_valid && !co_ready)
//   10 DE_LINES    R   11 DE_BYTES
//   12 DE_BLOCKED  R   13 CORE_STALL  14 IN_EMPTY  15 IN_ONLY
//   16 LAT_COUNT   R   17 LAT_SUM     18 LAT_MAX   (isolated chunks)
//   19 IN_FLIGHT   R   chunks currently in the core
//
// See vhsnunzip_harness_perf.sv for the exact counter definitions.

import vhsnunzip_harness_pkg::*;

/* -- Tie-off unused interfaces ------------------------------------------- */
always_comb notify.tie_off_m();
always_comb sq_rd.tie_off_m();
always_comb sq_wr.tie_off_m();
always_comb cq_rd.tie_off_s();
always_comb cq_wr.tie_off_s();

for (genvar I = 1; I < N_STRM_AXI; I++) begin : gen_tie_off
    always_comb axis_host_recv[I].tie_off_s();
    always_comb axis_host_send[I].tie_off_m();
end

localparam int HB = VSZ_BYTES;

/* -- Reset ---------------------------------------------------------------- */
logic hrst_n, core_reset;
always_ff @(posedge aclk) hrst_n <= aresetn;
assign core_reset = !hrst_n;

/* -- Host streams --------------------------------------------------------- */
AXI4SR axis_in  (.aclk(aclk), .aresetn(aresetn));
AXI4SR axis_out (.aclk(aclk), .aresetn(aresetn));

axisr_reg inst_reg_in  (.aclk(aclk), .aresetn(aresetn), .s_axis(axis_host_recv[0]), .m_axis(axis_in));
axisr_reg inst_reg_out (.aclk(aclk), .aresetn(aresetn), .s_axis(axis_out), .m_axis(axis_host_send[0]));

/* -- CSRs ----------------------------------------------------------------- */
localparam int N_REGS = 32;
logic [N_REGS-1:0][63:0] rd_regs;
logic                    clear;
logic [63:0]             throttle;

vhsnunzip_harness_csr #(.N_REGS(N_REGS)) inst_csr (
    .aclk(aclk),
    .aresetn(hrst_n),
    .axi_ctrl(axi_ctrl),
    .rd_regs(rd_regs),
    .clear(clear),
    .throttle(throttle)
);

/* -- Throttling ----------------------------------------------------------- */
logic in_en, out_en;
vhsnunzip_harness_gate #(.SEED(16'h1D0F)) inst_gate_in  (.clk(aclk), .rst_n(hrst_n), .mask(throttle[15:0]),  .enable(in_en));
vhsnunzip_harness_gate #(.SEED(16'hACE1)) inst_gate_out (.clk(aclk), .rst_n(hrst_n), .mask(throttle[31:16]), .enable(out_en));

/* -- Core ----------------------------------------------------------------- */
logic                    co_valid, co_ready, co_last;
logic [HB*8-1:0]         co_data;
logic [$clog2(HB)-1:0]   co_cnt;

logic                    de_valid, de_ready, de_dvalid, de_last;
logic [HB*8-1:0]         de_data;
logic [$clog2(HB):0]     de_cnt;

logic                    in_tready;
assign axis_in.tready = in_tready && in_en;

vhsnunzip_harness_axis_in #(.BYTES(HB)) inst_axis_in (
    .clk(aclk),
    .rst_n(hrst_n),
    .s_tdata(axis_in.tdata),
    .s_tkeep(axis_in.tkeep),
    .s_tlast(axis_in.tlast),
    .s_tvalid(axis_in.tvalid && in_en),
    .s_tready(in_tready),
    .co_valid(co_valid),
    .co_ready(co_ready),
    .co_data(co_data),
    .co_cnt(co_cnt),
    .co_last(co_last)
);

vhsnunzip_harness_core #(
    .DUAL_ISSUE(VSZ_DUAL_ISSUE),
    .LONG_CHUNKS(VSZ_LONG_CHUNKS),
    .RAM_BLOCK(VSZ_RAM_BLOCK)
) inst_vhsnunzip (
    .clk(aclk),
    .reset(core_reset),
    .co_valid(co_valid),
    .co_ready(co_ready),
    .co_data(co_data),
    .co_cnt(co_cnt),
    .co_last(co_last),
    .de_valid(de_valid),
    .de_ready(de_ready),
    .de_dvalid(de_dvalid),
    .de_data(de_data),
    .de_cnt(de_cnt),
    .de_last(de_last)
);

logic out_tvalid;
assign axis_out.tvalid = out_tvalid && out_en;
assign axis_out.tid    = '0;

vhsnunzip_harness_axis_out #(.BYTES(HB)) inst_axis_out (
    .clk(aclk),
    .rst_n(hrst_n),
    .de_valid(de_valid),
    .de_ready(de_ready),
    .de_dvalid(de_dvalid),
    .de_data(de_data),
    .de_cnt(de_cnt),
    .de_last(de_last),
    .m_tdata(axis_out.tdata),
    .m_tkeep(axis_out.tkeep),
    .m_tlast(axis_out.tlast),
    .m_tvalid(out_tvalid),
    .m_tready(axis_out.tready && out_en)
);

/* -- Counters ------------------------------------------------------------- */
localparam int CW = 48;
logic [CW-1:0] timestamp, cycles, chunks_in, chunks_out, co_lines, co_bytes, co_stall;
logic [CW-1:0] de_lines, de_bytes, de_blocked, core_stall, in_empty, in_only;
logic [CW-1:0] lat_count, lat_sum, lat_max;
logic [15:0]   in_flight;

vhsnunzip_harness_perf #(.BYTES(HB), .CW(CW)) inst_perf (
    .clk(aclk),
    .rst_n(hrst_n),
    .clear(clear),
    .co_valid(co_valid),
    .co_ready(co_ready),
    .co_cnt(co_cnt),
    .co_last(co_last),
    .de_valid(de_valid),
    .de_ready(de_ready),
    .de_cnt(de_cnt),
    .de_last(de_last),
    .timestamp(timestamp),
    .cycles(cycles),
    .chunks_in(chunks_in),
    .chunks_out(chunks_out),
    .co_lines(co_lines),
    .co_bytes(co_bytes),
    .co_stall(co_stall),
    .de_lines(de_lines),
    .de_bytes(de_bytes),
    .de_blocked(de_blocked),
    .core_stall(core_stall),
    .in_empty(in_empty),
    .in_only(in_only),
    .lat_count(lat_count),
    .lat_sum(lat_sum),
    .lat_max(lat_max),
    .in_flight(in_flight)
);

always_comb begin
    rd_regs     = '0;
    rd_regs[1]  = throttle;
    rd_regs[2]  = {32'h56535A31, 21'b0, VSZ_RAM_BLOCK != 0, VSZ_LONG_CHUNKS != 0, VSZ_DUAL_ISSUE != 0, 8'(HB)};
    rd_regs[3]  = 64'(timestamp);
    rd_regs[4]  = 64'(cycles);
    rd_regs[5]  = 64'(chunks_in);
    rd_regs[6]  = 64'(chunks_out);
    rd_regs[7]  = 64'(co_lines);
    rd_regs[8]  = 64'(co_bytes);
    rd_regs[9]  = 64'(co_stall);
    rd_regs[10] = 64'(de_lines);
    rd_regs[11] = 64'(de_bytes);
    rd_regs[12] = 64'(de_blocked);
    rd_regs[13] = 64'(core_stall);
    rd_regs[14] = 64'(in_empty);
    rd_regs[15] = 64'(in_only);
    rd_regs[16] = 64'(lat_count);
    rd_regs[17] = 64'(lat_sum);
    rd_regs[18] = 64'(lat_max);
    rd_regs[19] = 64'(in_flight);
end
