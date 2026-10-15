#!/usr/bin/env python3
"""Check raw-sector and track-info delivery without private game images."""
import json
from pathlib import Path
import subprocess

from psx_disc_host import DiscHost
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    directory=ROOT/'build/sim/disc-host';directory.mkdir(parents=True,exist_ok=True)
    asset_dir=directory/'Disc Host Test';asset_dir.mkdir(exist_ok=True)
    blocks=[bytes((i*29+sector*101)&255 for i in range(2352)) for sector in range(5)]
    image=asset_dir/'Disc Image.bin';image.write_bytes(b''.join(blocks))
    cue=asset_dir/'Disc Image.cue';cue.write_text('FILE "Disc Image.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:02\n')
    with DiscHost(cue) as host:
        meta=host.prepare(directory)
        requests=[0,149,150,151,152,153,524287]
        expected=directory/'expected.hex16'
        expected.write_text(''.join(f'{int.from_bytes(host.read_physical(lba)[i:i+2],"little"):04x}\n'
            for lba in requests for i in range(0,2352,2)))
    source=ROOT/'sim/reference/psx_disc_host.sv';bench=directory/'tb_disc_host.sv'
    bench.write_text('''`timescale 1ns/1ps
module tb_disc_host;
reg clk=0,start=0,request=0;
reg [31:0] lba=0;
wire ack,write_enable,metadata_write,mounted;
wire [15:0] data;
wire [8:0] metadata_addr;
wire [31:0] metadata_data;
integer sector_count;
psx_disc_host host(.clk(clk),.start(start),.sector_request(request),.physical_lba(lba),
 .sector_ack(ack),.sector_write(write_enable),.sector_data(data),.track_addr(metadata_addr),
 .track_data(metadata_data),.track_write(metadata_write),.mounted(mounted),.sector_count(sector_count));
always #15 clk=~clk;
reg [31:0] expected_meta[0:399];
reg [15:0] expected[0:8231];
reg [31:0] requests[0:6];
integer meta_checks=0,data_checks=0,request_index=0,word_index=0;
initial begin
 $readmemh("trackinfo.hex32",expected_meta);$readmemh("expected.hex16",expected);
 requests[0]=0;requests[1]=149;requests[2]=150;requests[3]=151;requests[4]=152;requests[5]=153;requests[6]=524287;
 repeat(5) @(negedge clk);start=1;wait(mounted);
 for(integer i=0;i<7;i++) begin
  @(negedge clk);lba=requests[i];request=1;
  wait(ack);@(negedge clk);request=0;
  wait(!ack);if(word_index!=1176) $fatal(1,"Sector halfword count changed");word_index=0;request_index++;
 end
 repeat(3) @(negedge clk);
 if(meta_checks!=400 || data_checks!=8232 || sector_count!=7) $fatal(1,"Disc host checks incomplete");
 $display("PASS disc host metadata=%d halfwords=%d sectors=%d",meta_checks,data_checks,sector_count);$finish;
end
always @(posedge clk) begin
 if(metadata_write) begin
  if(metadata_addr!=meta_checks || metadata_data!==expected_meta[meta_checks]) $fatal(1,"Track-info mismatch");meta_checks++;
 end
 if(write_enable) begin
  if(!ack || !mounted || data!==expected[data_checks]) $fatal(1,"Raw sector mismatch index=%d word=%d",request_index,word_index);
  word_index++;data_checks++;
 end
end
initial begin repeat(20000) @(negedge clk);$fatal(1,"Disc host timeout");end
endmodule
''')
    verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    with (directory/'checks.log').open('w') as log:
        subprocess.run([verilator,'--binary','--timing','-j','2','-Wno-fatal','--top-module','tb_disc_host',
            '--Mdir',str(directory/'obj'),str(source),str(bench)],cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=120,check=True)
    args=[str(directory/'obj/Vtb_disc_host'),f'+disc_file={image}',f'+disc_metadata={meta["metadata"]}',
        '+disc_sectors=3','+disc_index=2','+disc_delay=13']
    protected={str(path):digest(path) for path in (source,bench,image,cue,meta['metadata'],expected,directory/'obj/Vtb_disc_host')}
    result=subprocess.run(args,cwd=directory,capture_output=True,text=True,timeout=60)
    output=result.stdout+result.stderr;print(output,end='')
    with (directory/'checks.log').open('a') as file:file.write(output)
    identity=all(digest(Path(path))==sha for path,sha in protected.items())
    passed=result.returncode==0 and identity and 'PASS disc host' in output
    (directory/'report.json').write_text(json.dumps({'host_sha256':digest(source),'bench_sha256':digest(bench),
        'metadata_sha256':digest(meta['metadata']),'expected_sha256':digest(expected),'stdout':output,'passed':passed,
        'protected_sha256':protected,'source_and_input_identity_verified':identity,
        'verilator_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'metadata_checks':400,'halfword_checks':8232,'sector_checks':7,
        'cd_controller_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not passed:raise SystemExit('Disc host checks failed')


if __name__=='__main__':main()
