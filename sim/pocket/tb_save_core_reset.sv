// SPDX-License-Identifier: MIT
// "Restart Game" during a save or a load: the real psx_save_exp1 through a core reset in the
// middle of a request, then a load request (scripts/run_save_core_reset.py). The bus writes are
// the routine's (ACK, SAVE_MODE, SPU_HOLD, CD_FREEZE, a page copy). Aux port: a model that answers
// every request after 12 clk2x and is not reset by the core reset (as psx_vram_psram).
// +define+HAVE_CORE_RESET: the device has the core_reset input (prepare_save_core_reset.py).
// Plusarg +CASE=save | load | held.
`timescale 1ns/1ps
module dpram_dif #(parameter addr_width_a=11, data_width_a=8, addr_width_b=9, data_width_b=32) (
    input wire clock_a, clken_a, input wire [addr_width_a-1:0] address_a, input wire [data_width_a-1:0] data_a,
    input wire wren_a, output reg [data_width_a-1:0] q_a,
    input wire clock_b, clken_b, input wire [addr_width_b-1:0] address_b, input wire [data_width_b-1:0] data_b,
    input wire wren_b, output reg [data_width_b-1:0] q_b);
    reg [31:0] mem [0:511];
    integer i;
    initial begin q_a = 0; q_b = 0; for (i = 0; i < 512; i = i + 1) mem[i] = 0; end
    always @(posedge clock_a) begin
        if (wren_a) mem[address_a[10:2]][8*address_a[1:0] +: 8] <= data_a;
        q_a <= mem[address_a[10:2]][8*address_a[1:0] +: 8];
    end
    always @(posedge clock_b) begin
        if (wren_b) mem[address_b] <= data_b;
        q_b <= mem[address_b];
    end
endmodule

module tb_save_core_reset;
    reg clk2x = 0, clk1x = 0, clk_74a = 0;
    always #7.38 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #6.734 clk_74a = !clk_74a;
    reg pll_reset = 1, core_reset = 1, core_held = 1;
    reg [12:0] bus_addr = 0; reg bus_write = 0; reg [7:0] bus_dataWrite = 0; wire [7:0] bus_dataRead;
    reg ss_start = 0, ss_load = 0;
    wire trap, save_mode, cd_freeze, spu_hold;
    wire start_ack, start_busy, start_ok, start_err, load_ack, load_busy, load_ok, load_err;
    wire aux_req, aux_we; wire [20:0] aux_addr; wire [31:0] aux_din; reg aux_done = 0;
    wire spuram_req;
    psx_save_exp1 dut (.clk2x(clk2x), .reset_2x(pll_reset),
`ifdef HAVE_CORE_RESET
        .core_reset(core_reset),
`endif
        .bus_addr(bus_addr), .bus_write(bus_write), .bus_dataWrite(bus_dataWrite), .bus_dataRead(bus_dataRead),
        .trap(trap), .save_mode(save_mode), .cd_freeze(cd_freeze), .timer_flags(12'd0), .gpu_ranges(63'd0),
        .clk1x(clk1x), .spu_hold(spu_hold), .spu_quiet(spu_hold), .spu_ss_rdata(32'd0), .spuram_req(spuram_req),
        .spuram_rdata(16'd0), .spuram_done(1'b0),
        .aux_req(aux_req), .aux_we(aux_we), .aux_addr(aux_addr), .aux_din(aux_din), .aux_dout(32'hA5A50000 | aux_addr), .aux_done(aux_done),
        .up_req(1'b0), .up_we(1'b0), .up_addr(21'd0), .up_din(32'd0),
        .clk_74a(clk_74a), .savestate_start(ss_start), .savestate_start_ack(start_ack), .savestate_start_busy(start_busy),
        .savestate_start_ok(start_ok), .savestate_start_err(start_err),
        .savestate_load(ss_load), .savestate_load_ack(load_ack), .savestate_load_busy(load_busy),
        .savestate_load_ok(load_ok), .savestate_load_err(load_err),
        .disc(3'd0), .disc_settled(1'b1), .disc_wrong(1'b0), .core_held(core_held), .cd_state(64'd0));

    // aux model: done 12 clk2x after the request; a request that falls before its done is a protocol break
    integer aux_wait = 0, aux_words = 0, aux_cut = 0, aux_after_edge = 0;
    reg aux_req_d = 0, edge_seen = 0;
    always @(posedge clk2x) begin
        aux_done <= 0; aux_req_d <= aux_req;
        if (aux_req && !aux_done) begin
            aux_wait <= aux_wait + 1;
            if (aux_wait == 11) begin aux_done <= 1; aux_wait <= 0; aux_words <= aux_words + 1; if (edge_seen) aux_after_edge <= aux_after_edge + 1; end
        end else aux_wait <= 0;
        if (aux_req_d && !aux_req && !aux_done && aux_wait != 0) aux_cut <= aux_cut + 1;
    end

    task bus_wr(input [12:0] a, input [7:0] d);
        begin
            @(posedge clk1x); bus_addr <= a; bus_dataWrite <= d; bus_write <= 1;
            @(posedge clk1x); bus_write <= 0;
            @(posedge clk1x);
        end
    endtask
    reg [7:0] status;
    task bus_rd(input [12:0] a);
        begin
            @(posedge clk1x); bus_addr <= a;
            repeat (3) @(posedge clk1x);
            status = bus_dataRead;
        end
    endtask
    task apf(input load);
        begin
            @(posedge clk_74a); if (load) ss_load <= 1; else ss_start <= 1;
            repeat (2) @(posedge clk_74a); ss_load <= 0; ss_start <= 0;
            repeat (40) @(posedge clk_74a);
        end
    endtask
    task show(input [8*24-1:0] label);
        begin
            bus_rd(13'h1800);
            $display("STATE %0s: save_mode=%b cd_freeze=%b spu_hold=%b trap=%b want_load=%b want_save=%b copy_busy=%b start_busy=%b start_ok=%b start_err=%b load_busy=%b load_ok=%b load_err=%b aux_words=%0d aux_after_edge=%0d aux_cut=%0d",
                     label, save_mode, cd_freeze, spu_hold, trap, status[2], status[1], status[0],
                     start_busy, start_ok, start_err, load_busy, load_ok, load_err, aux_words, aux_after_edge, aux_cut);
        end
    endtask
    task restart;   // "Restart Game": the Pocket holds the core in reset for a while
        begin
            @(posedge clk1x); core_reset <= 1; core_held <= 1; edge_seen <= 1;
            repeat (4000) @(posedge clk1x);
            show("in reset");
            @(posedge clk1x); core_reset <= 0; core_held <= 0;
            repeat (200) @(posedge clk1x);
        end
    endtask

    reg [8*8-1:0] which;
    integer guard;
    initial begin
        if (!$value$plusargs("CASE=%s", which)) which = "save";
        repeat (20) @(posedge clk2x); pll_reset <= 0;
        repeat (50) @(posedge clk1x);
        if (which == "held") begin
            // a wake: the load arrives while the core is held in reset, and runs after it
            apf(1);
            show("load in reset");
            @(posedge clk1x); core_reset <= 0; core_held <= 0;
            repeat (200) @(posedge clk1x);
            show("after reset");
        end else begin
            core_reset <= 0; core_held <= 0;
            repeat (50) @(posedge clk1x);
            apf(which == "load");
            guard = 0; while (!trap && guard < 2000) begin @(posedge clk1x); guard = guard + 1; end
            bus_wr(13'h1804, 8'd0);          // ACK
            bus_wr(13'h180E, 8'd1);          // SAVE_MODE
            bus_wr(13'h180C, 8'd1);          // SPU_HOLD
            if (which == "save") bus_wr(13'h180F, 8'd1);   // CD_FREEZE (a save freezes the drive)
            bus_wr(13'h1801, 8'd5); bus_wr(13'h1802, 8'd0);
            bus_wr(13'h1800, which == "load" ? 8'd3 : 8'd1);   // a page copy (512 words, about 6,700 clk2x)
            repeat (300) @(posedge clk1x);
            show("before reset");
            restart;
            show("after reset");
            // the game has booted: a Memory load is requested
            apf(1);
            repeat (200) @(posedge clk1x);
            show("after new load");
        end
        $display("DONE");
        $finish;
    end
endmodule
