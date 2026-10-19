#!/usr/bin/env python3
"""Time a recorded game GPU stream on the pinned MiSTer GPU, per frame.

Same trace format as run_gpu_replay.py (tools/ffvii-census, PSX_GPU_TRACE;
record with PSX_GPU_EVERY=1 so every frame end is a marker). GP0 words are
fed as fast as the GPU's input FIFO takes them, so the clk2x cycles between
two frame-end markers are the time the GPU needs to draw that frame. A 480i
game with "draw to display area" off must finish a frame inside one field
(about 1.13 million clk2x cycles at NTSC rate, docs/tekken3-graphics.md).

--vram ddr     MiSTer's DDR3 model (mister_ddr_model, no wait states).
--vram psram   the Pocket release VRAM: rtl/memory/psx_vram_psram.sv on two
               cycle-level chip models (sim/reference/psx_cram_cycle_model.sv).
--vram-source  another psx_vram_psram.sv (same ports) to time a candidate.

Every --dump-th marker the bench also writes VRAM, compared with Beetle's
snapshot like run_gpu_replay.py does, so a timing run is also checked for
pixel equality. Prints one line per frame and a summary; results go to
build/sim/gpu-timing/<name>-<vram>/.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from prepare_system_verilator import prepare
from run_gpu_replay import convert, compare
from tool_paths import ROOT, find_tool

FIELD_CLK2X = 67737600 * 1001 // 60000   # one NTSC field (59.94 Hz) in clk2x cycles

MEMORY = '''mister_ddr_model vram(.clk(clk2x),.busy(vram_busy),.burstcnt(vram_burstcnt),
 .addr({4'b0011,vram_addr[27:3]}),.dout(vram_dout),.ready(vram_dout_ready),.rd(vram_rd),
 .din(vram_din),.be(vram_be),.we(vram_we));'''

PSRAM = '''`define CHIP_STATS
wire [21:16] c0_a,c1_a; wire [15:0] c0_do,c1_do,c0_di,c1_di; wire c0_oe,c1_oe,c0_clk,c1_clk;
wire c0_adv,c1_adv,c0_cre,c1_cre,c0_ce0,c1_ce0,c0_ce1,c1_ce1,c0_oen,c1_oen,c0_wen,c1_wen,c0_ub,c1_ub,c0_lb,c1_lb;
wire vp_ready;
psx_vram_psram VP_PARAMS vp(.clk(clk2x),.reset(reset),.park(1'b0),
 .ddr_busy(vram_busy),.ddr_burstcnt(vram_burstcnt),.ddr_addr(vram_addr[27:3]),.ddr_dout(vram_dout),
 .ddr_dout_ready(vram_dout_ready),.ddr_rd(vram_rd),.ddr_din(vram_din),.ddr_be(vram_be),.ddr_we(vram_we),
 .aux_req(1'b0),.aux_we(1'b0),.aux_addr(21'b0),.aux_din(32'b0),.aux_dout(),.aux_done(),.card_write(),
 .cram0_a(c0_a),.cram0_dq_out(c0_do),.cram0_dq_oe(c0_oe),.cram0_dq_in(c0_di),.cram0_clk(c0_clk),
 .cram0_adv_n(c0_adv),.cram0_cre(c0_cre),.cram0_ce0_n(c0_ce0),.cram0_ce1_n(c0_ce1),
 .cram0_oe_n(c0_oen),.cram0_we_n(c0_wen),.cram0_ub_n(c0_ub),.cram0_lb_n(c0_lb),
 .cram1_a(c1_a),.cram1_dq_out(c1_do),.cram1_dq_oe(c1_oe),.cram1_dq_in(c1_di),.cram1_clk(c1_clk),
 .cram1_adv_n(c1_adv),.cram1_cre(c1_cre),.cram1_ce0_n(c1_ce0),.cram1_ce1_n(c1_ce1),
 .cram1_oe_n(c1_oen),.cram1_we_n(c1_wen),.cram1_ub_n(c1_ub),.cram1_lb_n(c1_lb));
psx_cram_cycle_model #(.LC(CHIP_LC)) chip0(.clk(CHIP_CLK0),.en(CHIP_EN0),.a(c0_a),.dq_out(c0_do),.dq_in(c0_di),.ce0_n(c0_ce0),.ce1_n(c0_ce1),
 .adv_n(c0_adv),.we_n(c0_wen),.ub_n(c0_ub),.lb_n(c0_lb),.cre(c0_cre));
psx_cram_cycle_model #(.LC(CHIP_LC)) chip1(.clk(CHIP_CLK1),.en(CHIP_EN1),.a(c1_a),.dq_out(c1_do),.dq_in(c1_di),.ce0_n(c1_ce0),.ce1_n(c1_ce1),
 .adv_n(c1_adv),.we_n(c1_wen),.ub_n(c1_ub),.lb_n(c1_lb),.cre(c1_cre));
// VRAM halfword h is lane h[1:0] of DDR word h>>2: lanes 0/1 are beat 0 of
// cram0/cram1, lanes 2/3 beat 1 (chip halfword {word, beat}, die 0).
function automatic [15:0] vram_get(input integer h);
 vram_get = h[0] ? chip1.mem[((h>>2)<<1)|h[1]] : chip0.mem[((h>>2)<<1)|h[1]];
endfunction
task automatic vram_set(input integer h, input [15:0] v);
 if (h[0]) chip1.mem[((h>>2)<<1)|h[1]] = v; else chip0.mem[((h>>2)<<1)|h[1]] = v;
endtask'''

DDR_ACCESS = '''function automatic [15:0] vram_get(input integer h);
 vram_get = h[0] ? vram.data[h>>1][31:16] : vram.data[h>>1][15:0];
endfunction
task automatic vram_set(input integer h, input [15:0] v);
 if (h[0]) vram.data[h>>1][31:16] = v; else vram.data[h>>1][15:0] = v;
endtask'''

MAIN = r'''integer stream,rc,count=0,snaps=0,stuck=0,dump_every=0;
string stream_name,out_dir;
reg [31:0] value;
reg [7:0] kind;
longint unsigned cyc2=0,busy2=0,vbusy2=0,vwrites=0,vreads=0,words=0,rwait=0,wwait=0,rdata=0;
int unsigned rleft=0;
// write pattern: full words, next word after the previous write, same word
// again, and a hit among the last 16 distinct written words (combining reach)
longint unsigned wfull=0,wseq=0,wsame=0,wrecent=0,wpix=0;
reg [24:0] wlast=0; reg [24:0] wtags[0:15]; reg [3:0] wptr=0;
initial for(int i=0;i<16;i++) wtags[i]='1;
always @(posedge clk2x) if(vram_we && !vram_busy && vram_addr[27:20]==0) begin : wstats
 reg hit; hit=0;
 for(int i=0;i<16;i++) if(wtags[i]==vram_addr[27:3]) hit=1;
 if(vram_be==8'hFF) wfull<=wfull+1;
 wpix<=wpix+(vram_be[0]+vram_be[2]+vram_be[4]+vram_be[6]);
 if(vram_addr[27:3]==wlast+1) wseq<=wseq+1;
 if(vram_addr[27:3]==wlast) wsame<=wsame+1;
 if(hit) wrecent<=wrecent+1; else begin wtags[wptr]<=vram_addr[27:3]; wptr<=wptr+1; end
 wlast<=vram_addr[27:3];
end
always @(posedge clk2x) begin
 cyc2<=cyc2+1;
 if(!ss_idle) busy2<=busy2+1;
 if(vram_busy && (vram_rd || vram_we)) vbusy2<=vbusy2+1;
 if(vram_we && !vram_busy) vwrites<=vwrites+1;
 if(vram_rd && !vram_busy) vreads<=vreads+1;
 if(vram_rd && vram_busy) rwait<=rwait+1;
 if(vram_we && vram_busy) wwait<=wwait+1;
 // clk2x cycles from a read's acceptance to its last word
 if(vram_rd && !vram_busy) rleft<=rleft+vram_burstcnt-(vram_dout_ready?1:0);
 else if(vram_dout_ready) rleft<=rleft-1;
 if(rleft!=0 || (vram_rd && !vram_busy)) rdata<=rdata+1;
end
task write_gpu(input [3:0] address,input [31:0] data_value);
 @(negedge clk1x);while(bus_stall || (address==0 && gpu.isyncfifo_in.fifocnt>=8'd192)) @(negedge clk1x);
 bus_addr=address;bus_datawrite=data_value;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask
// GP0 words back to back, one per clk1x while the input FIFO has room
task stream_gp0(input [31:0] data_value);
 @(negedge clk1x);
 while(bus_stall || gpu.isyncfifo_in.fifocnt>=8'd192) begin bus_write=0;@(negedge clk1x);end
 bus_addr=0;bus_datawrite=data_value;bus_write=1;
endtask
task end_stream;
 if(bus_write) begin @(negedge clk1x);bus_write=0;end
endtask
task read_gpu(output [31:0] data_value);
 @(negedge clk1x);while(bus_stall) @(negedge clk1x);
 bus_addr=0;bus_read=1;
 @(negedge clk1x);data_value=bus_dataread;bus_read=0;
endtask
task idle;
 integer n;
 n=0;
 repeat(4) @(negedge clk1x);
 while(!ss_idle && n<=4000000) begin @(negedge clk1x);n++;end
 if(!ss_idle) begin
  $display("idle timeout: proc_idle=%b fifoin_empty=%b fifoout_empty=%b vramstate=%0d pipeline_busy=%b word=%0d",
           gpu.proc_idle,gpu.fifoin_empty,gpu.fifoout_empty,gpu.vramstate,gpu.pipeline_busy,count);
  stuck++;
 end
endtask
reg [31:0] snapshot[0:262143];
reg [31:0] readback;
integer reads=0,read_mismatches=0;
longint unsigned t0,b0,v0,w0,r0,n0,rw0,ww0,rd0,f0,q0,s0,h0,p0,ws0=0,rs0=0,be0=0,ed0=0;
initial begin
 if(!$value$plusargs("stream=%s",stream_name) || !$value$plusargs("out=%s",out_dir)) $fatal(1,"+stream and +out required");
 void'($value$plusargs("dump=%d",dump_every));
 $readmemh({out_dir,"/vram_initial.hex"},snapshot);
 for(integer i=0;i<262144;i++) begin vram_set(2*i,snapshot[i][15:0]);vram_set(2*i+1,snapshot[i][31:16]);end
 repeat(100) @(negedge clk1x);reset=0;ss_reset=0;repeat(100) @(negedge clk1x);
 while(vram_busy) @(negedge clk1x);
 repeat(100) @(negedge clk1x);
 stream=$fopen(stream_name,"r");
 if(!stream) $fatal(1,"cannot open stream");
 t0=cyc2;b0=busy2;v0=vbusy2;w0=vwrites;r0=vreads;n0=0;rw0=rwait;ww0=wwait;rd0=rdata;f0=wfull;q0=wseq;s0=wsame;h0=wrecent;p0=wpix;
 while(!$feof(stream)) begin
  rc=$fscanf(stream,"%c %h\n",kind,value);
  if(rc!=2) break;
  case(kind)
   "W","S": begin stream_gp0(value);words++;end
   "C": begin
     end_stream();
     write_gpu(4,value);
     if(value[31:24]==8'h10) begin repeat(8) @(negedge clk1x);read_gpu(readback);end
    end
   "R": begin end_stream();read_gpu(readback);reads++;if(readback!==value) read_mismatches++;end
   "V": begin
     end_stream();
     idle();
     $display("FRAME %0d cycles=%0d gpu_busy=%0d vram_busy=%0d vram_writes=%0d vram_reads=%0d gp0_words=%0d read_wait=%0d write_wait=%0d read_data=%0d full=%0d seq=%0d same=%0d recent=%0d pixels=%0d",
              value,cyc2-t0,busy2-b0,vbusy2-v0,vwrites-w0,vreads-r0,words-n0,rwait-rw0,wwait-ww0,rdata-rd0,wfull-f0,wseq-q0,wsame-s0,wrecent-h0,wpix-p0);
     `ifdef CHIP_STATS
     $display("CHIPS write_segments=%0d read_segments=%0d busy_edges=%0d edges=%0d",chip0.write_segments-ws0,chip0.read_segments-rs0,chip0.busy_edges-be0,chip0.edges-ed0);
     ws0=chip0.write_segments;rs0=chip0.read_segments;be0=chip0.busy_edges;ed0=chip0.edges;
     `endif
     snaps++;
     if(dump_every>0 && snaps%dump_every==0) begin
      repeat(64) @(negedge clk1x);   // posted VRAM writes land
      for(integer i=0;i<262144;i++) snapshot[i]={vram_get(2*i+1),vram_get(2*i)};
      $writememh($sformatf("%s/vram_%0d.hex",out_dir,value),snapshot);
     end
     t0=cyc2;b0=busy2;v0=vbusy2;w0=vwrites;r0=vreads;n0=words;rw0=rwait;ww0=wwait;rd0=rdata;f0=wfull;q0=wseq;s0=wsame;h0=wrecent;p0=wpix;
    end
   default: $fatal(1,"bad stream record %c",kind);
  endcase
  count++;
 end
 $display("PASS GPU timing words=%0d snapshots=%0d reads=%0d read_mismatches=%0d not_idle_at_snapshot=%0d",count,snaps,reads,read_mismatches,stuck);
 $finish;
end
endmodule
'''


def bench_text(template, psram, params=''):
    text = template.read_text()
    if text.count(MEMORY) != 1:
        sys.exit('run_gpu_timing: memory model instance not found in tb_gpu_replay.sv')
    head = text[:text.index('integer stream,rc,count=0')]
    values = dict(p.split('=') for p in params.split(',') if p)
    full = values.get('FULL_RATE', '0') not in ('0', "1'b0")
    memory = PSRAM.replace('VP_PARAMS', '#(' + ','.join(f'.{k}({v})' for k, v in values.items()) + ')' if values else '')
    memory = memory.replace('CHIP_LC', values.get('LATENCY', '3'))
    for i in '01':
        memory = memory.replace(f'CHIP_CLK{i}', 'clk2x' if full else f'c{i}_clk').replace(f'CHIP_EN{i}', f'c{i}_clk' if full else "1'b1")
    return head.replace(MEMORY, memory if psram else MEMORY + '\n' + DDR_ACCESS) + MAIN


def build(directory, psram, vram_source, params=''):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    binary = directory / 'obj/Vtb_gpu_probe'
    stamp = directory / 'bench.sv'
    template = ROOT / 'sim/reference/tb_gpu_replay.sv'
    sources = [Path(__file__), template, ROOT / 'sim/reference/psx_cram_cycle_model.sv', vram_source]
    if binary.exists() and stamp.exists() and all(stamp.stat().st_mtime >= s.stat().st_mtime for s in sources):
        return binary
    with (directory / 'build.log').open('w') as log:
        verilog = directory / 'system.v'
        if not verilog.exists():
            manifest = prepare(directory / 'sources')
            for source in manifest['sources']:
                subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={source["library"]}', source['path']],
                               cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
            with verilog.open('w') as output:
                subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog',
                                'psx_mister_psx_top_resource'], cwd=directory, stdout=output, stderr=log, timeout=180, check=True)
            from ghdl_verilog import normalize_generated_verilog
            normalize_generated_verilog(verilog)
        text = verilog.read_text()
        name = re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n', text, re.M)[0]
        header = text[re.search(r'^module ' + re.escape(name) + r'\s*\n', text, re.M).start():].split(');', 1)[0]
        ports = {p.lower(): p for p in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
        bench = bench_text(template, psram, params)
        instance = re.search(r'GPU_ENTITY gpu\(.*?\);', bench, re.S).group()
        bound = re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)
        stamp.write_text(bench.replace(instance, bound).replace('GPU_ENTITY', name))
        extra = [str(ROOT / 'sim/reference/psx_cram_cycle_model.sv'), str(vram_source)] if psram else []
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_probe', '--Mdir', str(directory / 'obj'), str(verilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), *extra, str(stamp)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=3600, check=True)
    return binary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('trace'); parser.add_argument('--name', default=None)
    parser.add_argument('--vram', choices=('ddr', 'psram'), default='psram')
    parser.add_argument('--vram-source', default=str(ROOT / 'rtl/memory/psx_vram_psram.sv'))
    parser.add_argument('--params', default='', help='psx_vram_psram parameters, e.g. FULL_RATE=1,LATENCY=5,WRITE_HOLD=2')
    parser.add_argument('--dump', type=int, default=0, help='write and compare VRAM every N-th marker (0: never)')
    args = parser.parse_args()
    trace = Path(args.trace)
    vram_source = Path(args.vram_source).resolve()
    tag = args.vram if vram_source == (ROOT / 'rtl/memory/psx_vram_psram.sv').resolve() else f'{args.vram}-{vram_source.parent.name}'
    if args.params:
        tag += '-' + re.sub(r'[^A-Za-z0-9]+', '-', args.params).lower()
    directory = ROOT / 'build/sim/gpu-timing' / tag
    directory.mkdir(parents=True, exist_ok=True)
    binary = build(directory, args.vram == 'psram', vram_source, args.params)
    out = ROOT / 'build/sim/gpu-timing' / f'{args.name or trace.stem}-{tag}'; out.mkdir(parents=True, exist_ok=True)
    snapshots = convert(trace, out)
    result = subprocess.run([str(binary), f'+stream={out / "stream.txt"}', f'+out={out}', f'+dump={args.dump}'],
                            cwd=out, capture_output=True, text=True, timeout=36000)
    (out / 'run.log').write_text(result.stdout + result.stderr)
    line = next((l for l in result.stdout.splitlines() if 'PASS GPU timing' in l), None)
    if result.returncode or not line:
        sys.exit('GPU timing failed:\n' + (result.stdout + result.stderr)[-1500:])
    frames = [dict(re.findall(r'(\w+)=(\d+)', l)) | {'frame': int(l.split()[1])}
              for l in result.stdout.splitlines() if l.startswith('FRAME ')]
    for f in frames:
        cycles = int(f['cycles'])
        print(f"frame {f['frame']}: {cycles} clk2x ({100 * cycles / FIELD_CLK2X:.0f}% of a field), "
              f"writes {f['vram_writes']} (waiting {f['write_wait']}), reads {f['vram_reads']} (waiting {f['read_wait']}, "
              f"until data {f['read_data']}), GP0 words {f['gp0_words']}; writes full {f['full']}, "
              f"next word {f['seq']}, same word {f['same']}, in last 16 {f['recent']}, pixels {f['pixels']}")
    if frames:
        cycles = sorted(int(f['cycles']) for f in frames)
        over = sum(c > FIELD_CLK2X for c in cycles)
        print(f'{len(frames)} frames: median {cycles[len(cycles) // 2]} clk2x, max {cycles[-1]}, '
              f'{over} over one field ({FIELD_CLK2X})')
    for l in result.stdout.splitlines():
        if l.startswith('CHIPS'): print(l)
    print(line)
    if args.dump:
        total = 0
        for frame in sorted(snapshots):
            if (out / f'vram_{frame}.hex').exists():
                diff, _, _ = compare(out, frame, snapshots[frame])
                total += len(diff)
                print(f'frame {frame}: {len(diff)} pixels differ from Beetle')
        print(f'{total} differing pixels in total')


if __name__ == '__main__':
    main()
