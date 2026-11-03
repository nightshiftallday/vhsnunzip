`timescale 1ns / 1ps

import lynxTypes::*;

// AXI4-Lite CSR slave of the harness (64-bit registers, cThread::setCSR/getCSR
// index = register index). Writable: 0 CTRL (bit 0 clears the counters, reads
// 0) and 1 THROTTLE. All registers read from rd_regs, so rd_regs[1] should
// return the throttle value. The register map is listed in vfpga_top.svh.
module vhsnunzip_harness_csr #(
    parameter int N_REGS = 32
) (
    input  logic                      aclk,
    input  logic                      aresetn,

    AXI4L.s                           axi_ctrl,

    input  logic [N_REGS-1:0][63:0]   rd_regs,
    output logic                      clear,
    output logic [63:0]               throttle
);

localparam int ADDR_LSB = $clog2(AXIL_DATA_BITS / 8);
localparam int ADDR_W   = $clog2(N_REGS);

localparam int REG_CTRL     = 0;
localparam int REG_THROTTLE = 1;

logic awready, wready, bvalid, arready, rvalid;
logic [63:0] rdata;

assign axi_ctrl.awready = awready;
assign axi_ctrl.wready  = wready;
assign axi_ctrl.bvalid  = bvalid;
assign axi_ctrl.bresp   = 2'b00;
assign axi_ctrl.arready = arready;
assign axi_ctrl.rvalid  = rvalid;
assign axi_ctrl.rdata   = rdata;
assign axi_ctrl.rresp   = 2'b00;

logic [ADDR_W-1:0] waddr, raddr;
assign waddr = axi_ctrl.awaddr[ADDR_LSB +: ADDR_W];
assign raddr = axi_ctrl.araddr[ADDR_LSB +: ADDR_W];

// Writes: AW and W are accepted together; the response follows one cycle after
// the handshake. Reads: AR handshake, then R with the registered value.
always_ff @(posedge aclk) begin
    if (!aresetn) begin
        awready  <= 1'b0;
        wready   <= 1'b0;
        bvalid   <= 1'b0;
        arready  <= 1'b0;
        rvalid   <= 1'b0;
        rdata    <= '0;
        clear    <= 1'b0;
        throttle <= '0;
    end else begin
        awready <= 1'b0;
        wready  <= 1'b0;
        clear   <= 1'b0;
        if (!bvalid && !awready && axi_ctrl.awvalid && axi_ctrl.wvalid) begin
            awready <= 1'b1;
            wready  <= 1'b1;
        end
        if (awready) begin
            bvalid <= 1'b1;
            if (waddr == ADDR_W'(REG_CTRL) && axi_ctrl.wstrb[0]) begin
                clear <= axi_ctrl.wdata[0];
            end
            if (waddr == ADDR_W'(REG_THROTTLE)) begin
                for (int i = 0; i < 8; i++) begin
                    if (axi_ctrl.wstrb[i]) throttle[i*8 +: 8] <= axi_ctrl.wdata[i*8 +: 8];
                end
            end
        end else if (bvalid && axi_ctrl.bready) begin
            bvalid <= 1'b0;
        end

        arready <= 1'b0;
        if (!rvalid && !arready && axi_ctrl.arvalid) begin
            arready <= 1'b1;
        end
        if (arready) begin
            rvalid <= 1'b1;
            rdata  <= rd_regs[raddr];
        end else if (rvalid && axi_ctrl.rready) begin
            rvalid <= 1'b0;
        end
    end
end

endmodule
