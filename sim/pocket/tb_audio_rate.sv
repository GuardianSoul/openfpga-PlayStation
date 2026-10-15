`timescale 1ns/1ps
module tb_audio_rate;
    reg clk=0; always #5 clk=~clk;
    reg reset=1, advance=0;
    reg [6:0] level=32;
    wire [7:0] phase_coarse;
    wire [15:0] phase_fine;
    wire need_input;
    wire signed [15:0] correction;
    psx_audio_rate dut(.*);
    integer i, p, ppm, fill, minimum, maximum, consumed;
    longint arrival_fraction, arrivals, step, expected_control, withheld;
    task restart;
        begin
            reset=1; advance=0; level=32;
            repeat(3) @(negedge clk); reset=0;
        end
    endtask
    initial begin
        restart();
        for(i=0;i<160000;i=i+1) begin
            if(phase_coarse !== (i*147)%160 || phase_fine !== 0 || correction !== 0)
                $fatal(1,"Nominal rational phase at %0d",i);
            if(need_input !== (i==0 || (i*147)/160 != ((i-1)*147)/160))
                $fatal(1,"Nominal input carry at %0d",i);
            advance=1; @(negedge clk);
        end
        for(p=0;p<5;p=p+1) begin
            case(p) 0:ppm=-500; 1:ppm=-300; 2:ppm=0; 3:ppm=300; default:ppm=500; endcase
            restart(); fill=32; minimum=32; maximum=32; consumed=0; arrival_fraction=0;withheld=0;
            step=147; step=step*(1000000+ppm);
            for(i=0;i<1440000;i=i+1) begin
                // One model clock is one sink frame (30 seconds/case).
                // Model the source independently using its exact frequency.
                arrival_fraction=arrival_fraction+step;
                arrivals=arrival_fraction/160000000;
                arrival_fraction=arrival_fraction%160000000;
                // A bounded memory delay withholds <=15 source completions,
                // then publishes them as a worst-case instantaneous burst.
                if(i%24000>=1000 && i%24000<1016) begin withheld=withheld+arrivals;arrivals=0;end
                if(i%24000==1016) begin
                    if(withheld>15) $fatal(1,"Jitter fixture exceeds SPU backlog bound");
                    arrivals=arrivals+withheld;withheld=0;
                end
                fill=fill+arrivals-need_input;
                consumed=consumed+need_input;
                if(fill<1 || fill>62) $fatal(1,"Drift queue failed ppm %0d frame %0d level %0d",ppm,i,fill);
                if(fill<minimum) minimum=fill;
                if(fill>maximum) maximum=fill;
                level=fill; advance=1; @(negedge clk);
                if(phase_coarse>=160 || correction>4933 || correction < -4933)
                    $fatal(1,"Rate state out of range");
            end
            expected_control=9633792; expected_control=expected_control*ppm/1000000;
            if(correction-expected_control>1024 || correction-expected_control < -1024)
                $fatal(1,"Drift control failed ppm %0d got %0d expected near %0d",ppm,correction,expected_control);
            $display("RATE ppm %0d range %0d..%0d final %0d correction %0d consumed %0d",ppm,minimum,maximum,fill,correction,consumed);
            // Pausing holds phase and control; no fictitious source carry.
            advance=0; arrivals={phase_coarse,phase_fine}; expected_control=correction;
            repeat(300) @(negedge clk);
            if(arrivals!={phase_coarse,phase_fine} || expected_control!=correction) $fatal(1,"Paused rate changed");
        end
        $display("PASS audio rate: exact nominal ratio, 150 seconds drift + bounded15-sample jitter, pause"); $finish;
    end
    initial begin #1000000000; $fatal(1,"Rate timeout"); end
endmodule
