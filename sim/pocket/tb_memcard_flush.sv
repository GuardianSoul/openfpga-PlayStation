`timescale 1ps/1ps
// psx_memcard_flush (#18) against a core_bridge_cmd target model and a
// psx_cue_mount command model with random traffic. QUIET_BITS is cut to 10.
module tb_memcard_flush;
    reg clk=0, clk2x=0;
    always #6734 clk=~clk;      // 74.25 MHz
    always #7381 clk2x=~clk2x;  // 67.74 MHz
    localparam QB=10, QUIET=(1<<QB)-1;
    reg card_write=0, enable=0, hold=0;
    reg c_read=0, c_getfile=0, c_openfile=0, c_busy=0;
    reg [15:0] c_id=0; reg [31:0] c_offset=0, c_bridge=0, c_length=0;
    wire c_done, t_read, t_write, t_getfile, t_openfile, busy, dirty;
    wire [15:0] t_id; wire [31:0] t_offset, t_bridge, t_length;
    reg t_done=1; reg [2:0] t_error=0;
    wire [2:0] last_error; wire [7:0] flushes;
    psx_memcard_flush #(.QUIET_BITS(QB)) dut (.*);

    integer cycle=0; always @(posedge clk) cycle=cycle+1;
    // --- APF target model (core_bridge_cmd): done falls 3 clocks after the
    // request, parameters sampled then and checked stable until done rises.
    reg m_busy=0; integer m_t=0, m_len=0; reg [1:0] m_kind=0; reg m_write=0;
    reg [15:0] m_id; reg [31:0] m_off, m_br, m_ln;
    reg force_error=0; integer apf_flushes=0;
    // executed cue commands, in order
    reg [113:0] exec_q[0:4095]; integer eq_w=0, eq_r=0;
    always @(posedge clk) begin
        if(t_read||t_write||t_getfile||t_openfile) begin
            if(m_busy) $fatal(1,"FATAL overlapping APF commands at %0d",cycle);
            if((t_read+t_write+t_getfile+t_openfile)!=1) $fatal(1,"FATAL two kinds at once");
            m_busy=1; m_t=0; m_write=t_write; m_kind=t_getfile?1:t_openfile?2:0;
            m_len=t_write ? 300+$urandom%400 : 20+$urandom%200;
        end else if(m_busy) begin
            m_t=m_t+1;
            if(m_t==3) begin t_done<=0; m_id=t_id; m_off=t_offset; m_br=t_bridge; m_ln=t_length; end
            if(m_t>3 && {t_id,t_offset,t_bridge,t_length}!=={m_id,m_off,m_br,m_ln}) $fatal(1,"FATAL parameters moved during a command");
            if(m_t==m_len) begin
                t_done<=1; m_busy=0;
                if(m_write) begin
                    if(m_id!==16'h0010 || m_off!==0 || m_br!==32'h20000000 || m_ln!==32'h20000)
                        $fatal(1,"FATAL flush parameters %h %h %h %h",m_id,m_off,m_br,m_ln);
                    apf_flushes=apf_flushes+1; t_error<=force_error ? 3'd4 : 3'd0;
                end else begin
                    exec_q[eq_w]={m_kind,m_id,m_off,m_br,m_ln}; eq_w=eq_w+1; t_error<=0;
                end
            end
        end
    end
    // --- cue command model: pulse, parameters held while busy; done must fall then rise
    reg cue_on=1; integer cue_rate=40, issued=0, completed=0; reg c_low=0;
    reg [113:0] c_cmd;
    always @(posedge clk) begin
        c_read<=0; c_getfile<=0; c_openfile<=0;
        if(c_busy) begin
            if(!c_done) c_low<=1;
            else if(c_low) begin
                c_busy<=0;
                if(eq_r>=eq_w) $fatal(1,"FATAL cue saw done before its command ran (%0d)",cycle);
                if(exec_q[eq_r]!==c_cmd) $fatal(1,"FATAL cue command changed: %h vs %h",exec_q[eq_r],c_cmd);
                eq_r=eq_r+1; completed=completed+1;
            end
        end else if(cue_on && $urandom%cue_rate==0) begin
            reg [1:0] k; reg [15:0] i; reg [31:0] o,b,l;
            k=$urandom%3; i=$urandom; o=$urandom; b=$urandom; l=$urandom;
            c_read<=k==0; c_getfile<=k==1; c_openfile<=k==2;
            c_id<=i; c_offset<=o; c_bridge<=b; c_length<=l;
            c_busy<=1; c_low<=0; issued=issued+1; c_cmd={k,i,o,b,l};
        end
    end
    // --- equivalence window: pass-through while no flush
    reg check_pass=0; integer pass_cycles=0;
    always @(negedge clk) if(check_pass) begin
        if({t_read,t_write,t_getfile,t_openfile,t_id,t_offset,t_bridge,t_length,c_done}!==
           {c_read,1'b0,c_getfile,c_openfile,c_id,c_offset,c_bridge,c_length,t_done})
            $fatal(1,"FATAL not a pass-through at %0d",cycle);
        pass_cycles=pass_cycles+1;
    end
    // --- flush timing: never sooner than QUIET clocks after the last card write
    integer last_write_cycle=-100000, early_limit;
    always @(posedge clk) if(t_write) begin
        if(cycle-last_write_cycle < QUIET) $fatal(1,"FATAL flush %0d clocks after a card write",cycle-last_write_cycle);
        if(hold) $fatal(1,"FATAL flush during a save state");
        if(!enable) $fatal(1,"FATAL flush while disabled");
    end
    task automatic writes(input integer n);
        repeat(n) begin @(posedge clk2x); card_write<=1; @(posedge clk2x); card_write<=0;
            last_write_cycle=cycle; repeat($urandom%50) @(posedge clk2x); end
    endtask
    task automatic clocks(input integer n); repeat(n) @(posedge clk); endtask
    task automatic expect_flushes(input integer n, input [8*20-1:0] what);
        if(apf_flushes!=n) $fatal(1,"FATAL %0s: %0d flushes, expected %0d",what,apf_flushes,n);
        else $display("%0s: %0d flushes",what,apf_flushes);
    endtask

    initial begin
        clocks(20);
        // A. no writes: pass-through, no flush
        enable=1; check_pass=1; clocks(4*QUIET); check_pass=0;
        expect_flushes(0,"no writes");
        // B. a save (burst of writes), busy cue traffic: one flush after the quiet time
        cue_rate=6; writes(30); clocks(QUIET/2);
        expect_flushes(0,"inside quiet time");
        clocks(3*QUIET); expect_flushes(1,"after a save");
        clocks(3*QUIET); expect_flushes(1,"stays clean");
        // C. writes during a save state: waits for hold to fall
        hold=1; writes(5); clocks(3*QUIET); expect_flushes(1,"during hold");
        hold=0; clocks(3*QUIET); expect_flushes(2,"after hold");
        // D. writes, then the card is reloaded (enable falls): dirty is dropped
        writes(5); enable=0; clocks(3*QUIET); enable=1; clocks(3*QUIET); expect_flushes(2,"after reload");
        // E. a card write during the flush: flushed again
        writes(3); wait(t_write); clocks(10); writes(2); clocks(4*QUIET); expect_flushes(4,"write during flush");
        // F. an error: kept, not retried until the next write
        force_error=1; writes(2); clocks(4*QUIET); expect_flushes(5,"error");
        if(last_error!=3'd4) $fatal(1,"FATAL error not kept");
        clocks(4*QUIET); expect_flushes(5,"no retry");
        force_error=0; writes(1); clocks(4*QUIET); expect_flushes(6,"after next write");
        if(last_error!=0) $fatal(1,"FATAL error not cleared");
        // G. quiet cue, many saves back to back
        repeat(5) begin writes(4); clocks(3*QUIET); end
        expect_flushes(11,"five saves");
        cue_on=0; wait(!c_busy); clocks(400);
        if(issued!=completed || eq_r!=eq_w) $fatal(1,"FATAL cue commands issued %0d completed %0d executed %0d",issued,completed,eq_w);
        if(dut.flushes!=apf_flushes) $fatal(1,"FATAL flush counter");
        $display("PASS memcard flush: %0d flushes, %0d cue commands (all in order), %0d pass-through cycles",apf_flushes,completed,pass_cycles);
        $finish;
    end
    initial begin #20000000000; $fatal(1,"FATAL timeout"); end
endmodule
