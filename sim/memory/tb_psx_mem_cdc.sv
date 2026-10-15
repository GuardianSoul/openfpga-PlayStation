`timescale 1ns/1ps
`default_nettype none
module tb_psx_mem_cdc;
    parameter integer SRC_HALF = 10, DST_HALF = 8, DST_PHASE = 3;
    reg src_clk = 0, dst_clk = 0, reset_async = 1;
    always #(SRC_HALF) src_clk = !src_clk;
    initial begin #(DST_PHASE); forever #(DST_HALF) dst_clk = !dst_clk; end
    wire src_reset, dst_reset;
    reg s_valid = 0, s_write = 0, s_rsp_ready = 0;
    reg [24:0] s_addr = 0;
    reg [3:0] s_halfwords = 0;
    reg [127:0] s_wdata = 0;
    reg [15:0] s_byte_en = 0;
    wire s_ready, s_rsp_valid, s_rsp_error, d_valid, d_write, d_rsp_ready;
    wire [127:0] s_rsp_rdata, d_wdata;
    wire [24:0] d_addr;
    wire [3:0] d_halfwords;
    wire [15:0] d_byte_en;
    reg d_ready = 0, d_rsp_valid = 0, d_rsp_error = 0;
    reg [127:0] d_rsp_rdata = 0;
    reg block_request = 0, block_response = 0;
    reg pending = 0;
    integer delay_left = 0;
    integer completed = 0, accepted = 0, forwarded = 0, aborted = 0;
    reg [173:0] expected_request;
    reg [128:0] expected_response;
    reg [31:0] rng = 32'h50535805, backend_rng = 32'h12345678;
    reg stalled = 0, response_stalled = 0;
    reg [173:0] held_request;
    reg [128:0] held_response;
    psx_mem_cdc dut (.*);

    function automatic [128:0] reply(input reg [173:0] packet);
        reg wr;
        reg [24:0] address;
        reg [3:0] length;
        reg [127:0] data;
        reg [15:0] mask;
        begin
            {wr, address, length, data, mask} = packet;
            reply = {wr ^ address[24] ^ length[3], data ^ {address, length, mask, 83'h123456789abcdef012345}};
        end
    endfunction
    always @(posedge src_clk) begin
        if (!src_reset) begin
            if (s_valid && s_ready) begin
                accepted = accepted + 1;
                expected_request = {s_write, s_addr, s_halfwords, s_wdata, s_byte_en};
                expected_response = reply(expected_request);
            end
            if (response_stalled && (!s_rsp_valid || {s_rsp_error, s_rsp_rdata} !== held_response))
                $fatal(1, "CDC source response changed under backpressure");
            if (s_rsp_valid && {s_rsp_error, s_rsp_rdata} !== expected_response)
                $fatal(1, "CDC response payload mismatch");
            if (s_rsp_valid && s_rsp_ready) completed = completed + 1;
            response_stalled <= s_rsp_valid && !s_rsp_ready;
            held_response <= {s_rsp_error, s_rsp_rdata};
        end else response_stalled <= 0;
    end
    always @(posedge dst_clk or posedge reset_async) begin
        if (reset_async) begin
            pending <= 0; d_ready <= 0; d_rsp_valid <= 0;
            d_rsp_rdata <= 0; d_rsp_error <= 0; delay_left <= 0; stalled <= 0;
        end else if (dst_reset) begin
            pending <= 0; d_ready <= 0; d_rsp_valid <= 0; stalled <= 0;
        end else begin
            backend_rng <= backend_rng * 32'd1664525 + 32'd1013904223;
            d_ready <= !block_request && !pending && !d_rsp_valid && backend_rng[0];
            if (stalled && (!d_valid || {d_write, d_addr, d_halfwords, d_wdata, d_byte_en} !== held_request))
                $fatal(1, "CDC destination request changed under backpressure");
            stalled <= d_valid && !d_ready;
            held_request <= {d_write, d_addr, d_halfwords, d_wdata, d_byte_en};
            if (d_valid && d_ready) begin
                if (pending || d_rsp_valid || {d_write, d_addr, d_halfwords, d_wdata, d_byte_en} !== expected_request)
                    $fatal(1, "CDC request duplicated or corrupted");
                forwarded = forwarded + 1; pending <= 1; delay_left <= 3 + integer'(backend_rng[5:2]);
                {d_rsp_error, d_rsp_rdata} <= reply(expected_request);
                d_ready <= 0;
            end
            if (pending && !block_response) begin
                if (delay_left == 0) begin pending <= 0; d_rsp_valid <= 1; end
                else delay_left <= delay_left - 1;
            end
            if (d_rsp_valid && d_rsp_ready) d_rsp_valid <= 0;
        end
    end
    task automatic launch(input integer id);
        begin
            @(negedge src_clk);
            while (!s_ready) @(negedge src_clk);
            s_write = id[0]; s_addr = 25'(id * 99991); s_halfwords = 4'(id);
            rng = rng * 32'd1664525 + 32'd1013904223;
            s_byte_en = rng[15:0]; s_wdata[31:0] = rng;
            for (integer lane = 1; lane < 4; lane = lane + 1) begin
                rng = rng * 32'd1664525 + 32'd1013904223; s_wdata[lane*32 +: 32] = rng;
            end
            s_valid = 1;
            @(negedge src_clk);
            s_valid = 0; s_write = !s_write; s_addr = ~s_addr; s_halfwords = 0; s_wdata = ~s_wdata; s_byte_en = 0;
        end
    endtask
    task automatic consume(input integer hold_cycles);
        begin
            while (!s_rsp_valid) @(negedge src_clk);
            repeat (hold_cycles) begin
                if (s_ready) $fatal(1, "CDC admitted request before old response consumption");
                @(negedge src_clk);
            end
            s_rsp_ready = 1; @(negedge src_clk); s_rsp_ready = 0;
        end
    endtask
    task automatic reset_both;
        begin
            #1; reset_async = 1; s_valid = 0; s_rsp_ready = 0;
            #0.1;
            if (s_ready || s_rsp_valid || d_valid || d_rsp_ready) $fatal(1, "CDC did not gate transfers on asynchronous reset");
            repeat (4) @(negedge src_clk);
            reset_async = 0;
            while (src_reset || dst_reset) @(negedge src_clk);
            repeat (5) @(negedge src_clk);
            if (s_rsp_valid || d_valid) $fatal(1, "CDC stale transaction after reset");
        end
    endtask
    initial begin
        reset_both();
        for (integer id = 0; id < 600; id = id + 1) begin launch(id); consume(1 + id % 15); end
        block_request = 1; launch(601);
        while (!d_valid) @(negedge dst_clk);
        reset_both(); aborted = aborted + 1; block_request = 0;
        launch(602); consume(7);
        block_response = 1; launch(603);
        while (!pending) @(negedge dst_clk);
        reset_both(); aborted = aborted + 1; block_response = 0;
        launch(604); consume(2);
        launch(605); while (!s_rsp_valid) @(negedge src_clk);
        reset_both(); aborted = aborted + 1;
        for (integer id = 606; id < 706; id = id + 1) begin launch(id); consume(id % 5); end
        repeat (20) @(negedge src_clk);
        if (accepted != completed + aborted || forwarded != completed + 2 || completed != 702)
            $fatal(1, "CDC transaction accounting failed accepted=%0d forwarded=%0d completed=%0d", accepted, forwarded, completed);
        $display("PASS CDC src_half=%0d dst_half=%0d phase=%0d completed=%0d aborted=%0d",
                 SRC_HALF, DST_HALF, DST_PHASE, completed, aborted);
        $finish;
    end
    initial begin #10000000; $fatal(1, "CDC test timeout"); end
endmodule
`default_nettype wire
