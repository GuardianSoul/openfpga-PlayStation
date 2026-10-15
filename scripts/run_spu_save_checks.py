#!/usr/bin/env python3
"""Compare save-capable SPU context RAM: voice audio plus streaming save/restore.

Uses the same independent voice oracles as run_spu_context_checks.py.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from prepare_spu_save_context import save_context_source as context_ram_source
from prepare_system_verilator import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool
from ghdl_verilog import normalize_generated_verilog



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Independent output directory for a toolchain comparison')
    args = parser.parse_args()
    args.cd = False
    directory = args.output.resolve() if args.output else ROOT / ('build/sim/spu-save-cd' if args.cd else 'build/sim/spu-save-voices')
    directory.mkdir(parents=True, exist_ok=True)
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator:
        raise SystemExit('GHDL synthesis and Verilator are required')
    manifest = prepare(directory / 'sources')
    original = next(s for s in manifest['sources'] if Path(s['path']).name == 'spu.vhd')
    candidate = context_ram_source(directory / 'candidate', original['path'])
    candidate_path = Path(candidate['path'])
    text = candidate_path.read_text().replace('entity spu is', 'entity spu_context is').replace('architecture arch of spu is', 'architecture arch of spu_context is')
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
        else:
            marker = 'initial begin'
            bench = bench.replace(marker, 'initial begin for(integer b=0;b<524288;b++) memory_candidate.data[b]=memory.data[b];', 1)
            bench = bench.replace(';$finish;', f';$display("PASS SPU context pair outputs={len(output_nets)} ticks=%0d",paired_ticks);$finish;')
        save_tasks = """
integer save_checks=0, save_nonzero=0;
reg [31:0] saved_image [0:511];
task scan_save(input integer phase);
 for(integer a=0;a<512;a++) begin
  @(posedge clk1x); #1; SS_Adr=9'(a);SS_rden=1;
  @(posedge clk1x); #1;
  if(SS_DataRead !== SS_DataRead_candidate) $fatal(1,"SPU save read mismatch addr=%0d",a);
  if(phase==0) begin saved_image[a]=SS_DataRead;if(SS_DataRead!=0) save_nonzero++;end
  if(phase==1 && ((a>=64 && a<304) || (a>=320 && a<370) || (a>=384 && a<480)))
   if(SS_DataRead!==saved_image[a]) $fatal(1,"SPU table restore mismatch addr=%0d got=%x expected=%x",a,SS_DataRead,saved_image[a]);
  save_checks++;
 end
 @(posedge clk1x); #1; SS_rden=0;
endtask
"""
        bench = bench.replace('initial begin',save_tasks+'\ninitial begin',1)
        restore_fixture = """
 cpuPaused=1;
 wait(SS_idle && SS_idle_candidate); @(posedge clk1x); #1; ce=0;
 scan_save(0);
 if(save_nonzero<40) $fatal(1,"SPU save fixture lacks nonzero state: %0d",save_nonzero);
 // Reset both cores, restore the actual architectural image, and check every
 // table word against the pre-reset image before resuming paired audio.
 ce=1;cpuPaused=0;cold_reset();ce=0;cpuPaused=1;
 loading_savestate=1;
 for(integer a=0;a<512;a++) begin
  @(posedge clk1x); #1; SS_Adr=9'(a);SS_DataWrite=saved_image[a];SS_wren=1;
 end
 @(posedge clk1x); #1; SS_wren=0;
 repeat(3) @(posedge clk1x);
 // LOAD's final reset retains restored tables with loading_savestate high.
 reset=1;repeat(8) @(posedge clk1x); #1; reset=0;
 scan_save(1);
 @(posedge clk1x); #1; loading_savestate=0;ce=1;cpuPaused=0;
 repeat(16) @(posedge spu_tick);
 $display("PASS SPU save reads=%0d nonzero_words=%0d restored_audio_frames=16",save_checks,save_nonzero);
 $display("PASS SPU context pair outputs=24 ticks=%0d save_fixture_complete=1",paired_ticks);
"""
        bench=bench.replace('$finish;',restore_fixture+'\n$finish;')
        bench_path = directory / 'bench.sv'; bench_path.write_text(bench)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_spu_mix', '--Mdir', str(directory / 'obj'), *map(str, generated), str(models), str(bench_path)], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    protected = {str(p): digest(p) for p in generated + [template, models, bench_path, directory / 'obj/Vtb_spu_mix']}
    result = subprocess.run([str(directory / 'obj/Vtb_spu_mix')], cwd=directory, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    (directory / 'run.log').write_text(output)
    print(output, end='')
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    identity &= all(digest(Path(r['path'])) == r['sha256'] and
                    digest(Path(r['original'])) == r['original_sha256'] and
                    ('selected' not in r or digest(Path(r['selected'])) == r['selected_sha256'])
                    for r in manifest['sources'] + [candidate])
    pairs = list(re.finditer(r'PASS SPU context pair outputs=(\d+) ticks=(\d+)', output))
    pair = pairs[-1] if pairs else None
    oracle = re.search(r'PASS SPU CD mixer checks=\s*384 ticks=\s*1536 period_checks=\s*1535', output) if args.cd else re.search(
        r'PASS SPU voice cases=\s*51 pcm_checks=\s*3928 envelope_checks=\s*1836 loop_checks=\s*51 max_voices=24 accumulation_checks=256 attack_steps=\s*153', output)
    passed = result.returncode == 0 and identity and bool(oracle) and bool(pair)
    passed &= 'PASS SPU save reads=1024' in output
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
              'savestate_readback_verified': 'PASS SPU save reads=1024' in output, 'dma_sample_upload_verified': False,
              'general_reverb_verified': False, 'game_verified': False,
              'hardware_verified': False, 'stdout': output}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()
