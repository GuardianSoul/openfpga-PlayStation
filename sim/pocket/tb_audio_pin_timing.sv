`timescale 1ns/1ps
module pin_i2s_observer #(parameter integer PHASE=0,parameter real SETUP=1,HOLD=1) (
    input wire mclk,reset,lrck,dac,
    output integer decoded=0
);
    integer phase=0,index=-1,pair_index=-1;
    reg old_lrck=0;
    reg [15:0] word_value=0;
    realtime lrck_changed=-1000,dac_changed=-1000,capture;
    always @(lrck) lrck_changed=$realtime;
    always @(dac) dac_changed=$realtime;
    always @(posedge mclk) begin
        if(reset) begin phase=0;index=-1;pair_index=-1;old_lrck=0;decoded=0;end
        else begin
            if(phase==PHASE) begin
                capture=$realtime;
                if(capture-lrck_changed<SETUP || capture-dac_changed<SETUP) $fatal(1,"Audio pin setup aperture");
                if(lrck!=old_lrck) begin
                    old_lrck=lrck;index=0;word_value=0;
                    if(lrck) pair_index=pair_index+1;
                end else if(index>=0) begin
                    index=index+1;
                    if(index<=16) word_value={word_value[14:0],dac};
                    if(index==16) begin
                        if(pair_index<0 || pair_index>=tb.loaded) $fatal(1,"Audio decoder source index");
                        if(word_value !== (lrck ? tb.expected[pair_index][31:16] : tb.expected[pair_index][15:0]))
                            $fatal(1,"Audio decoded pair/channel mismatch phase=%0d pair=%0d lrck=%0d got=%h",PHASE,pair_index,lrck,word_value);
                        decoded=decoded+1;
                    end
                end
                #(HOLD);
                if(lrck_changed>capture || dac_changed>capture) $fatal(1,"Audio pin hold aperture");
            end
            phase=(phase+1)%4;
        end
    end
endmodule

module tb;
    parameter real CLK_DELAY=6,LR_DELAY=5.4,DAC_DELAY=3;
    parameter integer CORRUPT=0;
    reg audio_clk=0;always #40.690104 audio_clk=~audio_clk;
    reg reset=1;
    reg [31:0] frame=0;
    wire audio_lrck,audio_dac,gold_lrck,gold_dac;
    wire chip_mclk,chip_lrck,chip_dac;
    assign #(CLK_DELAY) chip_mclk=audio_clk;
    assign #(LR_DELAY) chip_lrck=audio_lrck;
    assign #(DAC_DELAY) chip_dac=audio_dac ^ (CORRUPT!=0);
    psx_audio_i2s dut(.*);
    golden_audio_i2s golden (.audio_clk(audio_clk),.reset(reset),.frame(frame),.audio_lrck(gold_lrck),.audio_dac(gold_dac));
    reg [31:0] expected[0:511];integer loaded=0,checks=0,i,epoch;
    always @(posedge gold_lrck) if(!reset) begin expected[loaded]=frame;loaded=loaded+1;end
    always @(negedge audio_clk) begin
        #0.001;
        if(!reset) begin
            if({audio_lrck,audio_dac} !== {gold_lrck,gold_dac}) $fatal(1,"Raw audio stream altered");
            checks=checks+1;
        end
    end
    wire [31:0] decoded[0:3];
    genvar g;generate for(g=0;g<4;g=g+1) begin:observers
        pin_i2s_observer #(.PHASE(g)) receiver (.mclk(chip_mclk),.reset(reset),.lrck(chip_lrck),.dac(chip_dac),.decoded(decoded[g]));
    end endgenerate
    initial begin
        for(epoch=0;epoch<2;epoch=epoch+1) begin
            reset=1;repeat(9) @(negedge audio_clk);#7;loaded=0;reset=0;
            for(i=0;i<40000;i=i+1) begin
                @(negedge audio_clk);#2;
                if(i%256==0) begin
                    case((i/256)%8)
                        0:frame=0;1:frame=32'h80007fff;2:frame=32'h7fff8000;
                        3:frame=32'hffffffff;4:frame=32'h5555aaaa;
                        default:frame={16'((i/256)*37),16'(~((i/256)*19))};
                    endcase
                end
            end
            if(loaded<150) $fatal(1,"Audio source coverage");
            for(i=0;i<4;i=i+1) if(decoded[i]<300) $fatal(1,"Audio receiver coverage phase%0d",i);
        end
        $display("PASS AUDIO_PINS checks=%0d decoders=%0d/%0d/%0d/%0d reset_epochs=2",checks,decoded[0],decoded[1],decoded[2],decoded[3]);$finish;
    end
    initial begin #10000000;$fatal(1,"Audio pin timeout");end
endmodule
