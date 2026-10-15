`timescale 1ns/1ps
// Replays a recorded physical-LBA request list (disc-requests.csv from a
// connected FFVII run) through the Pocket loader (psx_disc_host_apf.sv:
// psx_cd_stream behind the pinned APF handler) and compares every halfword
// with the disc image read directly here. Private disc data stays local.
module tb_cd_replay;
    reg clk=0;always #14.763 clk=~clk;
    reg start=0,req=0;reg [31:0] lba=0;
    wire ack,write,mounted;wire [15:0] data;wire [8:0] taddr;wire [31:0] tdata;wire twrite;
    integer delivered;
    psx_disc_host host(.clk(clk),.start(start),.sector_request(req),.physical_lba(lba),
        .sector_ack(ack),.sector_write(write),.sector_data(data),.track_addr(taddr),.track_data(tdata),
        .track_write(twrite),.mounted(mounted),.sector_count(delivered));
    string image_path,list_path;
    integer image,list,sectors,count=0,checks=0,max_wait=0,total_wait=0,gap=0,n,lo,hi,expected;
    integer index,requested;
    initial begin
        if(!$value$plusargs("disc_file=%s",image_path) || !$value$plusargs("requests=%s",list_path) ||
           !$value$plusargs("disc_sectors=%d",sectors)) $fatal(1,"Replay configuration missing");
        void'($value$plusargs("gap=%d",gap));
        image=$fopen(image_path,"rb");list=$fopen(list_path,"r");
        if(!image || !list) $fatal(1,"Cannot open replay inputs");
        repeat(10) @(negedge clk);start=1;wait(mounted);
        while($fscanf(list,"%d,%d\n",index,requested)==2) begin
            integer waited;
            @(negedge clk);lba=requested;req=1;waited=0;
            do begin @(posedge clk);waited++;end while(!ack);
            @(negedge clk);req=0;
            if(waited>max_wait) max_wait=waited;total_wait+=waited;
            while(!write) @(posedge clk);
            if(requested>=150 && requested<150+sectors)
                if($fseek(image,(requested-150)*2352,0)!=0) $fatal(1,"Seek failed");
            for(integer h=0;h<1176;h++) begin
                if(requested<150) expected=0;
                else if(requested>=150+sectors) expected=16'haaaa;
                else begin lo=$fgetc(image);hi=$fgetc(image);expected={hi[7:0],lo[7:0]};end
                if(!write || data!==expected[15:0])
                    $fatal(1,"Replay mismatch request=%0d lba=%0d halfword=%0d got=%h expected=%h",index,requested,h,data,expected[15:0]);
                checks++;@(posedge clk);
            end
            while(ack) @(posedge clk);
            count++;
            repeat(gap) @(negedge clk);
        end
        $display("PASS CD REPLAY requests=%0d halfwords=%0d max_wait_clocks=%0d mean_wait_clocks=%0d",
            count,checks,max_wait,total_wait/(count==0 ? 1:count));
        $finish;
    end
endmodule
