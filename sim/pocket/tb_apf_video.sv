`timescale 1ns/1ps
// APF interface requirements, including nonzero PSX RGB during blanking and
// long input sync levels. This executes the actual full-top source fragment.
module tb_apf_video;
    reg video_rgb_clock=0;
    always #9.312 video_rgb_clock=~video_rgb_clock;
    reg hsync=0,vsync=0,hblank=1,psx_vblank=1,video_ce=0;
    reg [7:0] video_r=0,video_g=0,video_b=0;
    wire video_skip,video_de,video_hs,video_vs;
    wire [23:0] video_rgb;
    reg previous_hs=0,previous_vs=0;
    integer checked=0,hs_pulses=0,vs_pulses=0,active_pixels=0;
    apf_video_fixture dut(.*);
    task automatic check;
        @(posedge video_rgb_clock);#1;
        if(video_hs!==(hsync && !previous_hs) || video_vs!==(vsync && !previous_vs))
            $fatal(1,"APF sync must pulse once per input rising edge");
        if(video_de!==(!hblank && !psx_vblank)) $fatal(1,"APF active area/data misaligned");
        if(video_skip!==(video_de && !video_ce)) $fatal(1,"APF skip must stay inside active area");
        if(!video_de && video_rgb!==24'b0) $fatal(1,"APF RGB must be black outside active area");
        if(video_de && video_rgb!=={video_r,video_g,video_b}) $fatal(1,"APF pixel data misaligned");
        previous_hs=hsync;previous_vs=vsync;checked++;
        hs_pulses+=video_hs;vs_pulses+=video_vs;active_pixels+=video_de && !video_skip;
    endtask
    initial begin
        check();
        // Multiple input pixel divisors and changing line/field widths.
        for(integer mode=0;mode<4;mode++) begin
            for(integer n=0;n<2048;n++) begin
                integer column,line;
                @(negedge video_rgb_clock);
                column=n%(32+mode*8);line=n/(32+mode*8);
                hsync=column>=24+mode*8;vsync=line%16>=13;
                hblank=column>=20+mode*8;psx_vblank=line%16>=12;
                video_ce=n%(mode+1)==0;
                // Deliberately retain nonzero RGB throughout inactive clocks.
                video_r=8'(n*13+3);video_g=8'(n*17+5);video_b=8'(n*19+7);
                check();
            end
        end
        if(hs_pulses<150 || vs_pulses<10 || active_pixels<1000)
            $fatal(1,"APF protocol coverage insufficient");
        $display("PASS APF VIDEO clocks=%0d HS=%0d VS=%0d pixels=%0d",checked,hs_pulses,vs_pulses,active_pixels);
        $finish;
    end
endmodule
