"""Expose the existing connected fixture to a serializable C++ clock driver."""
import re
from pathlib import Path

from tool_paths import ROOT


def write_bench(directory):
    template = ROOT / 'sim/reference/tb_psx_system_fast.sv'
    original = template.read_text()
    clock_marker = 'always #14762550 clk1x=~clk1x;'
    memory_marker = 'mister_sdram_model #(.BOOT_FILE("boot.hex8")) main_memory('
    tail_marker = 'integer cycles=0,checks=0,max_cycles=2000000;'
    if any(original.count(marker) != 1 for marker in (clock_marker, memory_marker, tail_marker)):
        raise ValueError('Connected fixture clock/memory contract changed')
    text = original[:original.index(clock_marker)]
    text += original[original.index(memory_marker):original.index(tail_marker)]
    ports = {
        'clk1x': 'input logic', 'clk2x': 'input logic',
        'clk3x': 'input logic', 'clkvid': 'input logic', 'reset': 'input logic',
        'hasCD': 'input logic', 'LIDopen': 'input logic',
        'trackinfo_data': 'input logic [31:0]', 'trackinfo_addr': 'input logic [8:0]',
        'trackinfo_write': 'input logic', 'cd_hps_req': 'output logic',
        'cd_hps_lba': 'output logic [31:0]', 'cd_hps_ack': 'input logic',
        'cd_hps_write': 'input logic', 'cd_hps_data': 'input logic [15:0]',
        'sound_out_left': 'output logic [15:0]', 'sound_out_right': 'output logic [15:0]',
        'DisplayWidth': 'output logic [10:0]', 'DisplayHeight': 'output logic [9:0]',
        'DisplayOffsetX': 'output logic [9:0]', 'DisplayOffsetY': 'output logic [8:0]',
    }
    for name in ports:
        text, count = re.subn(r'^logic (?:\[[^\n]+?\]\s+)?' + name + r'=\w+;\n', '', text, flags=re.M)
        if count != 1:
            raise ValueError('Connected fixture port declaration changed: ' + name)
    for name in ('Triangle', 'Circle', 'Cross', 'Square', 'Select', 'Start',
                 'Right', 'Left', 'Up', 'Down', 'R1', 'R2', 'L1', 'L2'):
        key = 'joypad1_Key' + name
        text, count = re.subn(r'^logic ' + key + r'=0;\n', '', text, flags=re.M)
        if count != 1:
            raise ValueError('Connected fixture pad declaration changed: ' + key)
    added = [
        'input logic [15:0] pad_mask', 'input logic diagnostic',
        'input logic capture', 'input logic [63:0] capture_cycle',
        'output logic [63:0] cycles=0', 'output logic [31:0] checks=0',
        'output logic diagnostic_done=0', 'output logic reset_busy',
        'output logic [31:0] cpu_pc', 'output logic [31:0] cpu_opcode',
        'output logic display_disabled', 'output logic display_rgb24',
    ]
    header = 'module psx_checkpoint(\n ' + ',\n '.join(
        [kind + ' ' + name for name, kind in ports.items()] + added) + '\n);'
    text = text.replace('module tb_psx_fast;', header)
    text += '''
assign reset_busy=core.component_core.isavestates.savestate_busy;
assign cpu_pc=core.component_core.icpu.pc;
assign cpu_opcode=core.component_core.icpu.opcode0;
assign display_disabled=core.component_core.igpu.gpustat_displaydisable;
assign display_rgb24=core.component_core.igpu.gpustat_colordepth24;
wire joypad1_KeySelect=pad_mask[0],joypad1_KeyStart=pad_mask[3];
wire joypad1_KeyUp=pad_mask[4],joypad1_KeyRight=pad_mask[5];
wire joypad1_KeyDown=pad_mask[6],joypad1_KeyLeft=pad_mask[7];
wire joypad1_KeyL2=pad_mask[8],joypad1_KeyR2=pad_mask[9];
wire joypad1_KeyL1=pad_mask[10],joypad1_KeyR1=pad_mask[11];
wire joypad1_KeyTriangle=pad_mask[12],joypad1_KeyCircle=pad_mask[13];
wire joypad1_KeyCross=pad_mask[14],joypad1_KeySquare=pad_mask[15];
reg [31:0] expected[0:2647];
initial $readmemh("expected.hex",expected);
always @(posedge clk1x) begin
 cycles<=cycles+1;
 if(diagnostic && ram_ena && !ram_rnw && !reset_busy) begin
  if(ram_Adr>=4096 && ram_Adr<4096+2648*4) begin
   if(ram_Adr!=4096+checks*4 || ram_dataWrite!=expected[checks])
    $fatal(1,"CHECKPOINT diagnostic mismatch index=%d addr=%08x got=%08x expected=%08x",checks,ram_Adr,ram_dataWrite,expected[checks]);
   checks<=checks+1;
  end else if(ram_Adr==25'hff00) begin
   if(ram_dataWrite!=32'h600df00d || checks!=2648) $fatal(1,"CHECKPOINT diagnostic incomplete");
   diagnostic_done<=1;
  end
 end
end
// Files are opened, written and closed within one settled event. There are
// no live HDL file handles or coroutines in a saved model.
always @(negedge clk1x) if(capture) begin : snapshot
 integer file_handle;
 file_handle=$fopen($sformatf("vram-%012d.hex32",capture_cycle),"w");
 for(integer i=0;i<262144;i++) $fwrite(file_handle,"%08x\\n",video_memory.data[i]);
 $fclose(file_handle);
 file_handle=$fopen("main-ram.hex32","w");
 for(integer i=0;i<524288;i++)
  $fwrite(file_handle,"%02x%02x%02x%02x\\n",main_memory.data[i*4+3],main_memory.data[i*4+2],main_memory.data[i*4+1],main_memory.data[i*4]);
 $fclose(file_handle);
 file_handle=$fopen("spu-ram.hex32","w");
 for(integer i=0;i<131072;i++)
  $fwrite(file_handle,"%02x%02x%02x%02x\\n",spu_memory.data[i*4+3],spu_memory.data[i*4+2],spu_memory.data[i*4+1],spu_memory.data[i*4]);
 $fclose(file_handle);
 file_handle=$fopen($sformatf("snapshot-%012d.csv",capture_cycle),"w");
 $fwrite(file_handle,"cycles,width,height,x,y,rgb24,display_disabled,pc,sectors,audio_samples,audio_nonzero,pad,audio_start_sample\\n");
 // The C++ driver writes complete metadata after the model settles.
 $fclose(file_handle);
end
endmodule
'''
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'psx_checkpoint.sv'
    path.write_text(text)
    return path
