// SPDX-License-Identifier: GPL-2.0-only
// CD sample sequencing of a late SPU: original (BASE) against spu-cd-queue (QUEUE).
// Both SPUs see the same register writes, the same CD input (changed 7 clocks
// after every spu_tick, like cd_top) and the same SPU RAM latency schedule.
// +spikes=0: latency stays inside the 24-clock voice slot (never late). The two
//            completed-sample streams must be identical, voices included.
// +spikes=1: latency spikes make the SPU fall up to ~10 ticks behind (below the
//            15-tick sound_timeout). Voices are silent; every completed sample
//            is decoded back to the CD tick it mixed, and each SPU must mix
//            consecutive ticks. The count of skipped/repeated ticks is reported.
`timescale 1fs/1fs

module spu_lat_mem #(parameter BYTES=524288)(input clk,input ena,rnw,input [18:0] adr,
    input [31:0] din,input integer latency,output reg done=0,output reg [31:0] dout=0);
    byte unsigned data [0:BYTES-1];
    initial foreach(data[i]) data[i]=0;
    integer count=-1;
    reg [18:0] a=0;reg r=0;reg [15:0] w=0;
    always @(posedge clk) begin
        done<=0;
        if(ena) begin a<=adr;r<=rnw;w<=din[15:0];count<=latency;end
        else if(count==0) begin
            done<=1;count<=-1;
            if(r) dout<={16'b0,data[{a[18:1],1'b1}],data[{a[18:1],1'b0}]};
            else begin data[{a[18:1],1'b0}]=w[7:0];data[{a[18:1],1'b1}]=w[15:8];end
        end else if(count>0) count<=count-1;
    end
endmodule

module tb_spu_cd_queue;
logic clk1x=1,clk2x=1,clk2xIndex=0,ce=1,reset=1;
logic SPUon=1,SPUIRQTrigger=0,useSDRAM=1,REPRODUCIBLESPUIRQ=1,REPRODUCIBLESPUDMA=0,REVERBOFF=1,cpuPaused=0;
logic [15:0] cd_left=0,cd_right=0;
logic [9:0] bus_addr=0;logic [15:0] bus_dataWrite=0;logic bus_read=0,bus_write=0;
logic dma_read=0,dma_write=0;logic [15:0] dma_writedata=0;
logic mem_ack=0;logic [63:0] mem_DOUT=0;logic mem_DOUT_READY=0;
logic SS_reset=1,loading_savestate=0;logic [31:0] SS_DataWrite=0;logic [8:0] SS_Adr=0;logic SS_wren=0,SS_rden=0;
logic [15:0] SS_RAM_dataWrite=0;logic [18:0] SS_RAM_Adr=0;logic SS_RAM_request=0,SS_RAM_rnw=0;
integer latency=9;
INSTANCES
always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
reg toggle=0,sampled=0;
always @(posedge clk1x) toggle<=!toggle;
always @(posedge clk2x) begin sampled<=toggle;clk2xIndex<=sampled==toggle;end

// CD input like cd_top: new pair 7 clk1x after each spu_tick.
integer ticks=0,cd_delay=-1;
function automatic [15:0] cd_l(input integer t); cd_l=16'(t*97-20000); endfunction
function automatic [15:0] cd_r(input integer t); cd_r=16'(t*-61+3000); endfunction
always @(posedge clk1x) begin
    if(spu_tick_base) begin ticks<=ticks+1;cd_delay<=6;end
    else if(cd_delay==0) begin cd_left<=cd_l(ticks);cd_right<=cd_r(ticks);cd_delay<=-1;end
    else if(cd_delay>0) cd_delay<=cd_delay-1;
end

task write_reg(input [9:0] address,input [15:0] data);
 @(negedge clk1x);bus_addr=address;bus_dataWrite=data;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask

function integer expected(input integer sample);
 integer stage;
 stage=(sample*16384)>>>15;
 expected=(stage*16384)>>>15;
endfunction

// Completed-sample streams.
integer maxstash=0;
always @(posedge clk1x) if(int'(spu_base.stashedsamples)>maxstash) maxstash=int'(spu_base.stashedsamples);
integer nb=0,nq=0,timeouts=0,mismatches=0,spikes=0;
integer base_l[0:8191],base_r[0:8191],queue_l[0:8191],queue_r[0:8191];
always @(posedge clk1x) begin
    if(sound_out_valid_base) begin base_l[nb%8192]=$signed(sound_out_left_base);base_r[nb%8192]=$signed(sound_out_right_base);nb++;end
    if(sound_out_valid_queue) begin queue_l[nq%8192]=$signed(sound_out_left_queue);queue_r[nq%8192]=$signed(sound_out_right_queue);nq++;end
    if(sound_timeout_base || sound_timeout_queue) timeouts++;
end

// Decode each channel of a completed sample back to the CD tick it mixed.
function integer tick_of(input integer value,input integer right,input integer guess);
 tick_of=-1;
 for(integer d=0;d<40;d++) for(integer s=-1;s<=1;s+=2) begin
  integer t; t=guess+d*s;
  if(tick_of<0 && t>=0 && expected($signed(right ? cd_r(t):cd_l(t)))==value) tick_of=t;
 end
endfunction

// Skipped/repeated ticks plus torn pairs (left and right from different ticks).
integer torn=0;
function integer slips(input integer which,input integer first,input integer last);
 integer prev,t,t2,l,r;
 slips=0;prev=-1;torn=0;
 for(integer k=first;k<last;k++) begin
  l=which ? queue_l[k%8192]:base_l[k%8192];r=which ? queue_r[k%8192]:base_r[k%8192];
  t=tick_of(l,0,prev<0 ? k:prev+1);t2=tick_of(r,1,t<0 ? k:t);
  if(t<0 || t2<0) $fatal(1,"%s sample %0d (%0d/%0d) matches no CD tick",which ? "QUEUE":"BASE",k,l,r);
  if(t2!=t) torn++;
  if(prev>=0 && t!=prev+1) slips++;
  prev=t;
 end
 slips+=torn;
endfunction

integer mode;
initial begin
 if(!$value$plusargs("spikes=%d",mode)) mode=0;
 repeat(100) @(negedge clk1x);reset=0;SS_reset=0;
 repeat(100) @(negedge clk1x);
 write_reg(10'h180,16'h2000);write_reg(10'h182,16'h2000);
 write_reg(10'h1b0,16'h4000);write_reg(10'h1b2,16'h4000);write_reg(10'h1aa,16'hc001);
 // 24 looping ADPCM voices in SDRAM (same authored blocks in both memories);
 // silent (volume 0) in the late run so every sample decodes to its CD tick.
 for(integer v=0;v<24;v++) for(integer b=0;b<16;b++) begin
  automatic byte unsigned value=(b==0) ? 8'((((v%4)+1)<<4)|(v%13)) : (b==1) ? 8'h03 : 8'(v*37+b*19);
  mem_base.data[(v+100)*4096+b]=value;mem_queue.data[(v+100)*4096+b]=value;
 end
 for(integer v=0;v<24;v++) begin
  write_reg(v*16+0,mode ? 16'h0000 : 16'(16'h0800+v*13));write_reg(v*16+2,mode ? 16'h0000 : 16'(16'h0800+v*7));
  write_reg(v*16+4,16'(16'h0400+v*16'h0123));
  write_reg(v*16+6,16'((v+100)*512));write_reg(v*16+14,16'((v+100)*512));
  write_reg(v*16+8,16'h00ff);write_reg(v*16+10,16'h1fc0);
 end
 write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
 if(mode==0) begin
  for(integer n=0;n<3000;n++) begin
   @(posedge spu_tick_base);latency=4+(n*7)%6;
   if(n%97==0) begin @(negedge clk1x);ce=0;repeat(5) @(negedge clk1x);ce=1;end
  end
  repeat(800) @(negedge clk1x);
  if(nb!=nq) $fatal(1,"sample counts differ base=%0d queue=%0d",nb,nq);
  for(integer k=0;k<nb && k<8192;k++) if(base_l[k]!==queue_l[k] || base_r[k]!==queue_r[k]) mismatches++;
  if(mismatches!=0 || timeouts!=0) $fatal(1,"never-late streams differ: mismatches=%0d timeouts=%0d",mismatches,timeouts);
  $display("PASS SPU cd-queue on-time identical samples=%0d timeouts=0",nb);
 end else begin
  integer first,sb,sq;
  repeat(40) @(posedge spu_tick_base);
  first=nb;
  for(integer n=0;n<2400;n++) begin
   @(posedge spu_tick_base);
   // A spike every 120 ticks, 1..6 ticks long, of 40..70 clocks per access.
   if(n%120<1+(n/120)%6) begin latency=40+((n/120)*7)%31;if(n%120==0) spikes++;end
   else latency=9;
  end
  latency=9;repeat(60) @(posedge spu_tick_base);
  if(timeouts!=0) $fatal(1,"bench too harsh: %0d sound_timeouts",timeouts);
  if(nb!=nq) $fatal(1,"sample counts differ base=%0d queue=%0d",nb,nq);
  sb=slips(0,first,nb);sq=slips(1,first,nq);
  $display("SPU cd-queue late SPU: spikes=%0d samples=%0d max_stashed=%0d base_slips=%0d queue_slips=%0d timeouts=0",spikes,nb-first,maxstash,sb,sq);
  if(sb==0) $fatal(1,"bench never made the original SPU slip");
  if(sq!=0) $fatal(1,"queue SPU slipped");
  $display("PASS SPU cd-queue late SPU keeps CD order base_slips=%0d queue_slips=0",sb);
 end
 $finish;
end
initial begin repeat(4000000) @(negedge clk1x);$fatal(1,"bench timeout");end
endmodule
