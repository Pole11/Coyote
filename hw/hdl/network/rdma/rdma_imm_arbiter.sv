`timescale 1ns / 1ps

import lynxTypes::*;

/**
 * @brief   WriteIMM completions to the vFPGA that owns the QP
 *
 * Same routing as rdma_meta_rx_arbiter for the ACKs: an ImmDt goes to vFPGA vfid.
 * An ImmDt for a vfid without a vFPGA is dropped, so that it cannot block the others.
 */
module rdma_imm_arbiter (
    metaIntf.s                          s_meta,
    metaIntf.m                          m_meta [N_REGIONS]
);

logic [N_REGIONS-1:0] ready;

for(genvar i = 0; i < N_REGIONS; i++) begin
    assign m_meta[i].valid = s_meta.valid && (s_meta.data.vfid == i);
    assign m_meta[i].data  = s_meta.data;
    assign ready[i]        = m_meta[i].ready;
end

assign s_meta.ready = (s_meta.data.vfid < N_REGIONS) ? ready[s_meta.data.vfid] : 1'b1;

endmodule