#!/usr/bin/env python3
"""Check selected Native identity LUTs before a fresh physical fit.

Vendor primitive truth, exact selected DMA lockstep, and selected CAS3/SPU-
first SDRAM transactions against the existing independent chip/golden model.
Compares SDRAM pin/public-output traces and access cycles with the unmodified
source. Ideal digital clocks; this is not routed or Pocket qualification.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_native_hold_cells import REVIEWED, dma_text, sdram_text
from prepare_resource_targets import UPSTREAM, digest
from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool


TRACE = '''    integer hold_trace = $fopen("pins-public.trace", "w");
    always @(negedge clk3) begin
        #0.001;
        $fdisplay(hold_trace, "%h %h", {
            ours.dq, ours.a, ours.ba, ours.dqml, ours.dqmh,
            ours.ncs, ours.nwe, ours.nras, ours.ncas, ours.cke, ours.chip_clk
        }, {ours.ch1_dout, ours.ch1_dout32, ours.ch2_dout, ours.ch3_dout,
            ours.ch1_ready, ours.ch2_ready, ours.ch3_ready, ours.ram_idle,
            ours.cache_wr, ours.cache_data, ours.cache_addr,
            ours.dma_wr, ours.dma_rp, ours.dma_data, ours.fifo_read});
    end
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('parent', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    parent, work = args.parent.resolve(), args.output.resolve()
    if work.exists() or not work.is_relative_to(ROOT/'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(work/'report.json')], cwd=ROOT).returncode:
        raise ValueError('Fresh ignored check directory required')
    original_inputs = inputs(parent)
    sources = {}
    for name, sha in REVIEWED.items():
        matches = [r for r in original_inputs if Path(r['path']).name == name]
        if len(matches) != 1 or matches[0]['sha256'] != sha:
            raise ValueError('Unreviewed selected source '+name)
        sources[name] = Path(matches[0]['path'])
    work.mkdir(parents=True)
    candidate = work/'candidate'
    candidate.mkdir()
    (candidate/'dma.vhd').write_text(dma_text(sources['dma.vhd'].read_text()))
    (candidate/'psx_sdram_ctrl.sv').write_text(sdram_text(sources['psx_sdram_ctrl.sv'].read_text()))
    leaf = candidate/'psx_hold_cell.sv'
    shutil.copyfile(ROOT/'rtl/pocket/psx_hold_cell.sv', leaf)
    tools = {n: find_tool(n) for n in ('ghdl', 'iverilog', 'vvp', 'quartus_sh')}
    if not all(tools.values()):
        raise ValueError('GHDL, Icarus and installed Quartus model required')
    atoms = Path(tools['quartus_sh']).parent.parent/'eda/sim_lib/cyclonev_atoms.v'
    runs = []

    def execute(command, directory, label, timeout=300):
        log = directory/(label+'.log')
        with log.open('w') as f:
            p = subprocess.run(list(map(str, command)), cwd=directory,
                               stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        runs.append(dict(name=label, command=list(map(str, command)),
                         exit_code=p.returncode, log=str(log), log_sha256=digest(log)))
        return p.returncode, log.read_text()

    # Compile the actual installed vendor atom, not a hand-written LUT model.
    primitive = work/'primitive'
    primitive.mkdir()
    truth = primitive/'tb.sv'
    truth.write_text('''module tb;
    reg i=0; wire o; integer k;
    psx_hold_cell dut(i,o);
    initial begin
        for(k=0;k<1000;k=k+1) begin i=k%2; #1;
            if(o !== i) $fatal(1,"Identity LUT mismatch"); end
        $display("PASS vendor identity LUT 1000 transitions"); $finish;
    end
endmodule
''')
    for fault in (False, True):
        selected = leaf
        if fault:
            selected = primitive/'wrong-mask.sv'
            selected.write_text(leaf.read_text().replace('AAAAAAAAAAAAAAAA', '5555555555555555'))
        rc, text = execute([tools['iverilog'], '-g2012', '-s', 'tb', '-o', 'tb.vvp',
                            truth, selected, atoms], primitive, 'compile-'+str(fault))
        assert rc == 0, text[-2000:]
        rc, text = execute([tools['vvp'], 'tb.vvp'], primitive, 'truth-'+str(fault))
        assert (rc != 0 and 'Identity LUT mismatch' in text) if fault else (
            rc == 0 and 'PASS vendor identity LUT' in text)

    # GHDL uses the identity relation just proven with the real vendor atom.
    # A candidate-only inverse relation must be visible to the public DMA TB.
    bench = (ROOT/'sim/reference/tb_dma_channel_ram.vhd').read_text()
    old = '         wait until falling_edge(clk1x);\n         reset <= chance(0.00005);\n'
    if bench.count(old) != 1:
        raise ValueError('Selected DMA stimulus anchor changed')
    bench = bench.replace(old,
        '         wait until rising_edge(clk1x); wait for 1 ns;\n'
        "         if o.dmaRequest = '0' and o.dmaOn = '0' then reset <= chance(0.00005); else reset <= '0'; end if;\n")
    for fault in (False, True):
        directory = work/('dma-fault' if fault else 'dma')
        directory.mkdir()
        stub = directory/'identity.vhd'
        stub.write_text('library ieee; use ieee.std_logic_1164.all;\n'
            'entity psx_hold_cell is port(i:in std_logic;o:out std_logic);end;\n'
            'architecture simulation of psx_hold_cell is begin o <= '+('not ' if fault else '')+'i;end;\n')
        altered = directory/'dma_chanram.vhd'
        altered.write_text((candidate/'dma.vhd').read_text().replace(
            'entity dma is', 'entity dma_chanram is').replace(
            'architecture arch of dma is', 'architecture arch of dma_chanram is'))
        tb = directory/'tb.vhd'
        tb.write_text(bench)
        for name in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd'):
            rc, text = execute([tools['ghdl'], '-a', '--std=08', '-frelaxed', '--work=mem', UPSTREAM/'rtl'/name], directory, 'mem-'+name)
            assert rc == 0, text[-2000:]
        for index, path in enumerate((stub, sources['dma.vhd'], altered, tb)):
            rc, text = execute([tools['ghdl'], '-a', '--std=08', '-frelaxed', path], directory, 'analyze-'+str(index))
            assert rc == 0, text[-2000:]
        rc, text = execute([tools['ghdl'], '-e', '--std=08', '-frelaxed', 'tb_dma_channel_ram'], directory, 'elaborate')
        assert rc == 0, text[-2000:]
        for seed in ((1,) if fault else (1, 2, 3)):
            rc, text = execute([tools['ghdl'], '-r', '--std=08', '-frelaxed', 'tb_dma_channel_ram',
                                '-gSEED='+str(seed), '--assert-level=error'], directory, 'run-'+str(seed))
            assert (rc != 0 and 'mismatch' in text.lower()) if fault else (
                rc == 0 and 'PASS DMA channel RAM cycles=400000' in text), text[-2000:]
            print('DMA', 'fault rejected' if fault else 'lockstep passed', seed, flush=True)

    bench = (ROOT/'sim/memory/tb_psx_sdram_ctrl.sv').read_text()
    if bench.count('    integer seed;\n') != 1:
        raise ValueError('SDRAM monitor anchor changed')
    bench = bench.replace('    integer seed;\n', '    integer seed;\n'+TRACE)
    comparisons = []
    for seed in (1, 29):
        baseline = None
        for mode in ('control', 'candidate') + (('fault4', 'fault26') if seed == 1 else ()):
            directory = work/f'sdram-{seed}-{mode}'
            directory.mkdir()
            source = sources['psx_sdram_ctrl.sv']
            if mode != 'control':
                source = directory/'psx_sdram_ctrl.sv'
                text = (candidate/'psx_sdram_ctrl.sv').read_text()
                if mode.startswith('fault'):
                    bit = int(mode[5:])
                    old = f'.i(wdata[{bit}])'
                    assert text.count(old) == 1
                    text = text.replace(old, f'.i(~wdata[{bit}])')
                source.write_text(text)
            tb, ddio = directory/'tb.sv', directory/'ddio.sv'
            tb.write_text(bench)
            ddio.write_text(ALTDDIO)
            rc, text = execute([tools['iverilog'], '-g2012', '-s', 'tb_psx_sdram_ctrl',
                '-Ptb_psx_sdram_ctrl.CAS=3', '-Ptb_psx_sdram_ctrl.SPU_FIRST=1',
                '-Ptb_psx_sdram_ctrl.TAC=5.5', '-Ptb_psx_sdram_ctrl.OPS=2000',
                '-Ptb_psx_sdram_ctrl.SEED='+str(seed), '-o', 'tb.vvp',
                tb, ddio, source, leaf, atoms, ROOT/'sim/memory/sdram_chip_model.sv'], directory, 'compile')
            assert rc == 0, text[-2000:]
            rc, text = execute([tools['vvp'], '-n', 'tb.vvp'], directory, 'run')
            passed = rc == 0 and 'RESULT errors ours 0 reference 0' in text and 'ERROR' not in text
            if mode.startswith('fault'):
                assert not passed and 'ERROR' in text, 'Undetected actual SDRAM data fault'
            else:
                assert passed, text[-2000:]
                row = dict(seed=seed, mode=mode, access_sha256=digest(directory/'ours.log'),
                           pins_public_sha256=digest(directory/'pins-public.trace'))
                comparisons.append(row)
                if mode == 'control':
                    baseline = row
                else:
                    assert all(row[k] == baseline[k] for k in ('access_sha256', 'pins_public_sha256'))
            print('SDRAM', seed, mode, 'fault rejected' if mode.startswith('fault') else 'passed', flush=True)
    assert inputs(parent) == original_inputs
    report = dict(parent=str(parent), original_inputs_unchanged=True,
        selected_sources={n:dict(path=str(p), sha256=digest(p)) for n,p in sources.items()},
        candidate_sources=[dict(path=str(p), sha256=digest(p)) for p in candidate.iterdir()],
        actual_vendor_atom=dict(path=str(atoms), sha256=digest(atoms)),
        DMA_cycles_per_positive_run=400000, DMA_positive_seeds=[1,2,3],
        SDRAM_CAS=3, SDRAM_SPU_FIRST=1, SDRAM_ops=2000, SDRAM_comparisons=comparisons,
        vendor_mask_DMA_and_two_SDRAM_faults_rejected=True, runs=runs, passed=True,
        fit_verified=False, hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print('PASS Native identity-LUT functional checks; physical qualification still required', flush=True)


if __name__ == '__main__':
    main()
