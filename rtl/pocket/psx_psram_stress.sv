// SPDX-License-Identifier: MIT
// PSRAM stress for the Pocket probe: console-like packet traffic through
// psx_psram_pair (or its PAIRED=0 single-chip mode) against a shadow copy.
// Phase 1 fills 4,096 words on each die (consecutive word writes, die
// alternating). Phase 2 runs pseudo-random operations over both windows:
// masked word writes, write-then-immediate-read of the same word, aligned
// 4-word line reads (CPU cache fills), single-word reads, single halfword
// writes (SPU) and 2-word reads that start on any word. Every read word is
// compared; the first mismatch is kept (operation, halfword address,
// expected, actual) and later ones are counted.
// READS_ONLY: the fill writes an address pattern (high half {101, die, word},
// low half {010, die, word}) and phase 2 only reads, so a wrong word names
// the address and chip half it came from. MIX picks which writes join the reads.
// After the first mismatch the failing word is read once more (operation 6):
// fail_reread equal to fail_actual means the word is stored wrong (a write
// problem); equal to fail_expected means the first read was wrong.
`default_nettype none
module psx_psram_stress #(parameter [31:0] OPS=32'd2000000, parameter integer READS_ONLY=0, parameter integer MIX=0) (
    input wire clk, reset, initialized,
    output reg req_valid=0, req_write=0,
    input wire req_ready,
    output reg [24:0] req_addr=0,
    output reg [3:0] req_halfwords=0,
    output reg [127:0] req_wdata=0,
    output reg [15:0] req_byte_en=0,
    input wire rsp_valid,
    output wire rsp_ready,
    input wire [127:0] rsp_rdata,
    input wire rsp_error,
    output reg done=0,
    output reg [31:0] ops=0, fails=0,
    output reg [3:0] fail_op=0,
    output reg [24:0] fail_addr=0,
    output reg [31:0] fail_expected=0, fail_actual=0, fail_reread=0
);
    assign rsp_ready=1'b1;
    (* ramstyle="M10K" *) reg [31:0] shadow[0:8191];   // {die, word[11:0]}
    reg [31:0] lfsr=32'hace1_2468;
    wire [31:0] lfsr_next={lfsr[30:0], lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
    localparam [3:0] FILL=0, PICK=1, ISSUE=2, WAIT=3, CHECK=4, CHECK2=5, SHADOW=6, FINISH=7, DONE=8;
    reg [3:0] state=FILL;
    reg [12:0] fill=0;
    reg [2:0] op=0;
    reg [12:0] slot=0;          // {die, word}
    reg [2:0] words=0, index=0;
    reg [31:0] wdata=0;
    reg [3:0] mask=0;
    reg chained=0;              // op 1: read follows the write
    reg [127:0] got=0;
    reg [31:0] expect_q=0;
    reg [12:0] read_slot=0;
    reg [31:0] write_q=0; reg [3:0] mask_q=0; reg [12:0] write_slot=0;
    reg reread=0; reg [12:0] reread_slot=0;
    wire mismatch=state==CHECK && index!=0 && got[(index-1)*32+:32]!==expect_q;
    wire [31:0] fill_value=READS_ONLY!=0 ? {3'b101, fill[0], fill[12:1], 3'b010, fill[0], fill[12:1]} : lfsr;
    // MIX (with READS_ONLY's address-pattern fill): 2 reads + full-mask word
    // writes, 3 reads + masked word writes, 4 reads + halfword writes,
    // 5 reads + write-then-immediate-read (full mask).
    wire [2:0] read_op=lfsr[28:27]==2'd1 ? 3'd3 : lfsr[28:27]==2'd2 ? 3'd5 : 3'd2;
    wire [2:0] pick_op=MIX==2 || MIX==3 ? (lfsr[30:29]==2'd0 ? 3'd0 : read_op) :
                       MIX==4 ? (lfsr[30:29]==2'd0 ? 3'd4 : read_op) :
                       MIX==5 ? (lfsr[30:29]==2'd0 ? 3'd1 : read_op) :
                       READS_ONLY!=0 ? read_op
                                     : (lfsr[31:29]==3'd7 ? 3'd0 : lfsr[31:29]==3'd6 ? 3'd2 : lfsr[31:29]);
    wire [3:0] word_mask=MIX==2 || MIX==5 ? 4'hf : lfsr[15:12]|4'b0001;
    // halfword address of word w on die d: die 0 -> main RAM, die 1 -> 0x800000
    function automatic [24:0] hw(input [12:0] s);
        hw={1'b0, s[12], 10'b0, s[11:0], 1'b0};
    endfunction
    always @(posedge clk) begin
        expect_q<=shadow[read_slot];
        if (reset || !initialized) begin
            state<=FILL; fill<=0; req_valid<=0; done<=0; ops<=0; fails<=0;
        end else case (state)
            FILL: if (!req_valid) begin
                // word fill, die alternating: slot = {fill[0], fill[12:1]}
                slot<={fill[0], fill[12:1]}; wdata<=fill_value; mask<=4'hf; lfsr<=lfsr_next;
                req_write<=1; req_addr<=hw({fill[0], fill[12:1]}); req_halfwords<=2;
                req_wdata<={96'b0, fill_value}; req_byte_en<=16'h000f; req_valid<=1;
                write_slot<={fill[0], fill[12:1]}; write_q<=fill_value; mask_q<=4'hf;
                op<=0; state<=ISSUE;
            end
            PICK: begin
                lfsr<=lfsr_next; ops<=ops+1'b1;
                slot<={lfsr[20], lfsr[11:0]};
                op<=pick_op;
                chained<=0;
                case (pick_op)
                    3'd0, 3'd1: begin   // masked word write (1: then read it back at once)
                        req_write<=1; req_addr<=hw({lfsr[20], lfsr[11:0]}); req_halfwords<=2;
                        req_wdata<={96'b0, lfsr_next}; req_byte_en<={12'b0, word_mask};
                        write_slot<={lfsr[20], lfsr[11:0]}; write_q<=lfsr_next; mask_q<=word_mask;
                        chained<=pick_op==3'd1;
                    end
                    3'd2: begin         // aligned 4-word line read
                        req_write<=0; req_addr<=hw({lfsr[20], lfsr[11:2], 2'b00}); req_halfwords<=8;
                        read_slot<={lfsr[20], lfsr[11:2], 2'b00}; words<=4;
                    end
                    3'd3: begin         // single-word read
                        req_write<=0; req_addr<=hw({lfsr[20], lfsr[11:0]}); req_halfwords<=2;
                        read_slot<={lfsr[20], lfsr[11:0]}; words<=1;
                    end
                    3'd4: begin         // single halfword write (low or high half)
                        req_write<=1; req_addr<=hw({lfsr[20], lfsr[11:0]})+{24'b0, lfsr[13]}; req_halfwords<=1;
                        req_wdata<={112'b0, lfsr_next[15:0]}; req_byte_en<=16'h0003;
                        write_slot<={lfsr[20], lfsr[11:0]};
                        write_q<=lfsr[13] ? {lfsr_next[15:0], 16'b0} : {16'b0, lfsr_next[15:0]};
                        mask_q<=lfsr[13] ? 4'b1100 : 4'b0011;
                    end
                    default: begin      // 2-word read from any word
                        req_write<=0; req_addr<=hw({lfsr[20], lfsr[11:1], 1'b0}); req_halfwords<=4;
                        read_slot<={lfsr[20], lfsr[11:1], 1'b0}; words<=2;
                    end
                endcase
                req_valid<=1; index<=0; state<=ISSUE;
            end
            ISSUE: if (req_ready) begin req_valid<=0; state<=WAIT; end
            WAIT: if (rsp_valid) begin
                got<=rsp_rdata;
                if (op==3'd6) fail_reread<=rsp_rdata[31:0];
                if (rsp_error) begin fails<=fails+1'b1; if (fails==0) begin fail_op<=4'hf; fail_addr<=req_addr; end end
                state<=req_write ? SHADOW : CHECK;
            end
            SHADOW: begin
                // merge the write into the shadow (read-modify-write over two cycles)
                read_slot<=write_slot; state<=CHECK2;
            end
            CHECK2: begin
                state<=FINISH;
            end
            FINISH: begin
                shadow[write_slot]<={mask_q[3] ? write_q[31:24] : expect_q[31:24], mask_q[2] ? write_q[23:16] : expect_q[23:16],
                                     mask_q[1] ? write_q[15:8] : expect_q[15:8], mask_q[0] ? write_q[7:0] : expect_q[7:0]};
                if (op==3'd0 && fill!=13'h1fff && !done && ops==0) begin
                    fill<=fill+1'b1; state<=FILL;
                end else if (chained) begin
                    chained<=0; op<=3'd5; req_write<=0; req_addr<=hw(write_slot); req_halfwords<=2;
                    read_slot<=write_slot; words<=1; index<=0; req_valid<=1; state<=ISSUE;
                end else begin
                    state<=ops==OPS ? DONE : PICK;
                end
                if (op==3'd0 && fill==13'h1fff && ops==0) begin fill<=0; state<=PICK; end
            end
            CHECK: begin
                // one word per two cycles: shadow read address set, then compare
                if (mismatch) begin
                    fails<=fails+1'b1;
                    if (fails==0) begin
                        reread<=1; reread_slot<=read_slot-13'd1;
                        fail_op<={1'b0, op}; fail_addr<=hw(read_slot-13'd1);
                        fail_expected<=expect_q; fail_actual<=got[(index-1)*32+:32];
                    end
                end
                if (index==words) begin
                    if (reread || (mismatch && fails==0)) begin
                        reread<=0; op<=3'd6; req_write<=0; req_addr<=hw(reread ? reread_slot : read_slot-13'd1); req_halfwords<=2;
                        read_slot<=reread ? reread_slot : read_slot-13'd1; words<=1; index<=0; req_valid<=1; state<=ISSUE;
                    end else state<=ops==OPS ? DONE : PICK;
                end else begin index<=index+1'b1; read_slot<=read_slot+1'b1; end
            end
            DONE: done<=1;
            default: state<=FILL;
        endcase
    end
endmodule
`default_nettype wire
