// SPDX-License-Identifier: GPL-2.0-only
// Dumps every word of psx_memcard_blank for the Python format check.
module tb_memcard_blank;
  reg [13:0] index; wire [63:0] word;
  integer f, i;
  psx_memcard_blank dut(.word_index(index), .word(word));
  initial begin
    f = $fopen("blank_words.txt", "w");
    for (i = 0; i < 16384; i = i + 1) begin index = i; #1 $fwrite(f, "%016h\n", word); end
    $fclose(f); $finish;
  end
endmodule
