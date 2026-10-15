// SPDX-License-Identifier: GPL-2.0-only
// MiSTer register/clock interface for the pinned Laxer GTE CE adaptation.
// Experimental: command coverage and CPU execution remain to be established.
`include "GTEDefine.hv"
module psx_laxer_mister_adapter(
    input wire clk2x, input wire clk2xIndex, input wire ce, input wire reset,
    output reg gte_busy,
    input wire [5:0] gte_readAddr, output reg [31:0] gte_readData,
    input wire gte_readEna,
    input wire [5:0] gte_writeAddr_in, input wire [31:0] gte_writeData_in,
    input wire gte_writeEna_in,
    input wire [31:0] gte_cmdData, input wire gte_cmdEna
);
    wire [31:0] asynchronous_read;
    wire engine_busy;
    // A real clock enable freezes every pipeline stage, including ROM outputs.
    // Keep the 2x clock on normal clock routing; no combinational clock gate.
    wire engine_enable = reset || (ce && clk2xIndex);
    PocketGTEEngine engine(
        .i_clk(clk2x), .i_ce(engine_enable), .i_nRst(!reset),
        .i_regID(E_REG'(gte_writeAddr_in)), .i_readRegID(E_REG'(gte_readAddr)),
        .i_WritReg(gte_writeEna_in), .i_dataIn(gte_writeData_in),
        .o_dataOut(asynchronous_read), .i_DIP_USEFASTGTE(1'b0), .i_DIP_FIXWIDE(1'b0),
        .i_Instruction(gte_cmdData[24:0]), .i_run(gte_cmdEna), .o_executing(engine_busy)
    );
    // MiSTer samples reads on both 2x phases. Nonblocking assignments preserve
    // read-before-write on the phase that also accepts a register write.
    always @(posedge clk2x)
        if (!reset && ce && gte_readEna) gte_readData <= asynchronous_read;
    // The CPU's stalled MFC2 path consumes the previously registered read at
    // its 1x edge. Release busy on index=0, as the MiSTer IDLE state does, so
    // the next index=1 edge captures the read before that CPU sampling edge.
    always @(posedge clk2x)
        if (reset) gte_busy <= 1'b0;
        else if (ce) begin
            if (engine_busy || (clk2xIndex && gte_cmdEna)) gte_busy <= 1'b1;
            else if (!clk2xIndex) gte_busy <= 1'b0;
        end
endmodule
