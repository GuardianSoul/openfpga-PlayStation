// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Analogizer (RndMnkIII) on the Pocket cartridge port, for prepare
// --analogizer: 15 kHz analog video from the PSX GPU's own timing and the
// SNAC lines of a real PlayStation controller (psx_snac).
//
// Written for this core. The cartridge pin map and the SNAC "configuration B"
// lines for PSX controllers follow the Analogizer's published adapter
// (RndMnkIII/Analogizer, MIT, openFPGA_Pocket_Analogizer.v and
// openFPGA_Pocket_Analogizer_SNAC.sv; docs/analogizer-plan.md):
//   bank3 = {R[5:0], HS, VS}       bank2 = {B[0], BLANK_n, G[5:0]}
//   bank1 = {ATT2_n, ATT1_n, DAC clock, B[5:1]}
//   bank0[7] DAT in, bank0[6] ACK in, bank0[5] IRQ in (SNAC IN7)
//   pin30 = CLK out, pin31 = CMD out
// Video modes (mode): 0 RGBS (csync on HS), 1 RGsB (csync on the DAC sync
// pin, sync-on-green switch on), 2 YPbPr (BT.601, sync on Y). Off (enable
// low): every cartridge pin is an input, bank0 held high as at reset.
`default_nettype none
module psx_analogizer (
    input  wire        clk,          // video clock (clkvid)
    input  wire        clk_dac,      // clock for the DAC pin (clkvid, 90 degrees)
    input  wire        enable,       // video clock domain level
    input  wire [1:0]  mode,
    input  wire        ce,           // pixel enable (unused: the inputs are registers)
    input  wire [7:0]  r, g, b,
    input  wire        hs, vs, hblank, vblank,
    // SNAC (clk1x side lives in psx_snac)
    input  wire        snac_enable,
    input  wire        att1_n, att2_n, snac_cmd, snac_clk,
    output wire        snac_dat, snac_ack, snac_irq,
    // cartridge port
    inout  wire [7:0]  cart_tran_bank2,
    output wire        cart_tran_bank2_dir,
    inout  wire [7:0]  cart_tran_bank3,
    output wire        cart_tran_bank3_dir,
    inout  wire [7:0]  cart_tran_bank1,
    output wire        cart_tran_bank1_dir,
    inout  wire [7:4]  cart_tran_bank0,
    output wire        cart_tran_bank0_dir,
    inout  wire        cart_tran_pin30,
    output wire        cart_tran_pin30_dir,
    output wire        cart_pin30_pwroff_reset,
    inout  wire        cart_tran_pin31,
    output wire        cart_tran_pin31_dir
);
    // Stage 1: the GPU's pixel, blank and sync outputs are registers on this
    // clock already (gpu_videoout_async), so they are used as they come;
    // composite sync is HS xor VS (active low), so HS pulses invert during VS.
    wire [7:0] r1=r, g1=g, b1=b;
    wire blank1=hblank|vblank, hs1=hs, vs1=vs;
    wire csync_n1=~(hs1^vs1);

    // Stages 2a/2b: BT.601 full range in 1/256 steps, in the difference form, so it
    // takes 3 DSP blocks (9 products took 6 and pushed MDEC multipliers into
    // logic): Y = (77R + 150G + 29B) / 256, then the
    // colour differences Pb = 144 (B - Y) / 256 + 128 and
    // Pr = 183 (R - Y) / 256 + 128 (0.564 and 0.713 of B - Y and R - Y).
    // Black (blank) is Y 0, Pb/Pr 128.
    wire [7:0] rk=blank1 ? 8'd0 : r1, gk=blank1 ? 8'd0 : g1, bk=blank1 ? 8'd0 : b1;
    wire [16:0] ysum=17'd77*rk+17'd150*gk+17'd29*bk;
    reg [7:0] ya=0, ra=0, ga=0, ba=0;
    reg blanka=1, csync_na=1;
    always @(posedge clk) begin
        ya<=ysum[15:8]; ra<=rk; ga<=gk; ba<=bk; blanka<=blank1; csync_na<=csync_n1;
    end
    wire signed [8:0] bdy=$signed({1'b0,ba})-$signed({1'b0,ya}), rdy=$signed({1'b0,ra})-$signed({1'b0,ya});
    wire signed [17:0] pbsum=18'sd32768+18'sd144*bdy;
    wire signed [17:0] prsum=18'sd32768+18'sd183*rdy;
    reg [7:0] y2=0, pb2=8'd128, pr2=8'd128, r2=0, g2=0, b2=0;
    reg blank2=1, csync_n2=1;
    always @(posedge clk) begin
        y2<=ya;
        pb2<=pbsum[17] ? 8'd0 : pbsum[16] ? 8'd255 : pbsum[15:8];
        pr2<=prsum[17] ? 8'd0 : prsum[16] ? 8'd255 : prsum[15:8];
        r2<=ra; g2<=ga; b2<=ba;
        blank2<=blanka; csync_n2<=csync_na;
    end

    // Stage 3: the mode's pins (6-bit DAC)
    reg [5:0] ro=0, go=0, bo=0;
    reg hso=1, vso=1, blank_no=0;
    always @(posedge clk) begin
        case (mode)
            2'd1: begin ro<=r2[7:2]; go<=g2[7:2]; bo<=b2[7:2]; hso<=1'b1; vso<=csync_n2; blank_no<=~blank2; end     // RGsB
            2'd2: begin ro<=pr2[7:2]; go<=y2[7:2]; bo<=pb2[7:2]; hso<=1'b1; vso<=csync_n2; blank_no<=1'b1; end   // YPbPr
            default: begin ro<=r2[7:2]; go<=g2[7:2]; bo<=b2[7:2]; hso<=csync_n2; vso<=1'b1; blank_no<=~blank2; end // RGBS
        endcase
    end

    // Cartridge pins
    assign cart_tran_bank3=enable ? {ro,hso,vso} : 8'hzz;
    assign cart_tran_bank3_dir=enable;
    assign cart_tran_bank2=enable ? {bo[0],blank_no,go} : 8'hzz;
    assign cart_tran_bank2_dir=enable;
    wire [1:0] att=snac_enable ? {att2_n,att1_n} : 2'b11;
    assign cart_tran_bank1=enable ? {att,clk_dac,bo[5:1]} : 8'hzz;
    assign cart_tran_bank1_dir=enable;
    assign cart_tran_bank0=enable ? 4'hz : 4'hf;          // SNAC inputs while enabled
    assign cart_tran_bank0_dir=~enable;
    assign cart_tran_pin30=enable && snac_enable ? snac_clk : 1'bz;
    assign cart_tran_pin30_dir=enable && snac_enable;
    assign cart_pin30_pwroff_reset=enable;               // GPIO use while enabled
    assign cart_tran_pin31=enable && snac_enable ? snac_cmd : 1'bz;
    assign cart_tran_pin31_dir=enable && snac_enable;
    assign snac_dat=cart_tran_bank0[7];
    assign snac_ack=cart_tran_bank0[6];
    assign snac_irq=cart_tran_bank0[5];
endmodule
`default_nettype wire
