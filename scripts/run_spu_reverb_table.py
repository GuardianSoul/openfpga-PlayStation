#!/usr/bin/env python3
"""Compare the SPU reverb-table variant with the context-RAM SPU it modifies.

Both instances run the same random reverb fixture with separate, identical
memory models; all public outputs are compared after every falling clk2x edge.
"""
import json
from pathlib import Path
import re
import subprocess

from prepare_spu_context_ram import context_ram_source
from prepare_spu_reverb_table import reverb_table_source
from prepare_system_verilator import prepare
from tool_paths import ROOT, find_tool
from ghdl_verilog import normalize_generated_verilog


def main():
    directory = ROOT / 'build/sim/spu-reverb-table'; directory.mkdir(parents=True, exist_ok=True)
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL synthesis and Verilator are required')
    manifest = prepare(directory / 'sources')
    upstream = next(s for s in manifest['sources'] if Path(s['path']).name == 'spu.vhd')
    original = context_ram_source(directory / 'context', upstream['path'])
    candidate = reverb_table_source(directory / 'candidate', original['path'])
    path = Path(candidate['path'])
    path.write_text(path.read_text().replace('entity spu is', 'entity spu_reverb is').replace('architecture arch of spu is', 'architecture arch of spu_reverb is'))
    sources = [original if s is upstream else s for s in manifest['sources']] + [candidate]
    with (directory / 'build.log').open('w') as log:
        for record in sources:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=' + record['library'], record['path']], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        generated = []
        for entity in ('spu', 'spu_reverb'):
            out = directory / (entity + '.v')
            with out.open('w') as output:
                subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog', entity], cwd=directory, stdout=output, stderr=log, check=True, timeout=180)
            normalize_generated_verilog(out); generated.append(out)
        headers = []
        for entity, out in zip(('spu', 'spu_reverb'), generated):
            converted = out.read_text()
            match = re.search(r'^module (' + entity + r'(?:_Barch[^\s]*)?)[ \t]*\n', converted, re.M)
            header = converted[match.start():].split(');', 1)[0]
            ports = {name.lower(): name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
            outputs = {name.lower(): size or '' for size, name in re.findall(r'\boutput\s+(\[\d+:\d+\]\s+)?(\w+)', header)}
            headers.append((match[1], ports, outputs))
        common = set(re.findall(r'^module (\w+)', generated[0].read_text(), re.M))
        chunks = re.findall(r'^module (\w+)(.*?^endmodule\s*$)', generated[1].read_text(), re.M | re.S)
        generated[1].write_text('\n'.join('module ' + name + body for name, body in chunks if name not in common))
        bench = (ROOT / 'sim/reference/tb_spu_reverb.sv').read_text()
        instance = re.search(r'SPU_ENTITY spu_core\([^\n]+\);', bench).group()
        primary = re.sub(r'\.(\w+)\(', lambda m: '.' + headers[0][1][m[1].lower()] + '(', instance).replace('SPU_ENTITY', headers[0][0])
        bindings = re.findall(r'\.(\w+)\((\w+)\)', instance)
        output_nets = {net for name, net in bindings if name.lower() in headers[0][2]}
        changed = output_nets | {'sdram_dataRead', 'sdram_done'}
        second = instance.replace('SPU_ENTITY spu_core', headers[1][0] + ' candidate_core')
        second = re.sub(r'\.(\w+)\((\w+)\)', lambda m: '.' + headers[1][1][m[1].lower()] + '(' + m[2] + ('_candidate' if m[2] in changed else '') + ')', second)
        declarations = '\n'.join('logic ' + headers[0][2][name.lower()] + net + '_candidate;' for name, net in bindings if net in output_nets)
        declarations += '\nlogic [31:0] sdram_dataRead_candidate;\nlogic sdram_done_candidate;\n'
        comparisons = '\n'.join(f' if ({net} !== {net}_candidate) $fatal(1,"SPU reverb output mismatch {net} at tick=%0d",paired_ticks);' for net in sorted(output_nets))
        monitor = 'integer paired_ticks=0;\nalways @(negedge clk2x) begin\n #1; paired_ticks++;\n' + comparisons + '\nend\n'
        bench = bench.replace(instance, declarations + primary + '\n' + second + '\n' + monitor)
        memory = re.search(r'mister_sdram_model #\(.*?\) memory\(.*?\);', bench, re.S).group()
        copy = memory.replace(' memory(', ' memory_candidate(')
        copy = re.sub(r'\b(' + '|'.join(sorted(changed, key=len, reverse=True)) + r')\b', lambda m: m[0] + '_candidate', copy)
        bench = bench.replace(memory, memory + '\n' + copy)
        bench = bench.replace('$display("PASS SPU reverb fixture', f'$display("PASS SPU reverb pair outputs={len(output_nets)} ticks=%0d",paired_ticks);\n $display("PASS SPU reverb fixture')
        bench_path = directory / 'bench.sv'; bench_path.write_text(bench)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_spu_mix', '--Mdir', str(directory / 'obj'), *map(str, generated), str(ROOT / 'sim/reference/mister_memory_models.sv'), str(bench_path)], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    result = subprocess.run([str(directory / 'obj/Vtb_spu_mix')], cwd=directory, capture_output=True, text=True, timeout=1800)
    output = result.stdout + result.stderr
    (directory / 'run.log').write_text(output); print(output, end='')
    passed = result.returncode == 0 and 'PASS SPU reverb pair' in output and 'PASS SPU reverb fixture' in output
    (directory / 'report.json').write_text(json.dumps({'passed': passed, 'candidate': candidate, 'original': original,
        'compared_outputs': sorted(output_nets), 'stdout': output, 'game_verified': False, 'hardware_verified': False}, indent=2) + '\n')
    if not passed: raise SystemExit(1)


if __name__ == '__main__': main()
