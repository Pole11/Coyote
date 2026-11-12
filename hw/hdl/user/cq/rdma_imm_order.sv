`timescale 1ns / 1ps

import lynxTypes::*;

/**
 * @brief   Releases each WriteIMM ImmDt only after its data is in memory (MRC 6.3.2, Table 6-13)
 *
 * The stack writes the packet that completes WriteIMMs with last = 1 (one rq_wr request) and, in the same packet order,
 * sends their ImmDts on rdma_imm, with last = 1 on the final one of the group. The vFPGA forwards rq_wr to sq_wr, so
 * that request gets a write completion on cq_wr (LOCAL_WRITE, host = 0), in issue order. The n-th completion therefore
 * belongs to the n-th group of ImmDts: cnt counts the completions whose group has not been released yet.
 */
module rdma_imm_order #(
    parameter integer QDEPTH = 32
) (
    input  logic                        aclk,
    input  logic                        aresetn,

    metaIntf.s                          s_imm,      // rdma_imm: ImmDts from the stack, in RQMSN order
    metaIntf.s                          s_cq_wr,    // cq_wr: write completions of this vFPGA, always taken
    metaIntf.m                          m_imm       // ImmDts whose data is in memory
);

metaIntf #(.STYPE(imm_t)) imm_q (.*);

queue_meta #(.QDEPTH(QDEPTH)) inst_imm_q (.aclk(aclk), .aresetn(aresetn), .s_meta(s_imm), .m_meta(imm_q));

// write completions of the forwarded rq_wr requests: RDMA ACKs and host-issued transfers are not counted
logic cpl;
assign s_cq_wr.ready = 1'b1;
assign cpl = s_cq_wr.valid && (s_cq_wr.data.opcode == LOCAL_WRITE) && !s_cq_wr.data.host;

logic [15:0] cnt;
logic group_out;
assign group_out = m_imm.valid && m_imm.ready && imm_q.data.last;

always_ff @(posedge aclk) begin
    if (aresetn == 1'b0)
        cnt <= 0;
    else
        cnt <= cnt + cpl - group_out;
end

assign m_imm.valid = imm_q.valid && (cnt != 0);
assign m_imm.data  = imm_q.data;
assign imm_q.ready = m_imm.ready && (cnt != 0);

endmodule