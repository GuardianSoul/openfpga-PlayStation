// SPDX-License-Identifier: MIT
// psx_cue_mount against a model of the APF host (scripts/run_cue_mount.py):
// target reads, getfile and openfile over a small file system, bridge writes
// and reads of the path struct, and a stand-in for psx_cd_stream that loads a
// cue into DISC_SLOT, then reads the table file, sectors and table sizes.
// CH=1 (+cheats): the cheat port on a model main RAM; frames are flipped
// with the Cheats setting off, then on, and the RAM is checked (+cheatcheck
// file: halfword index and value per line). Every run checks that the
// firmware's stack (0x3E00-0x3FFF) keeps 64 bytes unused (stores come
// within 64 bytes of its floor only if it overflows into the variables).
// A launch counts the firmware's tries (its 4-byte reads of slot 0 at
// offset 0) and the shortest stretch with no command before a retry; +cold=N
// (a cold card) answers the first N commands with an error, +tries=N checks
// the tries and +minpause=N that pause (cycles).
`timescale 1ns/1ps
module tb_cue_mount;
    parameter FW="psx_cue_fw";
    parameter LC=0;
    parameter CH=0;
    parameter BOUND_COUNT=16;
    parameter MOUNT_LIMIT=20000000;
    parameter STREAM_LIMIT=2000000;
    localparam [31:0] STRUCT=32'h2C000000;
    reg clk=0; always #5 clk=~clk;
    reg in_menu=0, allcomplete=0; wire s_allcomplete;
    reg [1:0] disc_setting=0; wire table_grant;
    reg reset=1;

    // ---------------- file system ----------------
    reg [7:0] fs_bytes[0:4194303];
    reg [31:0] fs_meta[0:255];          // per file: start, size
    reg [31:0] fs_path[0:8191];        // per file: 64 words, LE, NUL-terminated
    reg [31:0] table_exp[0:399];
    reg [31:0] cheatcheck[0:127];
    reg [31:0] bounds[0:BOUND_COUNT-1];
    reg [7:0] image[0:2097151];
    integer nfiles, picked, expect_size, nsectors, expect_fail, expect_bin, launch, expect_json, size2, nsectors2;
    reg [7:0] slot_of[0:1023];
    integer sk; reg [8*16:1] pname;
    reg [8*256-1:0] dir;

    // ---------------- APF bridge ----------------
    reg bridge_wr=0, bridge_rd=0; reg [31:0] bridge_addr=0, bridge_data=0;
    reg slot_update=0; reg [15:0] slot_update_id=0; reg [31:0] slot_update_size=0;
    wire s_slot_update; wire [15:0] s_slot_update_id; wire [31:0] s_slot_update_size;
    wire s_bridge_wr; wire [31:0] s_bridge_addr, s_bridge_data, struct_rd_data;
    reg [9:0] table_addr=0; reg [31:0] table_q=0; wire [31:0] table_view;
    reg u_read=0; wire u_ready; reg [15:0] u_id=0; reg [31:0] u_offset=0, u_bridge=0, u_length=0;
    wire u_done; wire [2:0] u_error;
    wire d_read, d_getfile, d_openfile; wire [15:0] d_id; wire [31:0] d_offset, d_bridge, d_length;
    reg d_done=1; reg [2:0] d_error=0;
    wire cue_mounted, mount_failed;
    wire [255:0] dbg_words;   // probe readout (DEBUG)
    wire [31:0] param_struct, resp_struct;

    reg cheats_on=0, vbl_tgl=0, ch_ack=0; reg [15:0] ch_q=0;
    wire ch_tgl, ch_rnw, ch_byte; wire [20:0] ch_addr; wire [15:0] ch_data;
    psx_cue_mount #(.LIBCRYPT(LC),.CHEATS(CH),.DEBUG(1),.FIRMWARE0({FW,"0.hex"}),.FIRMWARE1({FW,"1.hex"}),.FIRMWARE2({FW,"2.hex"}),.FIRMWARE3({FW,"3.hex"})) dut(.clk(clk),.reset(reset),.in_menu(in_menu),.allcomplete(allcomplete),.disc_setting(disc_setting),.table_grant(table_grant),.s_allcomplete(s_allcomplete),.bridge_wr(bridge_wr),.bridge_rd(bridge_rd),.bridge_addr(bridge_addr),.bridge_data(bridge_data),
        .struct_rd_data(struct_rd_data),.slot_update(slot_update),.slot_update_id(slot_update_id),.slot_update_size(slot_update_size),
        .s_slot_update(s_slot_update),.s_slot_update_id(s_slot_update_id),.s_slot_update_size(s_slot_update_size),
        .s_bridge_wr(s_bridge_wr),.s_bridge_addr(s_bridge_addr),.s_bridge_data(s_bridge_data),
        .table_addr(table_addr),.table_q(table_q),.table_view(table_view),
        .u_read(u_read),.u_ready(u_ready),.u_id(u_id),.u_offset(u_offset),.u_bridge(u_bridge),.u_length(u_length),
        .u_done(u_done),.u_error(u_error),.d_read(d_read),.d_getfile(d_getfile),.d_openfile(d_openfile),.d_id(d_id),
        .d_offset(d_offset),.d_bridge(d_bridge),.d_length(d_length),.d_done(d_done),.d_error(d_error),
        .param_struct(param_struct),.resp_struct(resp_struct),.cue_mounted(cue_mounted),.mount_failed(mount_failed),.dbg_words(dbg_words),
        .cheats_on(cheats_on),.vbl_tgl(vbl_tgl),.ch_ack(ch_ack),.ch_q(ch_q),.ch_tgl(ch_tgl),.ch_rnw(ch_rnw),.ch_byte(ch_byte),
        .ch_addr(ch_addr),.ch_data(ch_data));

    // ---------------- main RAM model (CH) ----------------
    reg [15:0] mram[0:1048575];
    integer cheat_accesses=0, min_store='h4000;
    initial for(mi=0;mi<1048576;mi=mi+1) mram[mi]=0;
    initial forever begin
        @(posedge clk); #1;
        if(ch_tgl!==ch_ack && CH!=0) begin
            repeat(12) @(posedge clk); #1;
            if(ch_rnw) ch_q=mram[ch_addr[20:1]];
            else if(!ch_byte) mram[ch_addr[20:1]]=ch_data;
            else if(ch_addr[0]) mram[ch_addr[20:1]][15:8]=ch_data[7:0];
            else mram[ch_addr[20:1]][7:0]=ch_data[7:0];
            cheat_accesses=cheat_accesses+1;
            repeat(3) @(posedge clk); #1; ch_ack=ch_tgl;
        end
    end
    // the stack (0x3E00-0x3FFF): the lowest firmware store in it
    always @(posedge clk) if(dut.a_we && dut.adr[13:0]>=14'h3E00 && dut.adr[13:0]<min_store) min_store=dut.adr[13:0];

    integer errors=0, commands=0, opens=0, reads=0, cold=0, launch_tries=0, want_tries=0;
    time last_cmd=0, retry_gap=0;
    integer mount_commands, mount_opens, mount_reads;
    time mount_time;
    task fail(input [8*80-1:0] what); begin $display("FAIL: %0s", what); errors=errors+1; end endtask

    task bwrite(input [31:0] a, input [31:0] d);
        begin @(negedge clk); bridge_wr=1;bridge_addr=a;bridge_data=d; @(negedge clk); bridge_wr=0; end
    endtask
    task bread(input [31:0] a, output [31:0] d);
        // APF's bridge (io_bridge_peripheral): address, data sampled 4
        // clocks later, then bridge_rd; the data is the previous read's word.
        begin
            @(negedge clk); bridge_addr=a; repeat(4) @(negedge clk); bridge_rd=1; @(negedge clk); bridge_rd=0;
            bridge_addr=a+4; repeat(4) @(negedge clk); d=struct_rd_data; bridge_rd=1; @(negedge clk); bridge_rd=0;
        end
    endtask

    // ---------------- host model ----------------
    reg prev_rd=0, prev_gf=0, prev_of=0;
    integer i, j, f, start, size, k, match, kind, oform, mform, sw;
    reg [8191:0] real_path[0:127];
    integer fd, seek_result, byte_value;
    reg [31:0] w, pw;
    initial forever begin
        @(posedge clk); #1;
        if((d_read && !prev_rd) || (d_getfile && !prev_gf) || (d_openfile && !prev_of)) begin
            commands=commands+1;
            kind=d_read && !prev_rd ? 0 : d_getfile && !prev_gf ? 1 : 2;
            if(!s_allcomplete && kind==0 && d_id==0 && d_offset==0 && d_length==4) begin
                launch_tries=launch_tries+1;
                if(launch_tries>1 && (launch_tries==2 || $time-last_cmd<retry_gap)) retry_gap=$time-last_cmd;
            end
            prev_rd=d_read;prev_gf=d_getfile;prev_of=d_openfile;
            if($test$plusargs("trace")) $display("%0t cmd kind %0d id %0d off %0d br %08x len %0d",$time,kind,d_id,d_offset,d_bridge,d_length);
            repeat(3) @(negedge clk); d_done=0; d_error=0;
            repeat(5) @(negedge clk);
            if(commands<=cold) d_error=2;   // a cold card's failed read, getfile or open
            else if(kind==0) begin
                reads=reads+1;
                f=slot_of[d_id];
                if(f==255) d_error=3;
                else begin
                    start=fs_meta[2*f]; size=fs_meta[2*f+1];
                    if(d_offset+d_length>size) d_error=2;
                    else begin
                        if($test$plusargs("real_files")) begin
                            fd=$fopen(real_path[f],"rb");
                            if(!fd) $fatal(1,"cannot open private host file %0d",f);
                            seek_result=$fseek(fd,d_offset,0);
                            if(seek_result!=0) $fatal(1,"private host seek failed");
                        end
                        for(i=0;i<d_length;i=i+4) begin
                            for(j=0;j<4;j=j+1) begin
                                if(i+j>=d_length) w[8*j+:8]=0;
                                else if($test$plusargs("real_files")) begin
                                    byte_value=$fgetc(fd);
                                    if(byte_value<0) $fatal(1,"private host short read");
                                    w[8*j+:8]=byte_value;
                                end else w[8*j+:8]=fs_bytes[start+d_offset+i+j];
                            end
                            bwrite(d_bridge+i,w);
                        end
                        if($test$plusargs("real_files")) $fclose(fd);
                    end
                end
            end else if(kind==1) begin
                f=slot_of[d_id];
                if(f==255 || $test$plusargs("nogetfile")) d_error=3;   // nogetfile: APF gives a game JSON's slots no path
                else for(i=0;i<64;i=i+1) bwrite(resp_struct+4*i,fs_path[64*f+i]);
            end else begin
                // openform=N: this host takes a path only in the firmware's
                // open form N (0 as getfile wrote it, 1 bytes swapped in each
                // word, 2 from the second struct) and answers any other with
                // 4, as the Pocket answered form 0.
                if(!$value$plusargs("openform=%d",oform)) oform=0;
                opens=opens+1;
                match=255; mform=-1;
                for(sw=0;sw<2;sw=sw+1) for(f=0;f<nfiles;f=f+1) begin
                    k=1;
                    for(i=0;i<64 && k==1;i=i+1) begin
                        bread(param_struct+4*i,pw); w=fs_path[64*f+i];
                        if(sw) pw={pw[7:0],pw[15:8],pw[23:16],pw[31:24]};
                        for(j=0;j<4;j=j+1) if(k==1) begin
                            if(pw[8*j+:8]!=w[8*j+:8]) k=0;
                            else if(w[8*j+:8]==0) k=2;
                        end
                    end
                    if(k==2 && match==255) begin match=f; mform=param_struct!=STRUCT ? (sw ? 5 : 2) : sw; end
                end
                bread(param_struct+256,pw); if(pw!=0) fail("openfile flags not 0");
                if(match==255) d_error=oform ? 4 : 1;
                else if(mform!=oform) d_error=4;
                else slot_of[d_id]=match;
            end
            if($test$plusargs("trace")) $display("%0t   done err %0d",$time,d_error);
            @(negedge clk); d_done=1; last_cmd=$time;
        end
        prev_rd=d_read;prev_gf=d_getfile;prev_of=d_openfile;
    end

    // ---------------- stream stand-in ----------------
    reg [31:0] cap[0:2047];
    reg prev_sw=0;
    always @(posedge clk) begin
        prev_sw<=s_bridge_wr;
        if(s_bridge_wr && !prev_sw && s_bridge_addr[31:13]==19'h12000) cap[s_bridge_addr[12:2]]<=s_bridge_data;
    end
    task stream_read(input [15:0] id, input [31:0] off, input [31:0] br, input [31:0] len);
        begin
            @(negedge clk); u_read=1;u_id=id;u_offset=off;u_bridge=br;u_length=len;
            while(!u_ready) @(negedge clk);
            @(negedge clk); u_read=0;
            if(u_done) fail("done still high after accept");
            tries=0; while(!u_done && tries<STREAM_LIMIT) begin @(negedge clk); tries=tries+1; end
            if(!u_done) begin $fatal(1,"stream read timed out (bench limit)"); end
        end
    endtask
    reg [31:0] tmem[0:63];
    always @(posedge clk) table_q<=tmem[table_addr];

    reg [8*256-1:0] base;
    integer sec, s_got, tries, mi, mj, mk; reg [31:0] mw;
    reg got_update; reg [31:0] got_size;
    always @(posedge clk) if(s_slot_update && s_slot_update_id==16'd101) begin got_update<=1;got_size<=s_slot_update_size;end

    // A mounted virtual disc in slot dslot (101 or 201): table sizes, the
    // table file, sectors at every file boundary, a read past the end.
    task check_disc(input [15:0] dslot, input integer size, input integer nsec);
        begin
            // ID/size lookups: DISC_SLOT and TABLE_SLOT at their virtual sizes.
            @(negedge clk); table_addr=dslot==101 ? 2 : 8; @(negedge clk); table_addr=dslot==101 ? 3 : 9; @(negedge clk); @(negedge clk);
            if(table_view!==size) fail("table view: disc size");
            table_addr=dslot==101 ? 4 : 10; @(negedge clk); table_addr=dslot==101 ? 5 : 11; @(negedge clk); @(negedge clk);
            if(table_view!==1600) fail("table view: table size");
            table_addr=6; @(negedge clk); table_addr=7; @(negedge clk); @(negedge clk);
            if(table_view!==0) fail("table view: other slot");
            // The table file.
            stream_read(dslot+1,0,32'h24001000,1600);
            if(u_error!=0) fail("table read error");
            for(mi=0;mi<400;mi=mi+1) if(cap[1024+mi]!==table_exp[mi]) begin
                $display("table word %0d: got %08x want %08x",mi,cap[1024+mi],table_exp[mi]); fail("track table word"); end
            // Sectors, including every file boundary (out of order: reopens).
            for(mk=0;mk<BOUND_COUNT;mk=mk+1) if(bounds[mk]!==32'hffffffff) begin
                sec=bounds[mk];
                stream_read(dslot,sec*2352,32'h24000000,2352);
                if(u_error!=0) fail("sector read error");
                for(mi=0;mi<588;mi=mi+1) begin
                    for(mj=0;mj<4;mj=mj+1) mw[8*mj+:8]=image[($test$plusargs("real_files") ? mk : sec)*2352+4*mi+mj];
                    if(cap[mi]!==mw) begin $display("sector %0d word %0d: got %08x want %08x",sec,mi,cap[mi],mw); fail("sector data"); mi=588; end
                end
            end
            // Past the end: an APF error comes back.
            stream_read(dslot,nsec*2352,32'h24000000,2352);
            if(u_error==0) fail("read past the disc succeeded");
        end
    endtask

    initial begin
        if(!$value$plusargs("base=%s",base)) $fatal(1,"no +base");
        if($test$plusargs("real_files")) $readmemh({base,"/real_paths.hex"},real_path);
        else $readmemh({base,"/fs.hex"},fs_bytes);
        $readmemh({base,"/meta.hex"},fs_meta);
        $readmemh({base,"/paths.hex"},fs_path);
        if(!$value$plusargs("nfiles=%d",nfiles)) $fatal(1,"no +nfiles");
        if(!$value$plusargs("picked=%d",picked)) $fatal(1,"no +picked");
        if(!$value$plusargs("size=%d",expect_size)) expect_size=0;
        if(!$value$plusargs("sectors=%d",nsectors)) nsectors=0;
        if(!$value$plusargs("size2=%d",size2)) size2=0;
        if(!$value$plusargs("sectors2=%d",nsectors2)) nsectors2=0;
        expect_fail=$test$plusargs("fail");
        expect_bin=$test$plusargs("bin");
        launch=$test$plusargs("launch");
        expect_json=$test$plusargs("json");
        if(!$value$plusargs("cold=%d",cold)) cold=0;
        if(!$value$plusargs("tries=%d",want_tries)) want_tries=0;
        if(nsectors!=0) begin $readmemh({base,"/table.hex"},table_exp); $readmemh({base,"/image.hex"},image); $readmemh({base,"/bounds.hex"},bounds); end
        for(mi=0;mi<1024;mi=mi+1) slot_of[mi]=255;
        for(mi=0;mi<64;mi=mi+1) tmem[mi]=0;
        tmem[0]=32'h20; tmem[1]=524288; tmem[2]=101; tmem[3]=launch ? 0 : fs_meta[2*picked+1]; tmem[4]=102; tmem[5]=0; tmem[6]=100; tmem[7]=0;
        tmem[8]=201; tmem[9]=0; tmem[10]=202; tmem[11]=0;   // the Disc menu's disc 2 (size 0: not in the game file)
        got_update=0;
        repeat(4) @(negedge clk); reset=0;
        if(launch) begin
            // The user picks a file from the launch list (slot 0); APF loads
            // the slots, then the stream waits for s_allcomplete.
            if(picked>=0) slot_of[0]=picked;
            if($value$plusargs("cue100=%d",mi)) slot_of[100]=mi;   // a game JSON's slots
            if($value$plusargs("trk102=%d",mi)) slot_of[102]=mi;
            if($value$plusargs("slot99=%d",mi)) slot_of[99]=mi;    // the game JSON's .sbi
            if($value$plusargs("slot98=%d",mi)) slot_of[98]=mi;    // its cheat list
            if($value$plusargs("slot97=%d",mi)) slot_of[97]=mi;    // its track sizes
            // Its .bin files in 101, 102, ... (pupdate, Pocket Sync, 0.5.2)
            for(sk=101;sk<120;sk=sk+1) begin $sformat(pname,"slot%0d=%%d",sk); if($value$plusargs(pname,mi)) slot_of[sk]=mi; end
            // +sizes / +sizes_swap: the Chip32 loader's size block (psx_bios_check.asm
            // fw_size) for slots 0, 99, 100, 101, 102, 98, then "SIZE"; _swap writes
            // every word byte-swapped, as a Chip32 pmpw may arrive.
            if($test$plusargs("sizes") || $test$plusargs("sizes_swap")) begin
                sw=$test$plusargs("sizes_swap");
                for(mi=0;mi<6;mi=mi+1) begin
                    sk=mi==0 ? 0 : mi==1 ? 99 : mi==2 ? 100 : mi==3 ? 101 : mi==4 ? 102 : 98;
                    w=slot_of[sk]!=255 ? fs_meta[2*slot_of[sk]+1] : 0;
                    bwrite(32'h2C0001C4+4*mi, sw ? {w[7:0],w[15:8],w[23:16],w[31:24]} : w);
                end
                bwrite(32'h2C0001C0, sw ? 32'h455A4953 : 32'h53495A45);
            end
            @(negedge clk); allcomplete=1; last_cmd=$time;
            tries=0;
            while(!s_allcomplete && tries<MOUNT_LIMIT) begin @(negedge clk); tries=tries+1; end
            if(!s_allcomplete) begin fail("stream allcomplete never released"); $finish; end
            $display("launch: %0d tries, shortest pause before a retry %0d cycles",launch_tries,retry_gap/10);
            if(want_tries && launch_tries!=want_tries) fail("launch tries");
            if($value$plusargs("minpause=%d",mi) && retry_gap/10<mi) fail("no pause before a launch retry");
            got_size=dut.view_disc ? dut.disc_bytes : 0;
            if(got_update) fail("launch pick sent a slot update");
            // The stream's table lookups see the disc's size.
            if(dut.view_disc) begin
                @(negedge clk); table_addr=2; @(negedge clk); table_addr=3; @(negedge clk); @(negedge clk);
                if(table_view!==got_size) fail("launch: table view disc size");
            end
        end else begin
            // The user picks a file with Load disc.
            slot_of[101]=picked;
            @(negedge clk); slot_update=1;slot_update_id=101;slot_update_size=fs_meta[2*picked+1];
            @(negedge clk); slot_update=0;
            tries=0;
            while(!got_update && tries<MOUNT_LIMIT) begin @(negedge clk); tries=tries+1; end
            if(!got_update) begin fail("no slot update for the stream"); $finish; end
        end
        mount_time=$time; mount_commands=commands; mount_opens=opens; mount_reads=reads;
        $display("mount_cost: cycles=%0d reads=%0d opens=%0d commands=%0d",mount_time/10,mount_reads,mount_opens,mount_commands);
        if($value$plusargs("maxreads=%d",mi) && mount_reads>mi) fail("boot mount probed sizes the Chip32 loader gave");
        $display("mount: size %0d after %0d commands (%0d opens), cue_mounted %0d failed %0d",got_size,commands,opens,cue_mounted,mount_failed);
        $display("probe: %h forms %h mask %h fail line %0d forms err %h, last cmd %h, last failed %h, path \"%s\" .. \"%s\"",dbg_words[31:27],dbg_words[26:16],dbg_words[15:0],dbg_words[43:32],dbg_words[61:44],dbg_words[95:64],dbg_words[127:96],{dbg_words[159:128],dbg_words[191:160]},{dbg_words[223:192],dbg_words[255:224]});
        if(expect_json) begin
            if(cue_mounted || dut.view_disc || mount_failed) fail("game JSON was not left to APF");
        end else if(expect_fail) begin
            if(got_size!=0 || !mount_failed || cue_mounted) fail("bad cue was mounted");
        end else if(expect_bin) begin
            if(got_size!=fs_meta[2*picked+1] || cue_mounted) fail("plain image not passed through");
            if(launch && (!dut.view_disc || dut.view_table)) fail("launch bin not mounted");
            // Reads pass through unchanged.
            stream_read(101,2352,32'h24000000,2352);
            for(mi=0;mi<588;mi=mi+1) begin
                for(mj=0;mj<4;mj=mj+1) mw[8*mj+:8]=fs_bytes[fs_meta[2*picked]+2352+4*mi+mj];
                if(cap[mi]!==mw) begin fail("passthrough sector data"); mi=588; end
            end
        end else begin
            if(got_size!=expect_size || !cue_mounted) fail("virtual disc size");
            check_disc(101,expect_size,nsectors);
            // Other slots pass through untouched.
            slot_of[50]=picked;
            stream_read(50,0,32'h24000000,4);
            if(u_error!=0) fail("passthrough read");
            if(size2!=0) begin
                // The menu's Disc 2: lookups wait while it loads, then every
                // disc slot shows it.
                @(negedge clk); disc_setting=1;
                @(negedge clk); if(table_grant) fail("menu Disc did not hold the lookups");
                tries=0; while(!table_grant && tries<MOUNT_LIMIT) begin @(negedge clk); tries=tries+1; end
                if(!table_grant || !cue_mounted) begin fail("menu Disc 2 did not load"); $finish; end
                $readmemh({base,"/table2.hex"},table_exp); $readmemh({base,"/image2.hex"},image); $readmemh({base,"/bounds2.hex"},bounds);
                check_disc(201,size2,nsectors2);
                $display("menu: disc 2 after %0d commands (%0d opens)",commands,opens);
                // A Disc the game doesn't have keeps disc 2.
                @(negedge clk); disc_setting=3;
                tries=0; while(!table_grant && tries<MOUNT_LIMIT) begin @(negedge clk); tries=tries+1; end
                @(negedge clk); table_addr=2; @(negedge clk); table_addr=3; @(negedge clk); @(negedge clk);
                if(table_view!==size2) fail("absent Disc 4 changed the disc");
                // Back to disc 1.
                @(negedge clk); disc_setting=0;
                @(negedge clk); tries=0; while(!table_grant && tries<MOUNT_LIMIT) begin @(negedge clk); tries=tries+1; end
                $readmemh({base,"/table.hex"},table_exp); $readmemh({base,"/image.hex"},image); $readmemh({base,"/bounds.hex"},bounds);
                check_disc(101,expect_size,nsectors);
            end
        end
        // Cheats (before the pick below, which drops the launched game's list).
        if($test$plusargs("cheats")) begin
            // initial RAM: a halfword incremented per frame, a byte compared
            mram['h8020]=16'h0100;
            repeat(2000000) @(negedge clk);   // the list is read after the mount
            for(mi=0;mi<2;mi=mi+1) begin repeat(300000) @(negedge clk); vbl_tgl=!vbl_tgl; end
            repeat(300000) @(negedge clk);
            if(cheat_accesses!=0) fail("cheats applied with the setting off");
            cheats_on=1;
            for(mi=0;mi<3;mi=mi+1) begin repeat(300000) @(negedge clk); vbl_tgl=!vbl_tgl; end
            repeat(300000) @(negedge clk);
            if(!$value$plusargs("cheatcheck=%s",pname)) $fatal(1,"no +cheatcheck");
            $readmemh({base,"/cheatcheck.hex"},cheatcheck);
            for(mi=0;mi<64 && cheatcheck[2*mi]!==32'hffffffff;mi=mi+1)
                if(mram[cheatcheck[2*mi]]!==cheatcheck[2*mi+1][15:0]) begin
                    $display("cheat RAM halfword %h: got %h want %h",cheatcheck[2*mi],mram[cheatcheck[2*mi]],cheatcheck[2*mi+1][15:0]); fail("cheat RAM"); end
            $display("cheats: %0d main RAM accesses",cheat_accesses);
        end
        // Firmware echoes with the menu closed: a mounted cue stays mounted.
        if(!expect_fail && !expect_bin && !expect_json) begin
            got_update=0;
            repeat(300) @(negedge clk);
            @(negedge clk); slot_update=1;slot_update_id=103;slot_update_size=1234;
            @(negedge clk); slot_update_id=101;
            @(negedge clk); slot_update=0;
            repeat(2000) @(negedge clk);
            if(got_update || !cue_mounted || dut.upd_pending) fail("echo update remounted the disc");
            // After the menu was open, the same update is a pick.
            @(negedge clk); in_menu=1;
            repeat(4) @(negedge clk); in_menu=0;
            repeat(100) @(negedge clk); slot_update=1;slot_update_id=101;slot_update_size=1234;
            @(negedge clk); slot_update=0;
            if(!dut.upd_pending) fail("a pick after the menu was ignored");
        end
        $display("stack: lowest store %h",min_store);
        if(min_store<'h3E40) fail("firmware stack within 64 bytes of 0x3E00");
        if(errors==0) $display("PASS"); else $display("%0d errors",errors);
        $finish;
    end
endmodule
