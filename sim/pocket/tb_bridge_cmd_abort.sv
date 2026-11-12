// SPDX-License-Identifier: MIT
// core_bridge_cmd's target path before (core_bridge_cmd_old) and after the save-cmd-abort step
// (core_bridge_cmd), side by side (docs/save-states-finish/drive-state.md section 8).
// Phase 1 (abort low): the same commands and Pocket answers to both; every output equal on every
// clock. Phase 2 (the step only): a command the Pocket never takes ("cm" stays), one it took ("bu")
// and never finished, an abort while idle, and a command after each abort that the Pocket answers.
// Result: one line "BRIDGE PASS ..." or "BRIDGE FAIL <why>".
`timescale 1ns/1ps
module tb_bridge_cmd_abort;
    reg clk = 0; always #6.734 clk = ~clk;   // 74.25 MHz
    reg [31:0] bridge_addr = 0, bridge_wr_data = 0; reg bridge_rd = 0, bridge_wr = 0;
    reg t_read = 0, abort = 0;
    reg [15:0] t_id = 16'd101; reg [31:0] t_off = 0, t_br = 32'h24000000, t_len = 2352;
    wire [31:0] rd_old, rd_new; wire ack_old, ack_new, done_old, done_new; wire [2:0] err_old, err_new;
    integer errors = 0, compared = 0, phase = 1;

    core_bridge_cmd_old u_old (.clk(clk), .bridge_endian_little(1'b0), .bridge_addr(bridge_addr), .bridge_rd(bridge_rd),
        .bridge_rd_data(rd_old), .bridge_wr(bridge_wr), .bridge_wr_data(bridge_wr_data),
        .status_boot_done(1'b1), .status_setup_done(1'b0), .status_running(1'b1),
        .dataslot_requestread_ack(1'b0), .dataslot_requestread_ok(1'b0), .dataslot_requestwrite_ack(1'b0), .dataslot_requestwrite_ok(1'b0),
        .savestate_supported(1'b0), .savestate_addr(32'd0), .savestate_size(32'd0), .savestate_maxloadsize(32'd0),
        .savestate_start_ack(1'b0), .savestate_start_busy(1'b0), .savestate_start_ok(1'b0), .savestate_start_err(1'b0),
        .savestate_load_ack(1'b0), .savestate_load_busy(1'b0), .savestate_load_ok(1'b0), .savestate_load_err(1'b0),
        .target_dataslot_read(t_read), .target_dataslot_write(1'b0), .target_dataslot_getfile(1'b0), .target_dataslot_openfile(1'b0),
        .target_dataslot_ack(ack_old), .target_dataslot_done(done_old), .target_dataslot_err(err_old),
        .target_dataslot_id(t_id), .target_dataslot_slotoffset(t_off), .target_dataslot_bridgeaddr(t_br), .target_dataslot_length(t_len),
        .target_buffer_param_struct(32'd0), .target_buffer_resp_struct(32'd0),
        .datatable_addr(10'd0), .datatable_wren(1'b0), .datatable_data(32'd0));
    core_bridge_cmd u_new (.clk(clk), .bridge_endian_little(1'b0), .bridge_addr(bridge_addr), .bridge_rd(bridge_rd),
        .bridge_rd_data(rd_new), .bridge_wr(bridge_wr), .bridge_wr_data(bridge_wr_data),
        .status_boot_done(1'b1), .status_setup_done(1'b0), .status_running(1'b1),
        .dataslot_requestread_ack(1'b0), .dataslot_requestread_ok(1'b0), .dataslot_requestwrite_ack(1'b0), .dataslot_requestwrite_ok(1'b0),
        .savestate_supported(1'b0), .savestate_addr(32'd0), .savestate_size(32'd0), .savestate_maxloadsize(32'd0),
        .savestate_start_ack(1'b0), .savestate_start_busy(1'b0), .savestate_start_ok(1'b0), .savestate_start_err(1'b0),
        .savestate_load_ack(1'b0), .savestate_load_busy(1'b0), .savestate_load_ok(1'b0), .savestate_load_err(1'b0),
        .target_dataslot_read(t_read), .target_dataslot_write(1'b0), .target_dataslot_getfile(1'b0), .target_dataslot_openfile(1'b0),
        .target_dataslot_ack(ack_new), .target_dataslot_done(done_new), .target_dataslot_err(err_new),
        .target_dataslot_abort(abort),
        .target_dataslot_id(t_id), .target_dataslot_slotoffset(t_off), .target_dataslot_bridgeaddr(t_br), .target_dataslot_length(t_len),
        .target_buffer_param_struct(32'd0), .target_buffer_resp_struct(32'd0),
        .datatable_addr(10'd0), .datatable_wren(1'b0), .datatable_data(32'd0));

    // phase 1: every output of the target path and the bridge read data equal
    always @(posedge clk) if (phase == 1 && $time > 200) begin
        compared = compared + 1;
        if ({ack_old, done_old, err_old, rd_old} !== {ack_new, done_new, err_new, rd_new}) begin
            $display("BRIDGE FAIL phase 1 differs at %0t: old %b %b %0d %h new %b %b %0d %h", $time, ack_old, done_old, err_old, rd_old,
                     ack_new, done_new, err_new, rd_new);
            errors = errors + 1; $finish;
        end
    end

    task tick(input integer n); repeat (n) @(negedge clk); endtask
    task bwrite(input [31:0] a, input [31:0] d); begin @(negedge clk); bridge_addr = a; bridge_wr_data = d; bridge_wr = 1; @(negedge clk); bridge_wr = 0; end endtask
    task bread(input [31:0] a, output [31:0] d); begin @(negedge clk); bridge_addr = a; bridge_rd = 1; @(negedge clk); bridge_rd = 0; tick(2); d = rd_new; end endtask
    task check(input ok, input [8*60-1:0] why); if (!ok) begin $display("BRIDGE FAIL %0s", why); errors = errors + 1; end endtask
    task raise; begin @(negedge clk); t_read = 1; tick(2); t_read = 0; end endtask
    // the Pocket: sees "cm" in target_0, answers "bu" after b clocks and "ok"+code after o more
    task pocket(input integer b, input integer o, input [2:0] code);
        reg [31:0] d; integer n;
        begin
            n = 0; d = 0;
            while (d[31:16] != 16'h636D && n < 100) begin bread(32'hF8001000, d); n = n + 1; end
            check(d[31:16] == 16'h636D, "no cm in target_0");
            tick(b); bwrite(32'hF8001000, {16'h6275, d[15:0]});
            tick(o); bwrite(32'hF8001000, {16'h6F6B, 13'd0, code});
        end
    endtask

    integer i; reg [31:0] d;
    // power-up state as Quartus gives it (core_bridge_cmd sets no initial value on these)
    initial begin u_old.tstate = 0; u_new.tstate = 0; u_old.target_0 = 0; u_new.target_0 = 0; end
    initial begin
        tick(50);
        // ---- phase 1: abort low, answers of several shapes ----
        for (i = 0; i < 6; i = i + 1) begin
            raise; pocket(5 + 7 * i, 20 + 31 * i, i % 3);
            tick(10);
            check(done_new && err_new == i % 3, "phase 1: done / error");
        end
        tick(20);
        phase = 2;
        // ---- (a) the Pocket never takes the command: "cm" stays ----
        raise; tick(400);
        check(!done_new, "(a) done before an answer");
        @(negedge clk); abort = 1; @(negedge clk); abort = 0; tick(2);
        check(done_new && err_new == 3'd7, "(a) abort: no done / error 7");
        bread(32'hF8001000, d); check(d[31:16] != 16'h636D, "(a) cm still up after the abort");
        raise; pocket(10, 30, 0); tick(10);
        check(done_new && err_new == 0, "(a) the command after the abort");
        // ---- (b) the Pocket took it ("bu") and never finished ----
        raise; begin : took
            reg [31:0] q; integer n; n = 0; q = 0;
            while (q[31:16] != 16'h636D && n < 100) begin bread(32'hF8001000, q); n = n + 1; end
            bwrite(32'hF8001000, {16'h6275, q[15:0]});
        end
        tick(300); check(ack_new && !done_new, "(b) busy not seen");
        @(negedge clk); abort = 1; @(negedge clk); abort = 0; tick(2);
        check(done_new && err_new == 3'd7, "(b) abort: no done / error 7");
        raise; pocket(3, 9, 2); tick(10);
        check(done_new && err_new == 2, "(b) the command after the abort");
        // ---- (c) abort while idle: nothing changes ----
        @(negedge clk); abort = 1; @(negedge clk); abort = 0; tick(4);
        check(done_new && err_new == 2, "(c) an idle abort changed the result");
        raise; pocket(4, 4, 0); tick(10);
        check(done_new && err_new == 0, "(c) the command after an idle abort");
        if (errors == 0) $display("BRIDGE PASS compared %0d clocks", compared);
        $finish;
    end
endmodule

// behavioural stand-in for the data table RAM (not on the target path)
module mf_datatable (input [9:0] address_a, address_b, input clock_a, clock_b, input [31:0] data_a, data_b,
                     input wren_a, wren_b, output reg [31:0] q_a = 0, q_b = 0);
    reg [31:0] mem [0:1023];
    always @(posedge clock_a) begin if (wren_a) mem[address_a] <= data_a; q_a <= mem[address_a]; end
    always @(posedge clock_b) begin if (wren_b) mem[address_b] <= data_b; q_b <= mem[address_b]; end
endmodule

// the APF 3-stage synchroniser as core_bridge_cmd uses it (i, o, clk)
module synch_3 (input wire i, output reg o = 0, input wire clk);
    reg s1 = 0, s2 = 0;
    always @(posedge clk) {o, s2, s1} <= {s2, s1, i};
endmodule
