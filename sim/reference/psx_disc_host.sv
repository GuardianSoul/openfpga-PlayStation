// SPDX-License-Identifier: GPL-2.0-only
// Original simulation host for the pinned MiSTer raw-sector/track-info ports.
// No mechanical timing, CD decoding or Pocket filesystem is implemented here.
module psx_disc_host(input clk,input start,
    input sector_request,input [31:0] physical_lba,
    output reg sector_ack=0,sector_write=0,output reg [15:0] sector_data=0,
    output reg [8:0] track_addr=0,output reg [31:0] track_data=0,
    output reg track_write=0,output reg mounted=0,
    output integer sector_count=0);
    string image_path,metadata_path;
    integer image_file,metadata_file,index_sector=0,sectors=0;
    integer seek_status,lo,hi,position,cycle_delay=0;
    reg [31:0] metadata[0:399];
    reg [15:0] words[0:1175];
    reg [31:0] requested_lba;
    integer trace;
    initial begin
        if(!$value$plusargs("disc_file=%s",image_path) ||
           !$value$plusargs("disc_metadata=%s",metadata_path) ||
           !$value$plusargs("disc_sectors=%d",sectors)) $fatal(1,"Disc host configuration missing");
        void'($value$plusargs("disc_index=%d",index_sector));
        void'($value$plusargs("disc_delay=%d",cycle_delay));
        if(index_sector<0 || sectors<=0 || cycle_delay<0) $fatal(1,"Invalid disc host bounds");
        image_file=$fopen(image_path,"rb");
        if(!image_file) $fatal(1,"Cannot open private disc image");
        $readmemh(metadata_path,metadata);
        trace=$fopen("disc-requests.csv","w");
        wait(start);
        // Four-word records commit on their reserved fourth word. Preserve
        // header and every track record, including the zeroed unused records.
        for(integer i=0;i<400;i++) begin
            @(negedge clk);track_addr=i;track_data=metadata[i];track_write=1;
            @(negedge clk);track_write=0;
        end
        @(negedge clk);mounted=1;
        forever begin
            @(negedge clk);
            if(sector_request) begin
                requested_lba=physical_lba;
                $fwrite(trace,"%d,%d\n",sector_count,requested_lba);$fflush(trace);
                for(integer i=0;i<1176;i++) words[i]=requested_lba<150 ? 16'h0000:16'haaaa;
                if(requested_lba>=150 && requested_lba<150+sectors) begin
                    position=(index_sector+requested_lba-150)*2352;
                    seek_status=$fseek(image_file,position,0);
                    if(seek_status!=0) $fatal(1,"Disc seek failed");
                    for(integer i=0;i<1176;i++) begin
                        lo=$fgetc(image_file);hi=$fgetc(image_file);
                        if(lo<0 || hi<0) $fatal(1,"Private disc sector truncated");
                        words[i]={hi[7:0],lo[7:0]};
                    end
                end
                repeat(cycle_delay) @(negedge clk);
                sector_ack=1;
                repeat(2) @(negedge clk);
                for(integer i=0;i<1176;i++) begin
                    sector_data=words[i];sector_write=1;
                    @(negedge clk);
                end
                sector_write=0;
                repeat(2) @(negedge clk);sector_ack=0;sector_count++;
                if(sector_request) $fatal(1,"New sector request overlaps host completion");
            end
        end
    end
    final begin $fclose(image_file);$fclose(trace);end
endmodule
