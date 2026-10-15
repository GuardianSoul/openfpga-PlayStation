// SPDX-License-Identifier: GPL-2.0-only
// Formatted blank PlayStation memory card, one 64-bit word at a time.
//
// When a card's save file is missing, the core fills that card's 128 KiB
// window from this table so the guest sees a freshly formatted card, as on a
// new card from the factory (psx-spx "Memory Card Data Format"): frame 0 and
// the write-test frame 63 hold "MC"; frames 1-15 are free directory entries;
// frames 16-35 are an empty broken-sector list; frames 36-62 are zero; the
// fifteen data blocks are 0xFF. Byte 127 of each header frame is the XOR of
// bytes 0-126. Words are little-endian: byte i of the card is bits
// 8*(i%8)+7 .. 8*(i%8) of word i/8, matching joypad_mem's window layout.
module psx_memcard_blank (
    input  wire [13:0] word_index,   // 16,384 words of 8 bytes
    output reg  [63:0] word
);
    wire [9:0] frame = word_index[13:4];   // 128-byte frames
    wire [3:0] slot  = word_index[3:0];    // word within the frame
    always @(*) begin
        word = 64'hFFFF_FFFF_FFFF_FFFF;    // data blocks 1-15
        if (frame < 10'd64) begin
            word = 64'd0;
            if (frame == 10'd0 || frame == 10'd63) begin
                if (slot == 4'd0)  word = 64'h0000_0000_0000_434D;   // 'M','C'
                if (slot == 4'd15) word = 64'h0E00_0000_0000_0000;   // checksum 0x4D^0x43
            end else if (frame < 10'd16) begin
                if (slot == 4'd0)  word = 64'h0000_0000_0000_00A0;   // free block
                if (slot == 4'd1)  word = 64'h0000_0000_0000_FFFF;   // no next block
                if (slot == 4'd15) word = 64'hA000_0000_0000_0000;   // checksum
            end else if (frame < 10'd36) begin
                if (slot == 4'd0)  word = 64'h0000_0000_FFFF_FFFF;   // no broken sector
                if (slot == 4'd1)  word = 64'h0000_0000_0000_FFFF;
                // checksum of FF x6 is 0
            end
        end
    end
endmodule
