// SPDX-License-Identifier: MIT
// Optional BIOS logo skip (Pocket menu "Skip BIOS logo"), between the BIOS
// loader's memory port and the memory. The kernel copies the shell from ROM
// 0xBFC18000 to 0x80030000 and calls it; the shell shows the logo, then
// returns to boot the disc. With skip on, five words at ROM offset 0x18000
// make the shell enable the display (GP1(03h)=0) and return at once:
//   lui at,0x1F80 / lui t2,0x0300 / sw t2,0x1814(at) / jr ra / nop
// The menu value is written by the Pocket just before Reset Exit, so the
// patch waits for host_run after the loader is ready; bios_ready (which
// releases the CPU through psx_boot_gate) follows only once the words are
// written, so the CPU is still in reset meanwhile. Outside the patch the
// loader's port passes straight through. A reload (loader ready falling)
// finishes an in-flight patch packet, then waits for the next ready.
`default_nettype none
// SETTLE: the skip setting is read SETTLE clocks after Reset Exit, not at
// it. On NativeR 0.4.1 the logos still showed with the setting on, so the
// Pocket may write persisted settings just after Reset Exit; 2^22 clocks of
// clk_74a (~56 ms) is a negligible boot delay.
module psx_bios_patch #(parameter [16:0] SHELL_WORD=17'h06000, parameter integer SETTLE=1<<22) (
    input wire clk, reset,
    input wire loader_ready, host_run, skip,
    output reg bios_ready=0,         // registered: it feeds psx_boot_gate's asynchronous reset
    output reg patched=0,
    // loader side
    input wire l_valid, l_write,
    output wire l_ready,
    input wire [24:0] l_addr,
    input wire [3:0] l_halfwords,
    input wire [127:0] l_data,
    input wire [15:0] l_be,
    output wire l_rsp, l_error,
    input wire l_rsp_ready,
    // memory side
    output wire m_valid, m_write,
    input wire m_ready,
    output wire [24:0] m_addr,
    output wire [3:0] m_halfwords,
    output wire [127:0] m_data,
    output wire [15:0] m_be,
    input wire m_rsp, m_error,
    output wire m_rsp_ready
);
    localparam [31:0] W0=32'h3C011F80, W1=32'h3C0A0300, W2=32'hAC2A1814, W3=32'h03E00008, W4=32'h00000000;
    localparam [2:0] S_IDLE=0, S_ISSUE=1, S_WAIT=2, S_DONE=3, S_SETTLE=4;
    reg [22:0] settle=0;
    reg [2:0] state=S_IDLE;
    reg part=0;                      // packet 0: words 0-3, packet 1: word 4
    wire own=state==S_ISSUE || state==S_WAIT;
    always @(posedge clk) bios_ready<=!reset && loader_ready && state==S_DONE;
    assign m_valid=own ? state==S_ISSUE : l_valid;
    assign m_write=own ? 1'b1 : l_write;
    assign m_addr=own ? {7'b0, SHELL_WORD+(part ? 17'd4 : 17'd0), 1'b0} : l_addr;
    assign m_halfwords=own ? (part ? 4'd2 : 4'd8) : l_halfwords;
    assign m_data=own ? (part ? {96'b0, W4} : {W3, W2, W1, W0}) : l_data;
    assign m_be=own ? (part ? 16'h000f : 16'hffff) : l_be;
    assign m_rsp_ready=own ? 1'b1 : l_rsp_ready;
    assign l_ready=!own && m_ready;
    assign l_rsp=!own && m_rsp;
    assign l_error=m_error;
    always @(posedge clk) begin
        if (reset) begin state<=S_IDLE; part<=0; patched<=0; end
        else case (state)
            S_IDLE: if (loader_ready && host_run) begin
                    part<=0; patched<=0; settle<=0; state<=S_SETTLE;
                end
            S_SETTLE: if (!loader_ready || !host_run) state<=S_IDLE;
                else if (settle>=SETTLE-1) state<=skip ? S_ISSUE : S_DONE;
                else settle<=settle+1'b1;
            S_ISSUE: if (m_ready) state<=S_WAIT;
            S_WAIT: if (m_rsp) begin
                    if (!part) begin part<=1; state<=S_ISSUE; end
                    else begin patched<=!m_error; state<=loader_ready ? S_DONE : S_IDLE; end
                end
            S_DONE: if (!loader_ready) state<=S_IDLE;
            default: state<=S_IDLE;
        endcase
    end
endmodule
`default_nettype wire
