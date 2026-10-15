"""Build the connected BIOS/disc fixture with local media capture."""
from pathlib import Path

if __package__:
    from .tool_paths import ROOT
else:
    from tool_paths import ROOT


def cd_response_trace(clk,read,address,data,cycles,trace):
    """Sample a registered cd_top read after its accepting positive edge."""
    return f'''reg cd_response_pending=0;
integer cd_response_cycle=0;
always @(posedge {clk}) begin
 cd_response_pending<={read} && {address}==1;
 cd_response_cycle<={cycles};
end
always @(negedge {clk}) if(cd_response_pending)
 $fwrite({trace},"%d,CDR_READY,1,%x\\n",cd_response_cycle,{data});
'''


def write_bench(directory,trace_devices=False):
    directory=Path(directory)
    text=(ROOT/'sim/reference/tb_psx_system_fast.sv').read_text()
    text=text.replace('integer trace;', '''integer trace,audio_file,audio_samples=0,audio_nonzero=0;
integer capture_interval=0;
string pad_path;
integer pad_trace,pad_fd,pad_parse,pad_temporary,pad_mask=0,pad_previous=-1;
initial begin
 void'($value$plusargs("capture_interval=%d",capture_interval));
 void'($value$plusargs("pad_file=%s",pad_path));
 pad_trace=$fopen("pad-events.csv","w");
end
always @(negedge clk1x) if(cycles%1000000==0 && pad_path!="") begin
 pad_fd=$fopen(pad_path,"r");
 if(!pad_fd) $fatal(1,"Simulation pad state file missing");
 pad_parse=$fscanf(pad_fd,"%h",pad_temporary);$fclose(pad_fd);
 if(pad_parse!=1 || pad_temporary<0 || pad_temporary>65535) $fatal(1,"Invalid simulation pad mask");
 pad_mask=pad_temporary;
 joypad1_KeySelect=pad_mask[0];joypad1_KeyStart=pad_mask[3];
 joypad1_KeyUp=pad_mask[4];joypad1_KeyRight=pad_mask[5];joypad1_KeyDown=pad_mask[6];joypad1_KeyLeft=pad_mask[7];
 joypad1_KeyL2=pad_mask[8];joypad1_KeyR2=pad_mask[9];joypad1_KeyL1=pad_mask[10];joypad1_KeyR1=pad_mask[11];
 joypad1_KeyTriangle=pad_mask[12];joypad1_KeyCircle=pad_mask[13];joypad1_KeyCross=pad_mask[14];joypad1_KeySquare=pad_mask[15];
 if(pad_mask!=pad_previous) begin $fwrite(pad_trace,"%d,%04x\\n",cycles,pad_mask);$fflush(pad_trace);pad_previous=pad_mask;end
end
task capture_snapshot(input integer stamp);
 integer capture_file;
 capture_file=$fopen($sformatf("vram-%012d.hex32",stamp),"w");
 for(integer i=0;i<262144;i++) $fwrite(capture_file,"%08x\\n",video_memory.data[i]);
 $fclose(capture_file);
 $fflush(audio_file);
 capture_file=$fopen($sformatf("snapshot-%012d.csv",stamp),"w");
 $fwrite(capture_file,"cycles,width,height,x,y,rgb24,display_disabled,pc,sectors,audio_samples,audio_nonzero,pad\\n");
 $fwrite(capture_file,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%08x,%0d,%0d,%0d,%04x\\n",stamp,
  DisplayWidth,DisplayHeight,DisplayOffsetX,DisplayOffsetY,core.component_core.igpu.gpustat_colordepth24,
  core.component_core.igpu.gpustat_displaydisable,core.component_core.icpu.pc,disc_sectors,audio_samples,audio_nonzero,pad_mask);
 $fclose(capture_file);
endtask
always @(negedge clk1x) if(capture_interval>0 && cycles>0)
 if(cycles%capture_interval==0) capture_snapshot(cycles);
final $fclose(pad_trace);
integer disc_sectors;
wire disc_mounted;
reg disc_start=0;
psx_disc_host disc_host(.clk(clk1x),.start(disc_start),.sector_request(cd_hps_req),.physical_lba(cd_hps_lba),
 .sector_ack(cd_hps_ack),.sector_write(cd_hps_write),.sector_data(cd_hps_data),
 .track_addr(trackinfo_addr),.track_data(trackinfo_data),.track_write(trackinfo_write),
 .mounted(disc_mounted),.sector_count(disc_sectors));
initial begin
 wait(core.component_core.isavestates.savestate_busy);
 wait(!core.component_core.isavestates.savestate_busy);
 repeat(32) @(negedge clk1x);disc_start=1;
end
always @* begin hasCD=disc_mounted;LIDopen=!disc_mounted;end
initial audio_file=$fopen("audio.pcm","wb");
always @(posedge clk1x) if(cycles%768==0) begin
 $fwrite(audio_file,"%c%c%c%c",sound_out_left[7:0],sound_out_left[15:8],sound_out_right[7:0],sound_out_right[15:8]);
 audio_samples++;
 if(sound_out_left!=0 || sound_out_right!=0) audio_nonzero++;
end
final begin $fclose(audio_file);$display("AUDIO samples=%d nonzero=%d",audio_samples,audio_nonzero);end''')
    marker='if(cycles%500000==0) $display("PROGRESS cycles=%d pc=%08x checks=%d",cycles,core.component_core.icpu.pc,checks);'
    if text.count(marker)!=1:raise ValueError('System progress marker changed')
    text=text.replace(marker,'''if(cycles%500000==0) begin
 $display("PROGRESS cycles=%d pc=%08x sectors=%d",cycles,core.component_core.icpu.pc,disc_sectors);$fflush();
end''')
    marker='  $display("BIOS BOUNDED STOP pc=%08x",core.component_core.icpu.pc);$finish;'
    if text.count(marker)!=1:raise ValueError('System bounded-stop marker changed')
    text=text.replace(marker,'''  begin : capture_vram
   integer dump;
   dump=$fopen("vram.hex32","w");
   for(integer i=0;i<262144;i++) $fwrite(dump,"%08x\\n",video_memory.data[i]);
   $fclose(dump);
   $display("MEDIA width=%d height=%d x=%d y=%d frame=%d",DisplayWidth,DisplayHeight,DisplayOffsetX,DisplayOffsetY,video_frameindex);
  end
  $display("BIOS/DISC BOUNDED STOP pc=%08x sectors=%d",core.component_core.icpu.pc,disc_sectors);$finish;''')
    if trace_devices:
        declarations='''integer device_trace;
reg previous_cd_irq=0,previous_dma_irq=0;
initial device_trace=$fopen("device-trace.csv","w");
always @(posedge clk1x) begin
 if(core.component_core.bus_cd_write)
  $fwrite(device_trace,"%d,CDW,%x,%x\\n",cycles,core.component_core.bus_cd_addr,core.component_core.bus_cd_datawrite);
 if(core.component_core.bus_dma_write)
  $fwrite(device_trace,"%d,DMAW,%x,%x\\n",cycles,core.component_core.bus_dma_addr,core.component_core.bus_dma_datawrite);
 if(core.component_core.bus_irq_write)
  $fwrite(device_trace,"%d,IRQW,%x,%x\\n",cycles,core.component_core.bus_irq_addr,core.component_core.bus_irq_datawrite);
 if(core.component_core.irq_cdrom && !previous_cd_irq)
  $fwrite(device_trace,"%d,CD_IRQ,0,1\\n",cycles);
 if(core.component_core.irq_dma && !previous_dma_irq)
  $fwrite(device_trace,"%d,DMA_IRQ,0,1\\n",cycles);
 previous_cd_irq=core.component_core.irq_cdrom;
 previous_dma_irq=core.component_core.irq_dma;
 if(cycles%500000==0) begin
  $display("DEVICES irq=%x mask=%x cdflag=%x cdstatus=%x dmastate=%d",core.component_core.iirq.i_status,
    core.component_core.iirq.i_mask,core.component_core.icd_top.cdrom_irqflag,
    core.component_core.icd_top.cdrom_status,core.component_core.idma.dmastate);
  $fflush(device_trace);
 end
end
final $fclose(device_trace);
'''
        declarations+=cd_response_trace('clk1x','core.component_core.bus_cd_read','core.component_core.bus_cd_addr',
            'core.component_core.bus_cd_dataread','cycles','device_trace')
        text=text.replace('integer trace,audio_file',declarations+'integer trace,audio_file',1)
        marker='   $fclose(dump);'
        text=text.replace(marker,marker+'''
   dump=$fopen("main-ram.hex32","w");
   for(integer i=0;i<524288;i++) $fwrite(dump,"%02x%02x%02x%02x\\n",
     main_memory.data[i*4+3],main_memory.data[i*4+2],main_memory.data[i*4+1],main_memory.data[i*4]);
   $fclose(dump);''',1)
    path=directory/'tb_system_disc.sv';path.write_text(text);return path
