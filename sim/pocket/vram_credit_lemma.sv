// SPDX-License-Identifier: MIT
// Combinational lemma, not a formal proof of the entire CDC frontend.
// The RTL's existing invariant is pending <= M. An offered write adds at
// most 510 words. With power-of-two M >= 512, pending+addend < 2*M, so
// reassociating modulo 2*M loses no high bit. Clock/byte/oracle tests check
// the actual transformed frontend, including resets and pointer wraps.
module vram_credit_lemma #(parameter M=1024, B=$clog2(2*M)) (
    input [B-1:0] posted, finished,
    input [7:0] count,
    input offer,
    output bad
);
    wire [B-1:0] pending=posted-finished;
    wire [8:0] addend=offer ? {count,1'b0} : 9'b0;
    wire [B:0] original={1'b0,pending}+addend;
    wire [B-1:0] projected=posted+addend;
    wire [B-1:0] delta=projected-finished;
    assign bad=(pending<=M) && (original!={1'b0,delta});
endmodule
