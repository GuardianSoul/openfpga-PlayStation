`timescale 1fs/1fs
// psx_audio_guard + psx_audio_bridge (#21): lock never set, lock chatter,
// lock loss after start, forced overflow and missed deadline with
// self-recovery, restart; and the old core_top reset expression
// (ram_refresh || !audio_lock_sync) cycle-equal to the guard once the lock
// is good and stable. TICK_BITS is cut to 8 for sim time (the core uses 15).
module tb_audio_guard;
    reg src_clk=0, audio_clk=0;
    always #14762550 src_clk=~src_clk;     // 33.8688 MHz clk1x
    always #40690104 audio_clk=~audio_clk; // 12.288 MHz MCLK
    reg rst_n=0, pll_locked=0, restart=0;
    reg src_running=0, src_valid=0, blast=0;
    reg [31:0] src_frame=0;
    wire reset_async, clock_good, timed_out, recovered;
    wire src_full, src_overflow, frame_tick, fault, audio_lrck, audio_dac;
    wire [31:0] output_frame;
    wire [6:0] buffered_frames;
    wire [7:0] status;
    wire signed [15:0] rate_correction;
    localparam TB=8, TICK=1<<TB;
    psx_audio_guard #(.TICK_BITS(TB)) guard (.clk(src_clk),.rst_n(rst_n),.pll_locked(pll_locked),
        .restart(restart),.fatal(status[0]),.reset_async(reset_async),.clock_good(clock_good),
        .timed_out(timed_out),.recovered(recovered));
    psx_audio_bridge #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) bridge (.src_clk(src_clk),.audio_clk(audio_clk),
        .reset_async(reset_async),.src_running(src_running),.src_valid(src_valid),.src_frame(src_frame),
        .src_full(src_full),.src_overflow(src_overflow),.frame_tick(frame_tick),.output_frame(output_frame),
        .buffered_frames(buffered_frames),.fault(fault),.status(status),.rate_correction(rate_correction),
        .audio_lrck(audio_lrck),.audio_dac(audio_dac));

    // The core_top wrapper before #21, for the equivalence window.
    reg old_meta=0, old_sync=0, old_reset=1;
    always @(posedge src_clk or negedge rst_n)
        if(!rst_n) begin old_meta<=0; old_sync<=0; old_reset<=1; end
        else begin old_meta<=pll_locked; old_sync<=old_meta; old_reset<=restart || !old_sync; end
    reg check_equal=0; integer equal_cycles=0;
    always @(posedge src_clk) if(check_equal) begin
        if(old_reset!==reset_async) $fatal(1,"FATAL equivalence: old %b guard %b",old_reset,reset_async);
        equal_cycles=equal_cycles+1;
    end

    // 44.1 kHz source: a nonzero frame every 768 clk1x (or every clock when blasting)
    integer count=0, n=0;
    always @(negedge src_clk) begin
        src_valid=0;
        if(src_running && (blast || count>=767)) begin
            count=0; src_valid=1; n=n+1;
            src_frame={16'h3000+n[11:0],16'h1000+n[11:0]};
        end else count=count+1;
    end
    integer heard=0, lrck_edges=0, resets=0;
    reg last_lrck=0, last_reset=1;
    always @(posedge audio_clk) begin
        if(frame_tick && output_frame!=0) heard=heard+1;
        if(audio_lrck!=last_lrck) lrck_edges=lrck_edges+1;
        last_lrck=audio_lrck;
    end
    always @(posedge src_clk) begin if(reset_async && !last_reset) resets=resets+1; last_reset=reset_async; end

    task automatic clocks(input integer k); repeat(k) @(posedge src_clk); endtask
    task automatic expect_sound(input [8*24-1:0] what);
        integer h; begin
            h=heard; clocks(80000); // ~2.4 ms, 113 output frames
            if(heard-h<40) $fatal(1,"FATAL no sound %0s: %0d frames",what,heard-h);
            $display("sound %0s: %0d frames",what,heard-h);
        end
    endtask
    task automatic expect_silent(input [8*24-1:0] what);
        integer h; begin
            h=heard; clocks(20000);
            if(heard!=h) $fatal(1,"FATAL sound while %0s",what);
        end
    endtask

    initial begin
        clocks(10); rst_n=1; src_running=1;
        // 1. locked never rises: silent until the timeout, then sound anyway
        clocks(9*TICK);
        if(clock_good || !reset_async) $fatal(1,"FATAL released before the timeout");
        clocks(2*TICK+8);
        if(!clock_good || !timed_out || reset_async) $fatal(1,"FATAL timeout did not release");
        expect_sound("lock never set");
        // 2. a late lock and later loss change nothing once good
        pll_locked=1; clocks(3*TICK); pll_locked=0; clocks(3*TICK);
        if(reset_async || resets!=0) $fatal(1,"FATAL lock change reset the bridge");
        expect_sound("lock lost after start");
        // 3. power cycle (rst_n) with a chattering lock: no release while it chatters (< timeout)
        rst_n=0; clocks(4); rst_n=1;
        repeat(6) begin pll_locked=1; clocks(TICK/2); pll_locked=0; clocks(TICK/2+3); end
        if(clock_good) $fatal(1,"FATAL good while chattering");
        pll_locked=1; clocks(3*TICK);
        if(!clock_good || timed_out || reset_async) $fatal(1,"FATAL stable lock not latched");
        expect_sound("after chatter");
        // 4. equivalence with the old wrapper while the lock is good and stable, through a restart
        check_equal=1; clocks(500); restart=1; clocks(300); restart=0; clocks(500); check_equal=0;
        expect_sound("after restart");
        // 5. forced overflow: source blasts, fatal, muted, recovers by itself
        blast=1; clocks(400); blast=0;
        if(!status[0]) $fatal(1,"FATAL overflow not forced");
        clocks(TICK);
        if(heard==0) ;
        expect_sound("after overflow");
        if(!recovered) $fatal(1,"FATAL recovery bit not set");
        // 6. forced missed deadline
        @(posedge audio_clk); force bridge.missed_deadline=1; repeat(4) @(posedge audio_clk); release bridge.missed_deadline;
        if(!status[0]) $fatal(1,"FATAL deadline not forced");
        expect_sound("after missed deadline");
        if(status[0]) $fatal(1,"FATAL fault still set");
        $display("PASS audio guard: %0d resets, %0d equal cycles, %0d frames heard",resets,equal_cycles,heard);
        $finish;
    end
    initial begin #400000000000000; $fatal(1,"FATAL timeout"); end
endmodule
