#!/usr/bin/env python3
"""Compare spu-adsr-ticks against the SPU it modifies (context-RAM + spu-adsr-lean).
(Copied from run_spu_adsr_voices.py: the reference instance is that SPU instead
of upstream.)

The authored-voice bench (24 voices, ADSR settings, 51 attack trials, key
on/off, ce gaps) compares every SPU output on every clk2x edge. --fault
plants a wrong right shift in one tick load and must fail."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from prepare_spu_context_ram import context_ram_source
from prepare_spu_adsr_lean import adsr_lean_text
from prepare_spu_adsr_ticks import adsr_ticks_text
from prepare_system_verilator import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool
from ghdl_verilog import normalize_generated_verilog



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cd', action='store_true', help='Compare signed CD mixing instead of authored voices')
    parser.add_argument('--fault', action='store_true', help='Plant a wrong shift in a tick load (must fail)')
    parser.add_argument('--output', type=Path, help='Independent output directory for a toolchain comparison')
    args = parser.parse_args()
    directory = args.output.resolve() if args.output else ROOT / ('build/sim/spu-adsr-ticks-' + ('cd' if args.cd else 'voices') + ('-fault' if args.fault else ''))
    directory.mkdir(parents=True, exist_ok=True)
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator:
        raise SystemExit('GHDL synthesis and Verilator are required')
    manifest = prepare(directory / 'sources')
    original = next(s for s in manifest['sources'] if Path(s['path']).name == 'spu.vhd')
    candidate = context_ram_source(directory / 'candidate', original['path'])
    candidate_path = Path(candidate['path'])
    lean = adsr_lean_text(candidate_path.read_text())
    # the reference: the lean SPU as entity spu, in place of upstream
    reference = directory / 'reference' / 'spu.vhd'; reference.parent.mkdir(exist_ok=True); reference.write_text(lean)
    original['path'] = str(reference); original['sha256'] = digest(reference)
    original['original'] = str(reference); original['original_sha256'] = original['sha256']
    original.pop('selected', None)
    text = adsr_ticks_text(lean)
    if args.fault:
        import os
        if os.environ.get('ADSR_TICKS_FAULT') == 'step':   # the step of slow increasing rates off by one
            early = text.replace("return shift_left(to_signed( 7, 16) - to_integer(rate(1 downto 0)), shift);", "return shift_left(to_signed( 6, 16) - to_integer(rate(1 downto 0)), shift);")
        elif os.environ.get('ADSR_TICKS_FAULT') == 'shift0':   # the same load with no shift
            early = text.replace("tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 2;", "tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 0;")
        elif os.environ.get('ADSR_TICKS_FAULT') == 'gross':   # reachability probe: a fixed 256-tick load at the >>2 site
            early = text.replace("tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 2;", "tk_mode := 2; tk_val := to_unsigned(256, 24);")
        else:
            early = text.replace("tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 2;", "tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 1;")
        if early == text: raise SystemExit('fault anchor changed')
        text = early
    text = text.replace('entity spu is', 'entity spu_context is').replace('architecture arch of spu is', 'architecture arch of spu_context is')
    candidate_path.write_text(text)
    candidate['sha256'] = digest(candidate_path)
    template = ROOT / ('sim/reference/tb_spu_cd_mix.sv' if args.cd else 'sim/reference/tb_spu_voice.sv')
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    normalizations = []
    with (directory / 'build.log').open('w') as log:
        for record in manifest['sources'] + [candidate]:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=' + record['library'], record['path']], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        generated = []
        for entity in ('spu', 'spu_context'):
            path = directory / (entity + '.v')
            with path.open('w') as output:
                subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog', entity], cwd=directory, stdout=output, stderr=log, check=True, timeout=120)
            normalizations.append(normalize_generated_verilog(path))
            generated.append(path)
        headers = []
        for entity, path in zip(('spu', 'spu_context'), generated):
            converted = path.read_text()
            match = re.search(r'^module (' + entity + r'(?:_Barch[^\s]*)?)[ \t]*\n', converted, re.M)
            header = converted[match.start():].split(');', 1)[0]
            ports = {name.lower(): name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
            outputs = {name.lower(): size or '' for size, name in re.findall(r'\boutput\s+(\[\d+:\d+\]\s+)?(\w+)', header)}
            headers.append((match[1], ports, outputs))
        # Keep common dependencies once, and retain any newly parameterized
        # dpram wrapper needed only by the candidate.
        candidate_verilog = generated[1].read_text()
        common = set(re.findall(r'^module (\w+)', generated[0].read_text(), re.M))
        chunks = re.findall(r'^module (\w+)(.*?^endmodule\s*$)', candidate_verilog, re.M | re.S)
        generated[1].write_text('\n'.join('module ' + name + body for name, body in chunks if name not in common))
        bench = template.read_text()
        instance = re.search(r'SPU_ENTITY spu_core\([^\n]+\);', bench).group()
        primary = re.sub(r'\.(\w+)\(', lambda m: '.' + headers[0][1][m[1].lower()] + '(', instance).replace('SPU_ENTITY', headers[0][0])
        bindings = re.findall(r'\.(\w+)\((\w+)\)', instance)
        output_nets = {net for name, net in bindings if name.lower() in headers[0][2]}
        memory_inputs = {'sdram_dataRead', 'sdram_done'}
        changed = output_nets | memory_inputs
        candidate_instance = instance.replace('SPU_ENTITY spu_core', headers[1][0] + ' candidate_core')
        candidate_instance = re.sub(r'\.(\w+)\((\w+)\)', lambda m: '.' + headers[1][1][m[1].lower()] + '(' + m[2] + ('_candidate' if m[2] in changed else '') + ')', candidate_instance)
        declarations = '\n'.join('logic ' + headers[0][2][name.lower()] + net + '_candidate;' for name, net in bindings if net in output_nets)
        declarations += '\nlogic [31:0] sdram_dataRead_candidate;\nlogic sdram_done_candidate;\n'
        comparisons = '\n'.join(f' if ({net} !== {net}_candidate) $fatal(1,"SPU context output mismatch {net} at tick=%0d",paired_ticks);' for net in sorted(output_nets))
        monitor = 'integer paired_ticks=0;\nalways @(negedge clk2x) begin\n #1; paired_ticks++;\n' + comparisons + '\nend\n'
        bench = bench.replace(instance, declarations + primary + '\n' + candidate_instance + '\n' + monitor)
        memory = re.search(r'mister_sdram_model #\(.*?\) memory\(.*?\);', bench, re.S).group()
        second = memory.replace(' memory(', ' memory_candidate(')
        second = re.sub(r'\b(' + '|'.join(sorted(changed, key=len, reverse=True)) + r')\b', lambda m: m[0] + '_candidate', second)
        bench = bench.replace(memory, memory + '\n' + second)
        if not args.cd:
            marker = ' for(integer trial=0;trial<51;trial++) begin\n  attack_progress=0;'
            if bench.count(marker) != 1: raise ValueError('Voice preload marker changed')
            preload = ''' for(integer v=0;v<24;v++) begin
      memory.data[(v+100)*4096]=8'((((v%4)+1)<<4)|(v%13));
      memory.data[(v+100)*4096+1]=7;
      for(integer b=2;b<16;b++) memory.data[(v+100)*4096+b]=8'(v*37+b*19);
     end
     for(integer b=0;b<524288;b++) memory_candidate.data[b]=memory.data[b];
    '''
            bench = bench.replace(marker, preload + marker)
            extra = '''
     cold_reset();
     write_reg(10'h180,16'h1000);write_reg(10'h182,16'h1000);
     for(integer v=0;v<24;v++) begin
      write_reg(v*16+0,16'(16'h0100+v*13));write_reg(v*16+2,16'(16'h7800+v*7));
      write_reg(v*16+4,16'(16'h0801+(v%4)*16'h07ff));
      write_reg(v*16+6,16'((v+100)*512));write_reg(v*16+14,16'((v+100)*512));
      write_reg(v*16+8,16'h00ff);write_reg(v*16+10,16'h1fc0);
     end
     write_reg(10'h1aa,16'hc000);write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
     for(integer n=0;n<160;n++) begin
      @(posedge spu_tick);@(negedge clk1x);
      if(n%11==0) begin
       ce=0;repeat(5) @(negedge clk1x);ce=1;
      end
      if(n==80) begin write_reg(10'h18c,16'h5555);write_reg(10'h18e,16'h0055);end
     end
     $display("PASS SPU context pair outputs=%0d ticks=%0d extra_filter_fractional_frames=160",OUTPUT_COUNT,paired_ticks);
    '''.replace('OUTPUT_COUNT', str(len(output_nets)))
            bench = bench.replace(';$finish;', ';' + extra + '$finish;')
            # Full envelopes: fast attack/decay, every sustain level/direction/
            # mode, key off into release, with ce gaps (spu-adsr-lean coverage).
            adsr = '''
     for(integer round=0;round<4;round++) begin
      cold_reset();
      write_reg(10'h180,16'h3fff);write_reg(10'h182,16'h3fff);
      for(integer v=0;v<24;v++) begin
       write_reg(v*16+0,16'h3fff);write_reg(v*16+2,16'h3fff);write_reg(v*16+4,16'h1000);
       write_reg(v*16+6,16'((v+100)*512));write_reg(v*16+14,16'((v+100)*512));
       write_reg(v*16+8,16'(((round*7+v)%16)|(((v+round)%16)<<4)|(((v*3+round)%32)<<8)|((v%2)<<15)));
       write_reg(v*16+10,16'(((v*5+round*3)%32)|(((v+round)%2)<<5)|(((v*7+round)%128)<<6)|((round%2)<<14)|(((v+round)%2)<<15)));
      end
      write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
      for(integer n=0;n<1500;n++) begin
       @(posedge spu_tick);@(negedge clk1x);
       if(n%97==0) begin ce=0;repeat(3) @(negedge clk1x);ce=1;end
       if(n==700) begin write_reg(10'h18c,16'hffff);write_reg(10'h18e,16'h00ff);end
       if(n==1100) begin write_reg(10'h188,16'h5555);write_reg(10'h18a,16'h0055);end
      end
     end
     // spu-adsr-ticks: exponential attacks at rates 48-55 (the >>1 / >>2 tick loads above 0x6000;
     // a wrong shift only changes when the voice reloads from rate 52 up, ~14,000 samples in).
     // SPU enabled and unmuted (0x1AA), so every voice reaches the audio outputs.
     for(integer round=0;round<2;round++) begin
      cold_reset();
      write_reg(10'h1aa,16'hc000);
      write_reg(10'h180,16'h3fff);write_reg(10'h182,16'h3fff);
      for(integer v=0;v<24;v++) begin
       write_reg(v*16+0,16'h3fff);write_reg(v*16+2,16'h3fff);write_reg(v*16+4,16'h1000);
       write_reg(v*16+6,16'((v+100)*512));write_reg(v*16+14,16'((v+100)*512));
       write_reg(v*16+8,16'(((round*5+v)%16)|(((v+round)%16)<<4)|((48+(v+round)%8)<<8)|(((v+round)%4!=0)<<15)));
       write_reg(v*16+10,16'(((v*5+round*3)%32)|(((v+round)%2)<<5)|(((v*7+round)%128)<<6)|((round%2)<<14)|(((v+round)%2)<<15)));
      end
      write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
      for(integer n=0;n<24000;n++) begin
       @(posedge spu_tick);@(negedge clk1x);
       if(n%89==0) begin ce=0;repeat(3) @(negedge clk1x);ce=1;end
       if(n==23000) begin write_reg(10'h18c,16'hffff);write_reg(10'h18e,16'h00ff);end
      end
     end
     $display("PASS SPU ADSR envelope rounds=4 fast-attack rounds=2");
    '''
            if bench.count('$display("PASS SPU context pair') != 1: raise ValueError('context pair marker changed')
            bench = bench.replace('$display("PASS SPU context pair', adsr + '$display("PASS SPU context pair')
            if bench.count('repeat(2500000) @(negedge clk1x);$fatal(1,"Voice overall timeout")') != 1: raise ValueError('timeout marker changed')
            bench = bench.replace('repeat(2500000) @(negedge clk1x);$fatal(1,"Voice overall timeout")', 'repeat(120000000) @(negedge clk1x);$fatal(1,"Voice overall timeout")')
        else:
            marker = 'initial begin'
            bench = bench.replace(marker, 'initial begin for(integer b=0;b<524288;b++) memory_candidate.data[b]=memory.data[b];', 1)
            bench = bench.replace(';$finish;', f';$display("PASS SPU context pair outputs={len(output_nets)} ticks=%0d",paired_ticks);$finish;')
        bench_path = directory / 'bench.sv'; bench_path.write_text(bench)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_spu_mix', '--Mdir', str(directory / 'obj'), *map(str, generated), str(models), str(bench_path)], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    protected = {str(p): digest(p) for p in generated + [template, models, bench_path, directory / 'obj/Vtb_spu_mix']}
    result = subprocess.run([str(directory / 'obj/Vtb_spu_mix')], cwd=directory, capture_output=True, text=True, timeout=1800)
    output = result.stdout + result.stderr
    (directory / 'run.log').write_text(output)
    print(output, end='')
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    identity &= all(digest(Path(r['path'])) == r['sha256'] and
                    digest(Path(r['original'])) == r['original_sha256'] and
                    ('selected' not in r or digest(Path(r['selected'])) == r['selected_sha256'])
                    for r in manifest['sources'] + [candidate])
    pair = re.search(r'PASS SPU context pair outputs=(\d+) ticks=(\d+)', output)
    oracle = re.search(r'PASS SPU CD mixer checks=\s*384 ticks=\s*1536 period_checks=\s*1535', output) if args.cd else re.search(
        r'PASS SPU voice cases=\s*51 pcm_checks=\s*3928 envelope_checks=\s*1836 loop_checks=\s*51 max_voices=24 accumulation_checks=256 attack_steps=\s*153', output)
    passed = result.returncode == 0 and identity and bool(oracle) and bool(pair)
    passed &= bool(pair and int(pair[1]) == len(output_nets) and int(pair[2]) > 0)
    report = {'passed': passed, 'exit_code': result.returncode,
              'source_and_input_identity_verified': identity, 'protected_sha256': protected,
              'preparation': manifest, 'candidate': candidate, 'normalization': normalizations,
              'ghdl_version': subprocess.check_output([ghdl, '--version'], text=True),
              'verilator_version': subprocess.check_output([verilator, '--version'], text=True).strip(),
              'compared_outputs': sorted(output_nets), 'compared_ticks': int(pair[2]) if pair else 0,
              'sample_edge': 'One femtosecond after each falling clk2x edge',
              'stereo_sample_checks': 384 if args.cd else 3928,
              'extra_filter_fractional_frames': 0 if args.cd else 160,
              'extra_scope': 'Paired upstream output comparison, not an independent general ADPCM oracle',
              'savestate_readback_verified': False, 'dma_sample_upload_verified': False,
              'general_reverb_verified': False, 'game_verified': False,
              'hardware_verified': False, 'stdout': output}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()
