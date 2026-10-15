// SPDX-License-Identifier: GPL-2.0-only
// Two-disc simulation host for the pinned MiSTer raw-sector/track-info ports.
// Like psx_disc_host, but the bench chooses when to (re)load a disc: a load
// pulse writes the selected disc's 400 metadata words and switches the
// sector source to that disc's image.
module psx_disc_host_swap(input clk,input load,input disc_b,
    input sector_request,input [31:0] physical_lba,
    output reg sector_ack=0,sector_write=0,output reg [15:0] sector_data=0,
    output reg [8:0] track_addr=0,output reg [31:0] track_data=0,
    output reg track_write=0,output reg loaded=0,
    output integer sector_count=0);
    string image_a,image_b,meta_a,meta_b;
    integer file_a,file_b,current,sectors=0,seek_status,lo,hi,position;
    reg [31:0] metadata_a[0:399], metadata_b[0:399];
    reg [15:0] words[0:1175];
    reg [31:0] requested_lba;
    reg using_b=0;
    initial begin
        if(!$value$plusargs("disc_a=%s",image_a) || !$value$plusargs("disc_b=%s",image_b) ||
           !$value$plusargs("meta_a=%s",meta_a) || !$value$plusargs("meta_b=%s",meta_b) ||
           !$value$plusargs("disc_sectors=%d",sectors)) $fatal(1,"Swap host configuration missing");
        file_a=$fopen(image_a,"rb"); file_b=$fopen(image_b,"rb");
        if(!file_a || !file_b) $fatal(1,"Cannot open disc images");
        $readmemh(meta_a,metadata_a); $readmemh(meta_b,metadata_b);
    end
    // Metadata load on request.
    always begin
        @(posedge load);
        loaded=0; using_b=disc_b;
        for(integer i=0;i<400;i++) begin
            @(negedge clk);track_addr=i;track_data=disc_b ? metadata_b[i] : metadata_a[i];track_write=1;
            @(negedge clk);track_write=0;
        end
        @(negedge clk);loaded=1;
    end
    // Sector service from the current disc.
    always begin
        @(negedge clk);
        if(sector_request) begin
            requested_lba=physical_lba;
            current=using_b ? file_b : file_a;
            for(integer i=0;i<1176;i++) words[i]=requested_lba<150 ? 16'h0000:16'haaaa;
            if(requested_lba>=150 && requested_lba<150+sectors) begin
                position=(requested_lba-150)*2352;
                seek_status=$fseek(current,position,0);
                if(seek_status!=0) $fatal(1,"Disc seek failed");
                for(integer i=0;i<1176;i++) begin
                    lo=$fgetc(current);hi=$fgetc(current);
                    if(lo<0 || hi<0) $fatal(1,"Disc sector truncated");
                    words[i]={hi[7:0],lo[7:0]};
                end
            end
            sector_ack=1;
            repeat(2) @(negedge clk);
            for(integer i=0;i<1176;i++) begin
                sector_data=words[i];sector_write=1;
                @(negedge clk);
            end
            sector_write=0;
            repeat(2) @(negedge clk);sector_ack=0;sector_count++;
        end
    end
endmodule
