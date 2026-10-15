`timescale 1ns/1ps
module tb_sdram_shared;
    parameter real GPU_HALF=7.381275;
    parameter integer OPS=240;
    reg clk=0,clk2=1,gpu_clk=0,spu_clk=0,reset=1,gpu_run=1;
    always #8 clk=~clk;
    always #4 clk2=~clk2;
    always #(GPU_HALF) if(gpu_run) gpu_clk=~gpu_clk;
    always #14.763 spu_clk=~spu_clk;
    wire initialized,client_reset,backend_idle,fault;
    reg [2:0] pv=0,pw=0,prr=0;
    wire [2:0] pr,prv,pre;
    reg [2:0][24:0] pa=0;
    reg [2:0][3:0] pn=0;
    reg [2:0][127:0] pd=0;
    reg [2:0][15:0] pb=0;
    wire [2:0][127:0] prd;
    reg spu_request=0,spu_rnw=1,spu_ce=1;
    reg [18:0] spu_addr=0;reg [31:0] spu_data=0;
    wire spu_done,spu_idle,spu_fault,sv,sw,srr;
    wire [31:0] spu_read;wire [24:0] sa;wire [3:0] sn;
    wire [127:0] sd;wire [15:0] sb;
    psx_spu_sdram_adapter spu (
        .clk1x(spu_clk),.mem_clk(clk),.reset_async(reset),.core_ce(spu_ce),.discard_returns(1'b0),
        .request(spu_request),.rnw(spu_rnw),.addr(spu_addr),.data(spu_data),.byte_en(4'b0011),
        .done(spu_done),.read_data(spu_read),.source_idle(spu_idle),.fault(spu_fault),
        .p_valid(sv),.p_write(sw),.p_ready(pr[1]),.p_addr(sa),.p_halfwords(sn),.p_wdata(sd),.p_byte_en(sb),
        .p_rsp_valid(prv[1]),.p_rsp_error(pre[1]),.p_rsp_rdata(prd[1]),.p_rsp_ready(srr));
    wire [2:0] packet_valid={pv[2],sv,pv[0]},packet_write={pw[2],sw,pw[0]},packet_return_ready={prr[2],srr,prr[0]};
    wire [2:0][24:0] packet_addr={pa[2],sa,pa[0]};
    wire [2:0][3:0] packet_count={pn[2],sn,pn[0]};
    wire [2:0][127:0] packet_data={pd[2],sd,pd[0]};
    wire [2:0][15:0] packet_mask={pb[2],sb,pb[0]};
    wire [3:0] valid,write,ready,rvalid;
    wire [3:0][23:0] address;
    wire [3:0][31:0] data;
    wire [3:0][3:0] mask;
    wire [31:0] rdata;
    for(genvar p=0;p<3;p++) begin: packet
        psx_mem_bridge #(.PIPELINE_READS(1)) front (
            .clk(clk),.rst(client_reset),.req_valid(packet_valid[p] && initialized),.req_ready(pr[p]),
            .req_write(packet_write[p]),.req_addr(packet_addr[p]),.req_halfwords(packet_count[p]),.req_wdata(packet_data[p]),.req_byte_en(packet_mask[p]),
            .rsp_valid(prv[p]),.rsp_ready(packet_return_ready[p]),.rsp_rdata(prd[p]),.rsp_error(pre[p]),
            .mem_valid(valid[p]),.mem_ready(ready[p]),.mem_write(write[p]),.mem_addr(address[p]),
            .mem_wdata(data[p]),.mem_byte_en(mask[p]),.mem_rvalid(rvalid[p]),.mem_rdata(rdata));
    end
    reg rd=0,we=0;reg [27:0] vaddr=0;reg [7:0] count=0,be=0;reg [63:0] din=0;
    wire busy,vfault,vready;wire [63:0] dout;wire [23:0] va;
    psx_vram_stream vram (
        .src_clk(gpu_clk),.mem_clk(clk),.reset_async(reset),.mem_initialized(initialized),
        .rd(rd),.we(we),.addr(vaddr),.count(count),.be(be),.din(din),
        .busy(busy),.fault(vfault),.ready(vready),.dout(dout),
        .mem_valid(valid[3]),.mem_ready(ready[3]),.mem_write(write[3]),.mem_addr(va),
        .mem_wdata(data[3]),.mem_byte_en(mask[3]),.mem_rvalid(rvalid[3]),.mem_rdata(rdata));
    assign address[3]=va+24'h200000;
    wire [12:0] a;wire [1:0] ba,dm;wire ras_n,cas_n,we_n,cke,dram_clk;wire [15:0] dq;
    psx_sdram_shared #(.T_INIT(16),.T_REFI(41)) dut (
        .clk_sys(clk),.clk_mem(clk2),.clk_dram(!clk2),.reset_async(reset),
        .initialized(initialized),.client_reset(client_reset),.backend_idle(backend_idle),.fault(fault),
        .valid(valid),.write(write),.ready(ready),.addr(address),.data(data),.byte_en(mask),
        .rvalid(rvalid),.rdata(rdata),.dram_a(a),.dram_ba(ba),.dram_dqm(dm),
        .dram_ras_n(ras_n),.dram_cas_n(cas_n),.dram_we_n(we_n),.dram_cke(cke),.dram_dq(dq),.dram_clk(dram_clk));
    sdr_pin_model chip (.clk(dram_clk),.cke(cke),.ras_n(ras_n),.cas_n(cas_n),.we_n(we_n),
        .address(a),.bank(ba),.dm(dm),.host_oe(dut.phy.oe_reg),.dq(dq));
    // Independent physical map and frontend byte oracle. One 8KiB slice per
    // logical client/window; the runner changes only chip-model storage.
    function automatic integer slot(input [24:0] halfword);
        integer region;
        case(halfword[24:12])
            13'h000:region=0;13'h800:region=1;13'h840:region=2;
            13'h400:region=3;13'h480:region=4;13'h500:region=5;
            default:begin region=0;$fatal(1,"Shared SDRAM escaped client window %h",halfword);end
        endcase
        return region*4096+int'(halfword[11:0]);
    endfunction
    function automatic [24:0] packet_base(input integer p);
        case(p) 0:return 0;1:return 25'h800000;default:return 25'h840000;endcase
    endfunction
    reg [7:0] gold[0:49151];
    reg [31:0] returns[0:3][0:2047];
    integer heads[0:3],tails[0:3],words[0:3],packets[0:2];
    integer cycles=0,comparisons=0,tagged_reads=0,max_active=0;
    always @(posedge clk) begin
        cycles++;
        if(cycles>300000) $fatal(1,"Shared SDRAM timeout");
        if(!client_reset) begin
            if(fault || vfault || spu_fault) $fatal(1,"Shared SDRAM protocol fault");
            if(backend_idle && (dut.phy.oe_reg || dut.phy.r_wrdata_en || dut.phy.wr_en_d ||
                dut.phy.rden_sr!=0 || dut.return_valid)) $fatal(1,"Physical idle asserted before drain");
            for(integer p=0;p<4;p++) begin
                if(valid[p] && ready[p]) begin
                    integer index;reg [31:0] expected;
                    index=slot({address[p],1'b0})*2;words[p]++;
                    if(!write[p]) begin
                        expected=0;for(integer b=0;b<4;b++) expected[b*8+:8]=gold[index+b];
                        returns[p][tails[p]&2047]=expected;tails[p]++;
                    end
                end
                if(rvalid[p]) begin
                    if(heads[p]>=tails[p] || rdata!==returns[p][heads[p]&2047])
                        $fatal(1,"Read tag/order mismatch port=%0d got=%h",p,rdata);
                    heads[p]++;tagged_reads++;
                end
            end
            if($countones(valid)>max_active) max_active=$countones(valid);
        end
    end
    task automatic packet_worker(input integer p);
        reg [31:0] rng;reg [127:0] payload,want,held;
        reg [15:0] enable;reg [24:0] addr_in;integer n,index;
        rng=32'h2a1597be+p;
        for(integer op=0;op<OPS;op++) begin
            rng=(rng>>1)^((rng&1) ? 32'hd0000001:0);
            n=op%8+1;addr_in=packet_base(p)+25'((op*37+p*11)%2036);
            enable=16'(rng);payload={rng,~rng,rng^32'habcd4567,rng+32'h1212};
            @(negedge clk);pv[p]=1;pw[p]=!(op&1);pa[p]=addr_in;pn[p]=4'(n);pd[p]=payload;pb[p]=enable;
            @(posedge clk);while(!pr[p]) @(posedge clk);
            index=slot(addr_in)*2;want=0;
            if(!(op&1)) begin
                for(integer b=0;b<n*2;b++) if(enable[b]) gold[index+b]=payload[b*8+:8];
            end else for(integer b=0;b<n*2;b++) want[b*8+:8]=gold[index+b];
            @(negedge clk);pv[p]=0;pa[p]='1;pd[p]='1;pb[p]=0;
            wait(prv[p]);held=prd[p];
            repeat(op%7) begin @(negedge clk);if(!prv[p] || prd[p]!==held) $fatal(1,"Packet response not held");end
            if(pre[p] || prd[p]!==want) $fatal(1,"Packet bytes/mask mismatch port=%0d op=%0d got=%h want=%h",p,op,prd[p],want);
            @(negedge clk);prr[p]=1;@(negedge clk);prr[p]=0;packets[p]++;comparisons+=n*2;
        end
    endtask
    task automatic spu_worker;
        integer index;reg [15:0] want;
        for(integer op=0;op<OPS;op++) begin
            @(negedge spu_clk);spu_request=1;spu_rnw=op&1;
            spu_addr=19'(((op/2*53)%2036)*2);spu_data=32'h818abcd0^32'(op/2*971);
            index=slot(25'h800000+25'(spu_addr/2))*2;
            want=0;
            if(!(op&1)) begin gold[index]=spu_data[7:0];gold[index+1]=spu_data[15:8];end
            else want={gold[index+1],gold[index]};
            @(posedge spu_clk);@(negedge spu_clk);spu_request=0;spu_addr='1;spu_data='1;
            if(op%23==3) begin
                spu_ce=0;repeat(70) begin @(negedge spu_clk);if(spu_done) $fatal(1,"SPU return during CE pause");end
                if(spu_idle) $fatal(1,"SPU pending return lost during CE pause");spu_ce=1;
            end
            wait(spu_done);
            if(spu_read[15:0]!==want) $fatal(1,"SPU halfword response mismatch %0d",op);
            @(negedge spu_clk);packets[1]++;comparisons+=2;
        end
    endtask
    reg vactive=0;integer vbeats=0,vseen=0;reg [27:0] read_address;
    always @(posedge gpu_clk) if(vready) begin
        integer index;
        if(!vactive || vseen>=vbeats) $fatal(1,"Unexpected VRAM stream response");
        index=slot(25'h400000+25'((read_address+vseen*8)/2))*2;
        for(integer b=0;b<8;b++) if(dout[b*8+:8]!==gold[index+b])
            $fatal(1,"VRAM stream bytes/order mismatch beat=%0d",vseen);
        vseen++;comparisons+=8;
    end
    task automatic vcommand(input [27:0] address_in,input integer beats,input bit writing,input [7:0] enables,input [63:0] payload,input bit pause_source=0);
        @(negedge gpu_clk);while(busy) @(negedge gpu_clk);
        vaddr=address_in;count=8'(beats);we=writing;rd=!writing;be=enables;din=payload;
        if(!writing) begin vactive=1;vbeats=beats;vseen=0;read_address=address_in;end
        @(posedge gpu_clk);while(busy) @(posedge gpu_clk);
        if(writing) for(integer n=0;n<beats;n++) begin
            integer index;index=slot(25'h400000+25'((address_in+n*8)/2))*2;
            for(integer b=0;b<8;b++) if(enables[b]) gold[index+b]=payload[b*8+:8];
        end
        @(negedge gpu_clk);we=0;rd=0;vaddr='1;din='1;
        if(pause_source) begin
            repeat(8) @(negedge gpu_clk);gpu_run=0;
            repeat(200) @(negedge clk);gpu_run=1;
        end
        if(!writing) begin wait(vseen==beats);@(negedge gpu_clk);vactive=0;end
    endtask
    task automatic vworker;
        reg [27:0] base;integer beats;
        for(integer op=0;op<24;op++) begin
            case(op%3) 0:base=0;1:base=28'h100000;default:base=28'h200000;endcase
            case(op%4) 0:beats=1;1:beats=2;2:beats=127;default:beats=255;endcase
            vcommand(base+28'h3f8,beats,1,8'(op*19+3),{32'h4b991621^32'(op),32'h08a634d1+32'(op)});
            vcommand(base+28'h3f8,beats,0,0,0,op==3);
        end
    endtask
    initial begin
        integer before_final_write;
        for(integer i=0;i<49152;i++) gold[i]=0;
        for(integer p=0;p<4;p++) begin heads[p]=0;tails[p]=0;words[p]=0;end
        for(integer p=0;p<3;p++) packets[p]=0;
        repeat(10) @(negedge clk);reset=0;wait(initialized && !client_reset);
        fork packet_worker(0);spu_worker();packet_worker(2);vworker();join
        // End with a posted write and no later read to hide its pin-drain
        // window. Command acceptance is not physical memory completion.
        before_final_write=words[3];
        vcommand(0,1,1,8'hff,64'h99123478abcdef21);
        wait(words[3]==before_final_write+2);
        wait(backend_idle);repeat(6) @(negedge clk);
        if(max_active!=4 || chip.refreshes<5) $fatal(1,"Missing four-client/refresh contention");
        for(integer p=0;p<4;p++) if(heads[p]!=tails[p] || words[p]==0) $fatal(1,"Undrained client");
        for(integer i=0;i<24576;i++) begin
            if(chip.memory[i]!=={gold[2*i+1],gold[2*i]}) $fatal(1,"Physical committed bytes mismatch %0d",i);
        end
        $display("PASS SHARED SDRAM packets=%0d words=%0d reads=%0d bytes=%0d cycles=%0d refreshes=%0d max_clients=%0d",
            packets[0]+packets[1]+packets[2],words[0]+words[1]+words[2]+words[3],tagged_reads,comparisons,cycles,chip.refreshes,max_active);$finish;
    end
endmodule
