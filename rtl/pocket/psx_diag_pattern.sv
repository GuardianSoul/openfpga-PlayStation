// SPDX-License-Identifier: MIT
// On-screen memory probe diagnostics for photographs, without APF debug reads.
// Top band: four status segments. Below: eight 32-bit words, MSB left, one
// cell per bit (white=1, gray=0), with a gap between nibbles.
module psx_diag_pattern (
    input wire clk, reset,
    input wire [3:0] status_failed, status_passed,
    input wire [255:0] words,
    output wire [23:0] rgb,
    output wire de, hs, vs
);
    reg [9:0] x=0, y=0;
    always @(posedge clk) begin
        if (reset) begin x<=0; y<=0; end
        else if (x==799) begin x<=0; y<=y==524 ? 0 : y+1'b1; end
        else x<=x+1'b1;
    end
    // Inputs are final results held before their flags; sampled for display only.
    (* async_reg="true" *) reg [7:0] flags_meta=0, flags_sync=0;
    (* async_reg="true" *) reg [255:0] words_meta=0;
    reg [255:0] words_sync=0;
    always @(posedge clk) begin
        flags_meta<={status_failed,status_passed}; flags_sync<=flags_meta;
        words_meta<=words; words_sync<=words_meta;
    end
    // 640x480, 800x525 totals, as in psx_test_pattern.
    assign de=x<640 && y<480;
    assign hs=x>=656 && x<752;
    assign vs=y>=490 && y<492;
    // Bit cells: 20 pixels wide, column 0 is bit 31.
    reg [4:0] sub=0, column=0;
    always @(posedge clk) begin
        if (x==799) begin sub<=0; column<=0; end
        else if (sub==19) begin sub<=0; column<=column+1'b1; end
        else sub<=sub+1'b1;
    end
    // Rows of 36 lines starting at line 80; 28 lines drawn per row.
    reg [2:0] row=0; reg [5:0] line=0; reg in_rows=0;
    always @(posedge clk) if (x==799) begin
        if (y==79) begin in_rows<=1; row<=0; line<=0; end
        else if (in_rows && line==35) begin line<=0; row<=row+1'b1; if (row==7) in_rows<=0; end
        else line<=line+1'b1;
    end
    wire [31:0] word=words_sync[row*32 +: 32];
    wire bit_value=word[5'd31-column];
    wire [1:0] segment=x>=480 ? 2'd3 : x>=320 ? 2'd2 : x>=160 ? 2'd1 : 2'd0;
    wire seg_failed=flags_sync[4+segment], seg_passed=flags_sync[segment];
    wire [23:0] status=seg_failed ? 24'hff2020 : seg_passed ? 24'h20ff40 : 24'hffff20;
    reg [7:0] animation=0;
    always @(posedge clk) if (x==0 && y==0) animation<=animation+1'b1;
    wire divider=(x==159 || x==160 || x==319 || x==320 || x==479 || x==480);
    wire bit_cell=in_rows && line<28 && sub>=2 && !(column[1:0]==0 && sub<6);
    assign rgb=!de ? 24'b0 : y<64 ? (divider ? 24'h0 : status) :
        y>=448 ? {animation,animation,animation} :
        bit_cell ? (bit_value ? 24'hffffff : 24'h383838) : 24'h0;
endmodule
