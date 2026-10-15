#!/usr/bin/env python3
"""Pixel-compare the candidate GPU with the texture-cache tag-generation variant.

Same bench as run_gpu_vertex_ram: full VRAM image of every oracle program and
the GPU readbacks; cycle alignment is not required (a wrapped cache clear takes
256 extra clocks).
"""
from pathlib import Path
import re
import subprocess

import run_gpu_line_share as share
from prepare_gpu_tag_generation import tag_generation_text
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main(bits=8):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    directory = ROOT / 'build/sim' / ('gpu-tag-generation' if bits == 8 else f'gpu-tag-generation-{bits}bit'); directory.mkdir(parents=True, exist_ok=True)
    original_prepare = share.prepare
    with (directory / 'checks.log').open('w') as file:
        _, oldverilog, oldname = share.transcode(directory / 'original', True, ghdl, file, borrowed=True)
        oldverilog.write_text(oldverilog.read_text().replace('shared__', 'original__')); oldname = oldname.replace('shared__', 'original__')
        def prepare(target, **kwargs):
            manifest = original_prepare(target, **kwargs)
            source = next(s for s in manifest['sources'] if Path(s['path']).name == 'gpu_pixelpipeline.vhd')
            folder = Path(target) / 'gpu-tag-generation'; folder.mkdir(parents=True, exist_ok=True)
            path = folder / 'gpu_pixelpipeline.vhd'; path.write_text(tag_generation_text(Path(source['path']).read_text(), bits))
            manifest['sources'][manifest['sources'].index(source)] = {**source, 'path': str(path), 'sha256': digest(path),
                'selected': source['path'], 'selected_sha256': source['sha256']}
            return manifest
        share.prepare = prepare
        _, newverilog, newname = share.transcode(directory / 'adapted', True, ghdl, file, borrowed=True)
        share.prepare = original_prepare
        top = share.bench(directory, oldname, newname, False, False, True)
        bench = top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU tag generation')
        monitor = ("integer tag_clears=0, tag_wraps=0; logic [7:0] last_generation=1;\n"
                   "always @(posedge clk2x) begin if(new_gpu.igpu_pixelpipeline.taggeneration!=last_generation) begin\n"
                   " tag_clears++; if(new_gpu.igpu_pixelpipeline.taggeneration==1) tag_wraps++; end\n"
                   " last_generation=new_gpu.igpu_pixelpipeline.taggeneration; end\n"
                   "final $display(\"TAG clears=%0d wraps=%0d\",tag_clears,tag_wraps);\n")
        bench = bench.replace('integer checks=0;', monitor + 'integer checks=0;', 1)
        top.write_text(bench)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=file, stderr=subprocess.STDOUT, timeout=900, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    print('\n'.join(l for l in output.splitlines() if 'PASS' in l or 'TAG ' in l or 'mismatch' in l.lower() or 'fatal' in l.lower())[-3000:])
    if result.returncode or 'PASS GPU tag generation' not in output or not re.search(r'PASS GPU readback checks=\s*24', output):
        raise SystemExit('GPU tag generation comparison failed')


if __name__ == '__main__':
    main(8)
    main(2)  # wraps every third clear, exercising the tag clear
