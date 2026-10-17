#!/usr/bin/env python3
"""Check spu-cd-queue (scripts/prepare_spu_cd_queue.py) against the pinned SPU.

Both SPUs carry the Pocket completed-sample valid port. Two runs of
sim/reference/tb_spu_cd_queue.sv: never late (24 voices + CD, ce gaps): the
completed-sample streams must be identical; late (SPU RAM latency spikes, no
sound_timeout): the original must skip/repeat CD ticks and the queue none.
--fault plants a queue that pops one tick early and must fail."""
import argparse
from pathlib import Path
import re
import subprocess

from prepare_pocket_audio_stream import capture_spu_text
from prepare_spu_cd_queue import cd_queue_text
from prepare_system_verilator import prepare
from tool_paths import ROOT, find_tool
from ghdl_verilog import normalize_generated_verilog

PORTS = ('clk1x clk2x clk2xIndex ce reset SPUon SPUIRQTrigger useSDRAM REPRODUCIBLESPUIRQ REPRODUCIBLESPUDMA '
         'REVERBOFF cpuPaused spu_tick cd_left cd_right irqOut sound_timeout sound_out_left sound_out_right '
         'sound_out_valid bus_addr bus_dataWrite bus_read bus_write bus_dataRead spu_dmaRequest dma_read '
         'dma_readdata dma_write dma_writedata sdram_dataWrite sdram_Adr sdram_be sdram_rnw sdram_ena '
         'sdram_dataRead sdram_done mem_request mem_BURSTCNT mem_ADDR mem_DIN mem_BE mem_WE mem_RD mem_ack '
         'mem_DOUT mem_DOUT_READY SS_reset loading_savestate SS_DataWrite SS_Adr SS_wren SS_rden SS_DataRead '
         'SS_idle SS_RAM_dataWrite SS_RAM_Adr SS_RAM_request SS_RAM_rnw SS_RAM_dataRead SS_RAM_done').split()


def instance(module, header, suffix):
    ports = {name.lower(): (name, size or '') for size, name in
             re.findall(r'\b(?:input|output)\s+(\[\d+:\d+\]\s+)?(\w+)', header)}
    outputs = {name.lower() for name in re.findall(r'\boutput\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
    owned = {p.lower() for p in PORTS if p.lower() in outputs} | {'sdram_dataread', 'sdram_done'}
    decl, binds = [], []
    for port in PORTS:
        name, size = ports[port.lower()]
        net = port + ('_' + suffix if port.lower() in owned else '')
        if port.lower() in owned: decl.append(f'logic {size}{net};')
        binds.append(f'.{name}({net})')
    mem = (f'spu_lat_mem mem_{suffix}(.clk(clk1x),.ena(sdram_ena_{suffix}),.rnw(sdram_rnw_{suffix}),'
           f'.adr(sdram_Adr_{suffix}),.din(sdram_dataWrite_{suffix}),.latency(latency),'
           f'.done(sdram_done_{suffix}),.dout(sdram_dataRead_{suffix}));')
    return '\n'.join(decl) + f'\n{module} spu_{suffix}(' + ','.join(binds) + ');\n' + mem + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fault', action='store_true')
    args = parser.parse_args()
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL synthesis and Verilator are required')
    directory = ROOT / ('build/sim/spu-cd-queue' + ('-fault' if args.fault else ''))
    directory.mkdir(parents=True, exist_ok=True)
    manifest = prepare(directory / 'sources')
    original = Path(next(s for s in manifest['sources'] if Path(s['path']).name == 'spu.vhd')['path'])
    base = capture_spu_text(original.read_text())
    queue = cd_queue_text(base)
    if args.fault:
        planted = queue.replace('if (state = CAPTURE3 and cdq_count > 0) then\n               cdq_rp    <= cdq_rp + 1;',
                                'if (state = REVERB_READ2 and cdq_count > 0) then\n               cdq_rp    <= cdq_rp + 1;')
        if planted == queue: raise SystemExit('fault anchor changed')
        queue = planted
    variants = {'base': base, 'queue': queue}
    files = []
    for suffix, text in variants.items():
        text = text.replace('entity spu is', f'entity spu_{suffix} is').replace('architecture arch of spu is', f'architecture arch of spu_{suffix} is')
        path = directory / f'spu_{suffix}.vhd'; path.write_text(text); files.append(path)
    with (directory / 'build.log').open('w') as log:
        for s in manifest['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={s["library"]}', s['path']], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        for path in files:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=work', str(path)], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        instances, verilog = [], []
        seen = set()
        for suffix in variants:
            out = directory / f'spu_{suffix}.v'
            with out.open('w') as handle:
                subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog', f'spu_{suffix}'], cwd=directory, stdout=handle, stderr=log, check=True, timeout=180)
            normalize_generated_verilog(out)
            text = out.read_text()
            module = re.search(rf'^module (spu_{suffix}(?:_Barch[^\s]*)?)[ \t]*\n', text, re.M)[1]
            header = text[text.index('module ' + module):].split(');', 1)[0]
            instances.append(instance(module, header, suffix))
            chunks = re.findall(r'^module (\w+)(.*?^endmodule\s*$)', text, re.M | re.S)
            out.write_text('\n'.join('module ' + n + b for n, b in chunks if n not in seen))
            seen |= {n for n, _ in chunks}
            verilog.append(str(out))
        bench = directory / 'tb_spu_cd_queue.sv'
        bench.write_text((ROOT / 'sim/reference/tb_spu_cd_queue.sv').read_text().replace('INSTANCES', '\n'.join(instances)))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_spu_cd_queue',
                        '--Mdir', str(directory / 'obj'), *verilog, str(ROOT / 'sim/reference/mister_memory_models.sv'), str(bench)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    passed = True
    for spikes in (0, 1):
        result = subprocess.run([str(directory / 'obj/Vtb_spu_cd_queue'), f'+spikes={spikes}'], cwd=directory, capture_output=True, text=True, timeout=1800)
        output = result.stdout + result.stderr
        (directory / f'run-spikes{spikes}.log').write_text(output)
        print(output, end='', flush=True)
        passed &= result.returncode == 0 and 'PASS SPU cd-queue' in output
    if not passed: raise SystemExit('spu-cd-queue check FAILED')


if __name__ == '__main__':
    main()
