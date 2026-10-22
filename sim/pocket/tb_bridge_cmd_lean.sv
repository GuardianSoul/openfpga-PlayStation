// SPDX-License-Identifier: MIT
// Lockstep: the APF template's core_bridge_cmd against its --bridge-cmd-lean
// copy (scripts/prepare_bridge_cmd_lean.py), every output every clock, under
// random host bridge traffic and target commands whose inputs hold from the
// command's start until it is done (as psx_cue_mount's do). The host reads
// the parameter words 0x20-0x2C only while a command is announced, as APF
// does, never writes 0xF8001004/8 and reads 0x28/0x2C only during read/write
// commands: outside these the copy differs by design. +seed=N, +cycles=N.
`timescale 1ns/1ps
module tb_bridge_cmd_lean;
    reg clk=0; always #1 clk=~clk;
    reg [31:0] bridge_addr=0, bridge_wr_data=0; reg bridge_rd=0, bridge_wr=0;
    reg status_boot_done=1, status_setup_done=0, status_running=0;
    reg rq_ack=0, rq_ok=0, wq_ack=0, wq_ok=0;
    reg t_read=0, t_write=0, t_getfile=0, t_openfile=0;
    reg [15:0] t_id=0; reg [31:0] t_off=0, t_br=0, t_len=0;
    reg [31:0] param_struct=32'h2C000000, resp_struct=32'h2C000100;
    reg [9:0] dt_addr=0; reg dt_wren=0; reg [31:0] dt_data=0;
`define PORTS(sfx) \
    wire reset_n``sfx; wire [31:0] rd``sfx; wire rqr``sfx, rqw``sfx, upd``sfx, allc``sfx, rtcv``sfx, osn``sfx, ss``sfx, sl``sfx, tack``sfx, tdone``sfx; \
    wire [15:0] rqrid``sfx, rqwid``sfx, updid``sfx; wire [31:0] rqwsz``sfx, updsz``sfx, rtc0``sfx, rtc1``sfx, rtc2``sfx, dtq``sfx; wire [2:0] terr``sfx;
    `PORTS(_a)
    `PORTS(_b)
`define INST(mod, sfx) \
    mod u``sfx(.clk(clk),.reset_n(reset_n``sfx),.bridge_endian_little(1'b0),.bridge_addr(bridge_addr),.bridge_rd(bridge_rd),.bridge_rd_data(rd``sfx), \
        .bridge_wr(bridge_wr),.bridge_wr_data(bridge_wr_data),.status_boot_done(status_boot_done),.status_setup_done(status_setup_done),.status_running(status_running), \
        .dataslot_requestread(rqr``sfx),.dataslot_requestread_id(rqrid``sfx),.dataslot_requestread_ack(rq_ack),.dataslot_requestread_ok(rq_ok), \
        .dataslot_requestwrite(rqw``sfx),.dataslot_requestwrite_id(rqwid``sfx),.dataslot_requestwrite_size(rqwsz``sfx),.dataslot_requestwrite_ack(wq_ack),.dataslot_requestwrite_ok(wq_ok), \
        .dataslot_update(upd``sfx),.dataslot_update_id(updid``sfx),.dataslot_update_size(updsz``sfx),.dataslot_allcomplete(allc``sfx), \
        .rtc_epoch_seconds(rtc0``sfx),.rtc_date_bcd(rtc1``sfx),.rtc_time_bcd(rtc2``sfx),.rtc_valid(rtcv``sfx), \
        .savestate_supported(1'b0),.savestate_addr(32'b0),.savestate_size(32'b0),.savestate_maxloadsize(32'b0),.osnotify_inmenu(osn``sfx), \
        .savestate_start(ss``sfx),.savestate_start_ack(1'b0),.savestate_start_busy(1'b0),.savestate_start_ok(1'b0),.savestate_start_err(1'b0), \
        .savestate_load(sl``sfx),.savestate_load_ack(1'b0),.savestate_load_busy(1'b0),.savestate_load_ok(1'b0),.savestate_load_err(1'b0), \
        .target_dataslot_read(t_read),.target_dataslot_write(t_write),.target_dataslot_getfile(t_getfile),.target_dataslot_openfile(t_openfile), \
        .target_dataslot_ack(tack``sfx),.target_dataslot_done(tdone``sfx),.target_dataslot_err(terr``sfx), \
        .target_dataslot_id(t_id),.target_dataslot_slotoffset(t_off),.target_dataslot_bridgeaddr(t_br),.target_dataslot_length(t_len), \
        .target_buffer_param_struct(param_struct),.target_buffer_resp_struct(resp_struct), \
        .datatable_addr(dt_addr),.datatable_wren(dt_wren),.datatable_data(dt_data),.datatable_q(dtq``sfx));
    `INST(core_bridge_cmd, _a)
    `INST(core_bridge_cmd_lean, _b)
    wire [1023:0] out_a={reset_n_a,rd_a,rqr_a,rqw_a,upd_a,allc_a,rtcv_a,osn_a,ss_a,sl_a,tack_a,tdone_a,rtc0_a,rtc1_a,rtc2_a,dtq_a,terr_a};
    wire [1023:0] out_b={reset_n_b,rd_b,rqr_b,rqw_b,upd_b,allc_b,rtcv_b,osn_b,ss_b,sl_b,tack_b,tdone_b,rtc0_b,rtc1_b,rtc2_b,dtq_b,terr_b};
    // the slot request/update ids and sizes: compared while their strobe is up, when consumers sample them
    wire strobed_ok=(!rqr_a || rqrid_a===rqrid_b) && (!rqw_a || (rqwid_a===rqwid_b && rqwsz_a===rqwsz_b)) && (!upd_a || (updid_a===updid_b && updsz_a===updsz_b));
    integer strobes=0;
    always @(posedge clk) if(rqr_a || rqw_a || upd_a) strobes<=strobes+1;
    // the loader's acknowledge, a few clocks after a request
    integer ackwait=0;
    always @(posedge clk) begin
        if(rqr_a || rqw_a) begin ackwait<=ackwait+1; if(ackwait>=3) begin rq_ack<=1; wq_ack<=1; rq_ok<=ackwait[0]; wq_ok<=ackwait[1]; end end
        else begin ackwait<=0; rq_ack<=0; wq_ack<=0; end
    end
    integer cycles=0, mism=0, reads=0, preads=0, tcmds=0, fcmds=0, hcmds=0;
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(out_a!==out_b || !strobed_ok) begin mism=mism+1; if(mism<=5) $display("MISMATCH at %0d addr %08x rd %08x/%08x tstate %0d op %04x file %0d",cycles,bridge_addr,rd_a,rd_b,u_a.tstate,u_a.target_0[15:0],file_cmd); end
    end
    integer seed=1, maxc=200000, k, kc; reg file_cmd=0, cmd_busy=0;   // k: host process, kc: core process
    // explicit xorshift32 (deterministic per +seed in every simulator)
    reg [31:0] rs=32'h12345678;
    function automatic [31:0] rnd(input integer dummy); begin rs=rs^(rs<<13); rs=rs^(rs>>17); rs=rs^(rs<<5); rnd=rs; end endfunction
    // APF host: one bus access every few clocks
    task bus_write(input [31:0] a, input [31:0] d); begin @(negedge clk); bridge_addr=a; bridge_wr_data=d; bridge_wr=1; @(negedge clk); bridge_wr=0; repeat(rnd(0)%3) @(negedge clk); end endtask
    task bus_read(input [31:0] a); begin @(negedge clk); bridge_addr=a; bridge_rd=1; @(negedge clk); bridge_rd=0; reads=reads+1; repeat(1+rnd(0)%3) @(negedge clk); end endtask
    // the core: target commands with held inputs
    initial begin
        if(!$value$plusargs("seed=%d",seed)) seed=1; void'($value$plusargs("cycles=%d",maxc));
        rs=32'h9E3779B9^seed; if(rs==0) rs=1;
        forever begin
            repeat(5+rnd(0)%40) @(negedge clk);
            if(!cmd_busy && u_a.tstate==4'd0) begin
                kc=rnd(0)%4; t_id=rnd(0); t_off=rnd(0); t_br=rnd(0); t_len=rnd(0);
                file_cmd=kc>=2; cmd_busy=1; tcmds=tcmds+1; if(file_cmd) fcmds=fcmds+1;
                case(kc) 0: t_read=1; 1: t_write=1; 2: t_getfile=1; 3: t_openfile=1; endcase
                @(negedge clk); t_read=0; t_write=0; t_getfile=0; t_openfile=0;
                wait(tdone_a===1'b0); wait(tdone_a===1'b1); @(negedge clk); cmd_busy=0;   // done stays up until the next command starts
            end
        end
    end
    // the host side: answers target commands, issues host commands, pokes registers
    reg [31:0] r;
    initial begin
        repeat(4) @(negedge clk);
        while(cycles<maxc) begin
            k=rnd(0)%16;
            if(u_a.tstate==4'd14 /*WAITRESULT_RTR*/ && u_a.target_0[31:16]==16'h636D) begin
                bus_read(32'hF8001000); bus_write(32'hF8001000, 32'h6F6B0000);
            end else if(u_a.tstate==4'd15 /*WAITRESULT_DSO*/ && u_a.target_0[31:16]==16'h636D) begin
                bus_read(32'hF8001000);
                bus_read(32'hF8001020); bus_read(32'hF8001024); preads=preads+2;
                if(!file_cmd) begin bus_read(32'hF8001028); bus_read(32'hF800102C); preads=preads+2; end
                bus_write(32'hF8001000, 32'h62750000);
                repeat(rnd(0)%20) @(negedge clk);
                bus_write(32'hF8001000, {16'h6F6B, 13'b0, 3'(rnd(0)%8)});
            end else if(k<3) begin
                bus_write(32'hF8000020, rnd(0)); bus_write(32'hF8000024, rnd(0)); bus_write(32'hF8000028, rnd(0));
                case(rnd(0)%6)
                    0: bus_write(32'hF8000000, 32'h434D008A); 1: bus_write(32'hF8000000, 32'h434D0090); 2: bus_write(32'hF8000000, 32'h434D008F);
                    3: bus_write(32'hF8000000, 32'h434D0080); 4: bus_write(32'hF8000000, 32'h434D0082); default: bus_write(32'hF8000000, 32'h434D0000);
                endcase
                hcmds=hcmds+1;
                // the host waits for the result before it writes the next parameters: it
                // reads OK from host_0 over the bridge first, many clocks; 8 here (the
                // request/update strobes end one clock after the handler is idle)
                while(u_a.hstate!=4'd0) @(negedge clk);
                repeat(8) @(negedge clk);
            end else if(k<5) bus_write(32'hF8002000 | ({rnd(0)}%1024)*4, rnd(0));
            else if(k<7) begin dt_addr=rnd(0); dt_data=rnd(0); dt_wren=rnd(0)%2; @(negedge clk); dt_wren=0; end
            else if(k==7) begin status_setup_done=rnd(0)%2; status_running=rnd(0)%2; end
            else begin
                case(rnd(0)%8)
                    0: r=32'hF8000000; 1: r=32'hF8000004; 2: r=32'hF8000040; 3: r=32'hF8001000;
                    4: r=32'hF8001004; 5: r=32'hF8001008;
                    // the parameter words: only while a command is announced, when APF reads them
                    6: r=(u_a.tstate==4'd15 && u_a.target_0[31:16]!=16'h6F6B) ? 32'hF8001020 + 4*(rnd(0)%(file_cmd ? 2 : 4)) : 32'hF8001000;
                    default: r=32'hF8002000 | ({rnd(0)}%1024)*4;
                endcase
                if(r>=32'hF8001020 && r<32'hF8001030) preads=preads+1;
                bus_read(r);
            end
        end
        $display("cycles %0d, bus reads %0d (parameter words %0d), target commands %0d (file %0d), host commands %0d (strobed clocks %0d), mismatches %0d",cycles,reads,preads,tcmds,fcmds,hcmds,strobes,mism);
        if(mism==0 && tcmds>50 && fcmds>10 && preads>100 && strobes>100) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
