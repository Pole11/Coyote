`timescale 1ns / 1ps

import lynxTypes::*;

/**
 * @brief   WriteIMM completions read by software through the vFPGA CSRs (getCSR)
 *
 * The completions (ImmDts whose data is in memory) wait in a queue. A read of register 0 returns the oldest one and
 * removes it: bit 63 = valid, bits 47:32 = QPN ({vfid, pid}), bits 31:0 = ImmDt. With the queue empty it returns 0.
 * Writes are accepted and ignored.
 */
module rdma_imm_csr #(
    parameter integer QDEPTH = 64
) (
    input  logic                        aclk,
    input  logic                        aresetn,

    metaIntf.s                          s_imm,      // released ImmDts (rdma_imm_order)
    AXI4L.s                             axi_ctrl
);

localparam integer ADDR_LSB = $clog2(AXIL_DATA_BITS/8);
localparam integer IMM_REG = 0;

metaIntf #(.STYPE(imm_t)) imm_q (.*);

// it is the queue that when is read pops an element 
queue_meta #(.QDEPTH(QDEPTH)) inst_imm_q (.aclk(aclk), .aresetn(aresetn), .s_meta(s_imm), .m_meta(imm_q));

// read: one at a time, the queue head is taken when register 0 is read
logic rvalid;
logic [AXIL_DATA_BITS-1:0] rdata;
logic rd_imm;

// write in register 0 
assign rd_imm = axi_ctrl.arvalid && !rvalid && (axi_ctrl.araddr[ADDR_LSB+:8] == IMM_REG);
assign imm_q.ready = rd_imm;

always_ff @(posedge aclk) begin
    if (aresetn == 1'b0) begin
        rvalid <= 1'b0;
        rdata  <= 0;
    end
    else begin
        if (axi_ctrl.arvalid && !rvalid) begin
            rvalid <= 1'b1;
            rdata  <= (rd_imm && imm_q.valid) ? {1'b1, 15'b0, {(16-DEST_BITS-PID_BITS){1'b0}}, imm_q.data.vfid, imm_q.data.pid, imm_q.data.imm} : '0;
        end
        else if (rvalid && axi_ctrl.rready) begin
            rvalid <= 1'b0;
        end
    end
end

assign axi_ctrl.arready = !rvalid;
assign axi_ctrl.rvalid  = rvalid;
assign axi_ctrl.rdata   = rdata;
assign axi_ctrl.rresp   = 2'b00;

// write: address and data taken together, then an OKAY response
logic bvalid;

always_ff @(posedge aclk) begin
    if (aresetn == 1'b0)
        bvalid <= 1'b0;
    else if (axi_ctrl.awvalid && axi_ctrl.wvalid && !bvalid)
        bvalid <= 1'b1;
    else if (bvalid && axi_ctrl.bready)
        bvalid <= 1'b0;
end

assign axi_ctrl.awready = axi_ctrl.awvalid && axi_ctrl.wvalid && !bvalid;
assign axi_ctrl.wready  = axi_ctrl.awvalid && axi_ctrl.wvalid && !bvalid;
assign axi_ctrl.bvalid  = bvalid;
assign axi_ctrl.bresp   = 2'b00;

endmodule