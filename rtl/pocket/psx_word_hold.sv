// SPDX-License-Identifier: MIT
// Same-edge held context word, following MiSTer's port-A NEW_DATA mode.
// The caller supplies its original capture enable; no reset clears this word.
// Unlike reset-zero CPU payloads, these contexts need no reset-address mux.
`default_nettype none
module psx_word_hold #(parameter integer width=32)(
 input wire clk,we,
 input wire [width-1:0] d,
 output wire [width-1:0] q
);
 altsyncram #(
  .intended_device_family("Cyclone V"),.operation_mode("SINGLE_PORT"),
  .width_a(width),.widthad_a(1),.numwords_a(2),.width_byteena_a(1),
  .clock_enable_input_a("NORMAL"),.clock_enable_output_a("BYPASS"),
  .outdata_reg_a("UNREGISTERED"),.read_during_write_mode_port_a("NEW_DATA_NO_NBE_READ"),
  .power_up_uninitialized("FALSE"),.ram_block_type("M10K")
 ) mem(.clock0(clk),.clocken0(1'b1),.address_a(1'b0),.data_a(d),.wren_a(we),.q_a(q));
endmodule
`default_nettype wire
