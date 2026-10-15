// psx_psram_stress against psx_psram_pair with the behavioural controller: 20,000 ops, no mismatches.
module tb_psram_stress #(parameter integer R=0, parameter integer M=0);
  reg clk=0; always #5 clk=~clk; reg reset=1;
  wire init, rv, rw, rr, rspv, rspr, rspe, done; wire [24:0] ra; wire [3:0] rh; wire [127:0] wd, rd; wire [15:0] be;
  wire [31:0] ops, fails, fe, fa; wire [3:0] fo; wire [24:0] fad; wire [15:0] dq0,dq1;
  psx_psram_pair #(.T_INIT(50)) pair(.clk(clk),.reset(reset),.initialized(init),.req_valid(rv),.req_write(rw),.req_ready(rr),.req_addr(ra),
    .req_halfwords(rh),.req_wdata(wd),.req_byte_en(be),.rsp_valid(rspv),.rsp_ready(rspr),.rsp_rdata(rd),.rsp_error(rspe),
    .fill_valid(),.fill_word(),.fill_data(),.idle(),.cram0_a(),.cram1_a(),.cram0_dq(dq0),.cram1_dq(dq1),.cram0_wait(1'b0),.cram1_wait(1'b0),
    .cram0_adv_n(),.cram1_adv_n(),.cram0_cre(),.cram1_cre(),.cram0_ce0_n(),.cram0_ce1_n(),.cram1_ce0_n(),.cram1_ce1_n(),
    .cram0_oe_n(),.cram1_oe_n(),.cram0_we_n(),.cram1_we_n(),.cram0_ub_n(),.cram0_lb_n(),.cram1_ub_n(),.cram1_lb_n());
  psx_psram_stress #(.OPS(20000), .READS_ONLY(R), .MIX(M)) s(.clk(clk),.reset(reset),.initialized(init),.req_valid(rv),.req_write(rw),.req_ready(rr),.req_addr(ra),
    .req_halfwords(rh),.req_wdata(wd),.req_byte_en(be),.rsp_valid(rspv),.rsp_ready(rspr),.rsp_rdata(rd),.rsp_error(rspe),
    .done(done),.ops(ops),.fails(fails),.fail_op(fo),.fail_addr(fad),.fail_expected(fe),.fail_actual(fa));
  initial begin repeat(3) @(posedge clk); reset=0; wait(done); $display("ops=%0d fails=%0d first op=%0d addr=%h exp=%h act=%h",ops,fails,fo,fad,fe,fa); $finish; end
  initial begin #50000000; $display("TIMEOUT ops=%0d fails=%0d state=%0d", ops, fails, s.state); $finish; end
endmodule
