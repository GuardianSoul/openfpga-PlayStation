`timescale 1ns/1ps
module tb_audio_i2s;
    reg audio_clk=0; always #5 audio_clk=~audio_clk;
    reg reset=1;
    reg [31:0] frame=0;
    wire audio_lrck,audio_dac,ref_lrck,ref_dac;
    reg [1:0] divider=0;
    wire ref_sclk=divider[1];
    psx_audio_i2s dut(.*);
    sound_i2s #(.SIGNED_INPUT(1)) reference (.audio_sclk(ref_sclk),
        .audio_l(frame[15:0]),.audio_r(frame[31:16]),.audio_lrck(ref_lrck),.audio_dac(ref_dac));
    integer i,checks=0,decoded=0,bit_index=-1;
    reg old_lrck=0,check_bit=0;
    reg [15:0] word=0;
    reg [31:0] loaded=0;
    always @(posedge audio_clk) begin
        check_bit=0;
        if(reset) begin
            divider<=0;
            // Upstream has no initializer/reset; establish the same known
            // starting state for this bounded serialization comparison.
            reference.audio_lrck_cnt=0;reference.audgen_sampshift=0;
            reference.audio_lrck=0;reference.audio_dac=0;
            old_lrck=0;bit_index=-1;loaded=0;
        end else begin
            check_bit=divider==1;
            divider<=divider+2'd1;
            if(check_bit && dut.bit_count==31 && !audio_lrck) loaded=frame;
        end
    end
    always @(negedge audio_clk) if(!reset) begin
        if({audio_lrck,audio_dac} !== {ref_lrck,ref_dac}) $fatal(1,"I2S upstream cycle mismatch");
        checks=checks+1;
        if(check_bit) begin
            if(audio_lrck!=old_lrck) begin old_lrck=audio_lrck;bit_index=0;word=0; end
            else if(bit_index>=0) begin
                bit_index=bit_index+1;
                if(bit_index<=16) word={word[14:0],audio_dac};
                if(bit_index==16) begin
                    if(word !== (audio_lrck ? loaded[31:16] : loaded[15:0]))
                        $fatal(1,"I2S decoded channel %0d got %h pair %h",audio_lrck,word,loaded);
                    decoded=decoded+1;
                end
            end
        end
    end
    initial begin
        repeat(3) @(negedge audio_clk); reset=0;
        for(i=0;i<40000;i=i+1) begin
            @(negedge audio_clk);
            if(i%256==0) frame={16'((i/256)*37),16'(~((i/256)*19))};
            if(i==20000) begin reset=1;repeat(3) @(negedge audio_clk);reset=0; end
        end
        if(decoded<300) $fatal(1,"I2S decode coverage");
        $display("PASS audio I2S: %0d upstream cycle checks, %0d decoded channels",checks,decoded);$finish;
    end
endmodule
