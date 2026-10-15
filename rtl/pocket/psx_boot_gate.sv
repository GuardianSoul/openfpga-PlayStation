// SPDX-License-Identifier: MIT
// CPU reset asserts immediately and releases only on the CPU clock. A new
// freeze token is acknowledged after reset has reached two CPU clock edges.
// The loader uses that fresh acknowledgement before accepting any ROM data.
module psx_boot_gate (
    input wire host_clk, cpu_clk, reset_async, cpu_locked,
    input wire host_run, bios_ready, freeze_toggle,
    output wire cpu_reset,
    output wire freeze_ack
);
    wire stop_async = reset_async || !cpu_locked || !host_run || !bios_ready;
    (* async_reg="true", preserve, dont_merge *) reg [2:0] reset_pipe=3'b111;
    always @(posedge cpu_clk or posedge stop_async) begin
        if(stop_async) reset_pipe<=3'b111;
        else reset_pipe<={reset_pipe[1:0],1'b0};
    end
    assign cpu_reset=reset_pipe[2];

    (* async_reg="true", preserve, dont_merge *) reg freeze_meta=0, freeze_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg [2:0] reset_seen=0;
    (* async_reg="true", preserve, dont_merge *) reg lock_meta=0, lock_sync=0;
    (* preserve, dont_merge *) reg ack_cpu=0;
    always @(posedge cpu_clk or posedge reset_async) begin
        if(reset_async) begin freeze_meta<=0;freeze_sync<=0;reset_seen<=0;ack_cpu<=0;lock_meta<=0;lock_sync<=0;end
        else begin
            freeze_meta<=freeze_toggle;freeze_sync<=freeze_meta;
            reset_seen<={reset_seen[1:0],cpu_reset};
            lock_meta<=cpu_locked;lock_sync<=lock_meta;
            if(lock_sync && reset_seen[2] && cpu_reset) ack_cpu<=freeze_sync;
        end
    end
    (* async_reg="true", preserve, dont_merge *) reg ack_meta=0, ack_sync=0;
    always @(posedge host_clk or posedge reset_async) begin
        if(reset_async) begin ack_meta<=0;ack_sync<=0;end
        else begin ack_meta<=ack_cpu;ack_sync<=ack_meta;end
    end
    assign freeze_ack=ack_sync;
endmodule
