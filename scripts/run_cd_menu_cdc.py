#!/usr/bin/env python3
"""Four-disc release CDC contract against authored APF sectors and metadata.

Use the existing pinned-handler fixture, actual menu/swap/stream logic and
before-map synchronizer preservation. Add skewed 0..13ns wires ONLY in the
simulation copy. This is a digital protocol test, not physical signoff.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_pocket_cd_cdc import CD_FILE_PRESENCE_SHA, CD_TABLE_SHA, preserve_sync_flops, preserve_menu_flops
from prepare_resource_targets import digest
from psx_disc_host import metadata_words, multitrack_metadata_words
from tool_paths import ROOT, find_tool


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('CD menu fixture anchor differs: ' + old[:100])
    return text.replace(old, new, 1)


def delayed_stream(text, fault, *, table=False):
    header, host = text.split('    // ============================ host side =============================', 1)
    host, core = host.split('    // ============================ core side =============================', 1)
    parts = dict(host=host, core=core)
    fields = dict(host=[('req_lba', '20'), ('sel_slot', '16')],
                  core=[('m_valid', '1'), ('m_end', '20'), ('m_minutes', '8'),
                        ('m_seconds', '8'), ('m_ntracks', 'TB+1'), ('m_slot', '16'),
                        ('resp_buf', 'SB'), ('resp_kind', '2'), ('m_present', '4')])
    if table:
        fields['core'].append(('m_tb', '1'))
    aliases = []
    for domain, signals in fields.items():
        for index, (name, width) in enumerate(signals):
            alias = 'bundle_' + domain + '_' + name
            parts[domain], n = re.subn(r'\b' + name + r'\b', alias, parts[domain])
            if n < 1:
                raise ValueError('Missing CD consumer: ' + name)
            if name in ('m_valid', 'm_tb'):
                delay = '200.0' if fault == name else '13.0'
                aliases += [f'wire {alias};', f'assign #({delay}) {alias}={name};']
                continue
            delay = ('200.0' if fault == name else f'(13.0*((bitnum+{index})%4)/3.0)')
            aliases += [f'wire [{width}-1:0] {alias};',
                        f'for(genvar bitnum=0;bitnum<{width};bitnum++) begin: delay_{alias}',
                        f' assign #({delay}) {alias}[bitnum]={name}[bitnum];', 'end']
    return '`timescale 1ns/1ps\n' + header + '\n' + '\n'.join(aliases) + parts['host'] + parts['core']


def menu_bench(text):
    text = once(text, ' always #14.763 cclk=~cclk;', ''' real phase=0;
 initial begin
  if($value$plusargs("CORE_PHASE=%f",phase)) begin end
  #(phase);forever #14.763 cclk=~cclk;
 end''')
    text = once(text, " reg [15:0] disc_slot_id=16'h20;reg disc_select_strobe=0;", '''
 wire [15:0] disc_slot_id;wire disc_select_strobe;
 reg [1:0] disc_setting=0;wire [3:0] discs_present;
 wire menu_request,change_req,change_done,guest_idle,menu_busy;
 integer present_mask=15;
 psx_disc_menu menu(.clk(cclk),.reset(reset),.disc_setting(disc_setting),
  .change_req(change_req),.change_done(change_done),.disc_slot_id(disc_slot_id),
  .disc_select_strobe(disc_select_strobe),.disc_change_done(disc_change_done),
  .discs_present(discs_present),.swap_request(menu_request));
 // Short lid delays accelerate the fixture; production defaults are untouched.
 psx_disc_swap #(.OPEN_CYCLES(32),.CLOSE_CYCLES(16)) swapper(
  .clk(cclk),.reset(reset),.manual_lid_open(1'b0),.swap_request(menu_request),
  .disc_pending(disc_pending),.quiet(guest_idle),.change_req(change_req),
  .change_done(change_done),.lid_open(),.busy(menu_busy));
''')
    text = once(text, ".DEFAULT_SLOT(16'h20)", ".DEFAULT_SLOT(16'd101),.TRACK_STRIDE(16'd1),.DISC_STEP(16'd100)")
    text = once(text, '.has_disc(has_disc),', '.has_disc(has_disc),.discs_present(discs_present),.guest_idle(guest_idle),')
    a = text.index(' function automatic integer slot_size(')
    b = text.index(' endfunction', a) + len(' endfunction')
    text = text[:a] + ''' function automatic integer disc_sectors(input integer disc);
  case(disc) 0:return 40;1:return 9;2:return 30;3:return 12;endcase
  return 0;
 endfunction
 function automatic integer slot_size(input integer slot);
  for(integer d=0;d<4;d++) if(slot==101+100*d) return present_mask[d] ? disc_sectors(d)*2352 : 0;
  return -1;
 endfunction''' + text[b:]
    a = text.index(' task automatic write_table();')
    b = text.index(' endtask', a) + len(' endtask')
    text = text[:a] + ''' task automatic write_table();
  // Deliberately permuted table positions; unprovided discs have size zero.
  for(integer p=0;p<4;p++) begin
   integer d;d=(p+2)%4;
   write_bus(32'hf8002000+p*8,swap(101+100*d));
   write_bus(32'hf8002004+p*8,swap(slot_size(101+100*d)));
  end
 endtask''' + text[b:]
    a = text.index(' task automatic select(')
    b = text.index(' endtask', a) + len(' endtask')
    text = text[:a] + ''' task automatic select(input [15:0] slot);
  integer prior,n;prior=change_count;
  @(negedge hclk);disc_setting=2'((slot-101)/100);
  n=0;while(change_count==prior) begin
   @(posedge cclk);n++;if(n>200000) $fatal(1,"Disc change never completed");
  end
  wait(!menu_busy);repeat(20) @(negedge cclk);
 endtask''' + text[b:]
    a = text.index(' integer test_case=0,base_changes;')
    text = text[:a] + '''
 integer checked_discs=0,ignored_choices=0;
 reg old_inflight=0,old_waiting=0;
 reg [15:0] old_slot=101;reg [19:0] old_publication=0;
 always @(negedge cclk) begin
  if(!reset && old_inflight && dut.sel_inflight && dut.sel_slot!==old_slot)
   $fatal(1,"Selection changed before acknowledgement");
  old_inflight=dut.sel_inflight;old_slot=dut.sel_slot;
 end
 always @(negedge hclk) begin
  if(!host_reset && old_waiting && dut.mount_waiting &&
     {dut.m_slot,dut.m_present}!==old_publication)
   $fatal(1,"Publication changed before acknowledgement");
  old_waiting=dut.mount_waiting;old_publication={dut.m_slot,dut.m_present};
 end
 always @(posedge hclk) if(!host_reset && dut.h_sel_edge &&
    dut.bundle_host_sel_slot!==disc_slot_id) $fatal(1,"Selection payload missed toggle aperture");
 initial begin
  integer prior,current;current=0;
  if($value$plusargs("PRESENT_MASK=%d",present_mask)) begin end
  $readmemh("meta-a.hex",meta_a);$readmemh("meta-b.hex",meta_b);
  $readmemh("meta-a2.hex",meta_a2);$readmemh("meta-big.hex",meta_big);
  size_a=40*2352;size_b=9*2352;size_a2=30*2352;
  apf_latency=400;apf_spacing=2;
  repeat(6) @(negedge cclk);host_reset=0;reset=0;
  op_table=1;op_allcomplete=1;
  wait(change_count==1);repeat(20) @(negedge cclk);
  if(discs_present!==4'(present_mask)) $fatal(1,"Disc presence mask mismatch");
  check_table(0);fetch(101,40,150,0);checked_discs++;
  for(integer round=0;round<8;round++) begin
   integer d,s;d=(round*3+1)%4;s=101+100*d;
   if(!present_mask[d]) begin
    prior=change_count;@(negedge hclk);disc_setting=2'(d);
    repeat(2000) @(negedge cclk);
    if(change_count!=prior || menu_busy || disc_slot_id!=101+100*current)
     $fatal(1,"Absent disc choice was not ignored");
    ignored_choices++;
   end else if(d!=current) begin
    select(16'(s));current=d;
    if(dut.m_slot!=s || dut.sel_slot!=s || discs_present!==4'(present_mask) || sector_count!=disc_sectors(d))
     $fatal(1,"Mounted slot/publication mismatch");
    check_table(d);fetch(s,disc_sectors(d),149,0);
    fetch(s,disc_sectors(d),150,0);fetch(s,disc_sectors(d),150+disc_sectors(d)-1,0);
    fetch(s,disc_sectors(d),150+disc_sectors(d),0);checked_discs++;
   end
  end
  if(read_error || checks<800 || checked_discs<2) $fatal(1,"Insufficient menu coverage");
  $display("PASS CD MENU CDC mask=%0d checked_discs=%0d ignored=%0d checks=%0d sectors=%0d",
   present_mask,checked_discs,ignored_choices,checks,sector_reads);
  $finish;
 end
 initial begin #2000000000;$fatal(1,"CD menu timeout");end
endmodule
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    parent = ROOT / 'build/sim/cd-menu-cdc'; parent.mkdir(parents=True, exist_ok=True)
    work = args.output.resolve() if args.output else Path(tempfile.mkdtemp(dir=parent, prefix='checks-'))
    if args.output:
        if work.exists() or not work.is_relative_to(ROOT / 'build'):
            raise ValueError('Fresh ignored build output required')
        work.mkdir(parents=True)
    print(work, flush=True)
    stream, menu = [ROOT / 'rtl/pocket' / n for n in ('psx_cd_stream.sv', 'psx_disc_menu.sv')]
    bench = ROOT / 'sim/pocket/tb_cd_stream.sv'
    dependencies = [ROOT / p for p in ('rtl/pocket/psx_disc_swap.sv',
        'references/MIRLO64/target/pocket/core_bridge_cmd.v', 'references/MIRLO64/platform/pocket/common.v',
        'sim/pocket/mf_datatable_model.sv')]
    protected = {str(p): digest(p) for p in [stream, menu, bench, *dependencies, Path(__file__),
        ROOT / 'scripts/prepare_pocket_cd_cdc.py', ROOT / 'scripts/psx_disc_host.py']}
    if digest(stream) not in (CD_FILE_PRESENCE_SHA, CD_TABLE_SHA):
        raise ValueError('Expected reviewed release CD stream')
    for name, sectors in [('meta-a.hex', 40), ('meta-b.hex', 9), ('meta-a2.hex', 30), ('meta-big.hex', 12)]:
        (work / name).write_text(''.join(f'{x:08x}\n' for x in metadata_words(sectors, region=2)))
    (work / 'meta-mt.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words([20, 162, 155], region=2)))
    copied_bench = work / 'bench.sv'; copied_bench.write_text(menu_bench(bench.read_text()))
    copied_menu = work / 'menu.sv'; copied_menu.write_text(preserve_menu_flops(menu.read_text()))
    prepared = preserve_sync_flops(stream.read_text()); runs = []
    for name, fault, marker in [('bounded13', None, None), ('late-selection', 'sel_slot', 'Selection payload missed toggle aperture'),
                                 ('late-presence', 'm_present', 'Disc presence mask mismatch')]:
        d = work / name; d.mkdir(); rtl = d / 'stream.sv'; rtl.write_text(delayed_stream(prepared, fault))
        cmd = [find_tool('verilator'), '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal',
               '+define+CD_STREAM_TRACKS=19', '--top-module', 'tb_cd_stream', '--Mdir', str(d / 'obj'),
               *map(str, [rtl, copied_menu, copied_bench, *dependencies])]
        with (d / 'build.log').open('w') as f:
            subprocess.run(cmd, cwd=work, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=300)
        for phase, mask in ([(p, m) for p in [0, .1, 6.7, 13.4] for m in [15, 7, 5]] if not fault else [(0, 15)]):
            p = subprocess.run([str(d / 'obj/Vtb_cd_stream'), f'+CORE_PHASE={phase}', f'+PRESENT_MASK={mask}'],
                               cwd=work, capture_output=True, text=True, timeout=120)
            out = p.stdout + p.stderr; log = d / f'phase{phase}-mask{mask}.log'; log.write_text(out)
            passed = p.returncode != 0 and marker in out if fault else p.returncode == 0 and out.count('PASS CD MENU CDC') == 1
            runs.append(dict(name=name, fault=fault, phase_ns=phase, present_mask=mask, expected_result=passed,
                             exit_code=p.returncode, log=str(log), log_sha256=digest(log), rtl_sha256=digest(rtl)))
            (work / 'report.json').write_text(json.dumps(dict(passed=False, runs=runs), indent=2) + '\n')
            print(name, phase, mask, passed, out.strip()[-190:], flush=True)
            if not passed:
                raise RuntimeError('CD menu CDC check failed')
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    report = dict(passed=identity and len(runs) == 14, source_identity_verified=identity, original_sources=protected,
                  runs=runs, slots=[101, 201, 301, 401], tracks=19, payload_bound_ns=13,
                  synchronizer_and_toggle_delays_unmodified=True, per_bit_payload_skew_modelled=True,
                  analog_metastability_modelled=False, physical_timing_verified=False, hardware_verified=False)
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['passed']:
        raise RuntimeError('CD menu CDC source/coverage changed')


if __name__ == '__main__':
    main()
