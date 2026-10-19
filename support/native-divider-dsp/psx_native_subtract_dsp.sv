// SPDX-License-Identifier: GPL-2.0-only
// Exact native26 modulo subtraction, six lanes, combinational dedicated DSPs.
module psx_native_subtract_dsp(input[155:0]a,b,output[155:0]y);
genvar i;generate for(i=0;i<6;i=i+1)begin:lane
wire[27:0]full_difference;
altmult_add #(
      .width_a(26),.width_b(1),.representation_a("UNSIGNED"),.representation_b("UNSIGNED"),
      .port_signa("PORT_UNUSED"),.port_signb("PORT_UNUSED"),
      .port_addnsub1("PORT_UNUSED"),.port_addnsub3("PORT_UNUSED"),
      .multiplier1_direction("SUB"),.multiplier3_direction("ADD"),
      .dedicated_multiplier_circuitry("YES"),.intended_device_family("Cyclone V"),
      .input_register_a0("UNREGISTERED"),
      .input_register_a1("UNREGISTERED"),
      .input_register_a2("UNREGISTERED"),
      .input_register_a3("UNREGISTERED"),
      .signed_register_a("UNREGISTERED"),
      .signed_pipeline_register_a("UNREGISTERED"),
      .input_register_b0("UNREGISTERED"),
      .input_register_b1("UNREGISTERED"),
      .input_register_b2("UNREGISTERED"),
      .input_register_b3("UNREGISTERED"),
      .signed_register_b("UNREGISTERED"),
      .signed_pipeline_register_b("UNREGISTERED"),
      .input_register_c0("UNREGISTERED"),
      .input_register_c1("UNREGISTERED"),
      .input_register_c2("UNREGISTERED"),
      .input_register_c3("UNREGISTERED"),
      .multiplier_register0("UNREGISTERED"),
      .multiplier_register1("UNREGISTERED"),
      .multiplier_register2("UNREGISTERED"),
      .multiplier_register3("UNREGISTERED"),
      .addnsub_multiplier_register1("UNREGISTERED"),
      .addnsub_multiplier_pipeline_register1("UNREGISTERED"),
      .addnsub_multiplier_register3("UNREGISTERED"),
      .addnsub_multiplier_pipeline_register3("UNREGISTERED"),
      .addnsub1_round_register("UNREGISTERED"),
      .addnsub1_round_pipeline_register("UNREGISTERED"),
      .addnsub3_round_register("UNREGISTERED"),
      .addnsub3_round_pipeline_register("UNREGISTERED"),
      .mult01_round_register("UNREGISTERED"),
      .mult01_saturation_register("UNREGISTERED"),
      .mult23_round_register("UNREGISTERED"),
      .mult23_saturation_register("UNREGISTERED"),
      .loadconst_control_register("UNREGISTERED"),
      .coefsel0_register("UNREGISTERED"),
      .coefsel1_register("UNREGISTERED"),
      .coefsel2_register("UNREGISTERED"),
      .coefsel3_register("UNREGISTERED"),
      .output_register("UNREGISTERED"),.output_aclr("ACLR0"),
      .number_of_multipliers(2),.width_result(28)
 ) subtractor(.dataa({b[i*26+:26],a[i*26+:26]}),.datab(2'b11),.result(full_difference),.clock0(1'b0),.aclr0(1'b0),.ena0(1'b1),.signa(1'b0),.signb(1'b0),.addnsub1(1'b0),.addnsub3(1'b1));
assign y[i*26+:26]=full_difference[25:0];
end endgenerate
endmodule
