#!/usr/bin/env python3
"""Run the selected native SPU's expanded-context diagnostic on the Laptop.

This uses real SPU/gauss/RAM-controller RTL and portable RAM primitives. It
is not a full-console simulation or proof that all SPU phases are serialized.
"""
import argparse
import hashlib
import json
import re
import resource
import shutil
import subprocess
from pathlib import Path

from prepare_spu_sample_save import transform
from run_current_cpu_regfile import ADDRESS_MODEL

ROOT = Path(__file__).resolve().parents[1]
SPU_SHA = 'a2cff2864840f7fa1bbb33d0581318406a695c3c53095e58e6ff3d1a43ca0521'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(*, spu_transform=None, bench_source=None, extra_inputs=(),
         fault_choices=range(8), guest_mutation=None, supports_abort=False,
         scan_mode='indexed', bench_transform=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--fault', type=int, choices=fault_choices, default=0)
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--operations', type=int, default=1500)
    p.add_argument('--samples', type=int, default=256)
    p.add_argument('--guest-equivalence', action='store_true')
    p.add_argument('--guest-routing-fault', action='store_true')
    if supports_abort:
        p.add_argument('--abort-words', type=int, choices=range(16), default=0)
    p.add_argument('--ghdl', default=str(ROOT / 'build/tools/oss-cad-suite/bin/ghdl'))
    args = p.parse_args()
    if args.operations < 1000 or args.samples < 64:
        p.error('coverage requires at least 1000 operations and 64 samples')
    if args.guest_routing_fault and not args.guest_equivalence:
        p.error('routing mutation requires --guest-equivalence')
    if args.guest_equivalence and args.fault:
        p.error('ordinary guest equivalence and restore faults are separate runs')
    abort_words = getattr(args, 'abort_words', 0)
    if abort_words and (args.guest_equivalence or args.fault):
        p.error('abandoned-read recovery requires an unmutated restore run')
    manifest = json.loads((args.project / 'fit-inputs.json').read_text())
    ledger = {}

    def bind(path):
        path = path.resolve()
        ledger[str(path)] = sha(path)
        return path

    def selected(name):
        rows = [x for x in manifest['quartus_sources'] if Path(x['path']).name == name]
        if len(rows) != 1:
            raise ValueError('Expected one selected ' + name)
        path = Path(rows[0]['path'])
        if sha(path) != rows[0]['sha256']:
            raise ValueError('Selected source changed: ' + str(path))
        return bind(path)

    spu = selected('spu.vhd')
    if sha(spu) != SPU_SHA:
        raise ValueError('Requires exact unselected PXS4 full-audio SPU')
    spuram = selected('spu_ram.vhd')
    gauss = selected('spu_gauss.vhd')
    fifo = selected('SyncFifoFallThrough.vhd')
    bind(args.project / 'fit-inputs.json')
    for source in (__file__, ROOT / 'scripts/prepare_spu_sample_save.py',
                   ROOT / 'scripts/run_current_cpu_regfile.py', *extra_inputs):
        bind(Path(source))
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_dir = out / 'inputs'
    source_dir.mkdir()
    evidence = out / 'source-evidence'
    for name, digest in list(ledger.items()):
        target = evidence / name.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name, target)
        if sha(target) != digest:
            raise ValueError('Input changed while preserving evidence: ' + name)
        bind(target)

    def copy(path, name=None):
        bind(path)
        target = source_dir / (name or path.name)
        if target.exists():
            raise ValueError('Input filename collision: ' + str(target))
        shutil.copyfile(path, target)
        bind(target)
        return target

    # Separate basenames prevent GHDL LLVM object collisions across libraries.
    raw = ROOT / 'references/PSX_MiSTer/sim/system/src/mem'
    analyzes = []
    for lib in ('mem', 'work'):
        for name in ('dpram.vhd', 'RamMLAB.vhd'):
            analyzes.append((lib, copy(raw / name, lib + '_' + name)))
    text = (raw / 'dpram.vhd').read_text()
    text = text[:text.index('end rtl;') + len('end rtl;')]
    text = re.sub(r'\bdpram\b', 'pocket_dpram_sdp', text, flags=re.I)
    model = source_dir / 'sdp.vhd'
    model.write_text(text[:text.index('architecture rtl of')] + ADDRESS_MODEL)
    bind(model)
    analyzes += [('mem', model), ('mem', copy(raw / 'SyncFifo.vhd')),
                 ('mem', copy(fifo)), ('work', copy(gauss))]
    anchor = 'LIBRARY altera_mf;\nUSE altera_mf.altera_mf_components.all;'
    text = spuram.read_text()
    if text.count(anchor) != 1:
        raise ValueError('SPU RAM vendor-context anchor changed')
    ram_model = source_dir / 'spu_ram.vhd'
    ram_model.write_text(text.replace(anchor, '-- unused vendor context omitted'))
    bind(ram_model)
    dut = source_dir / 'spu_t.vhd'
    text = transform(spu.read_text()) if spu_transform is None else spu_transform(spu.read_text(), args.fault)
    if args.guest_routing_fault:
        anchor, replacement = guest_mutation or ('else adpcm_ram_data_a(bank);', 'else not adpcm_ram_data_a(bank);')
        if text.count(anchor) != 1:
            raise ValueError('Ordinary sample routing mutation anchor changed')
        text = text.replace(anchor, replacement)
    dut.write_text(re.sub(r'\bspu\b', 'spu_t', text))
    bind(dut)
    bench = copy(bench_source or ROOT / 'sim/pocket/tb_spu_full_context.vhd')
    if bench_transform:
        bench.write_text(bench_transform(bench.read_text()))
        bind(bench)
    control = None
    if args.guest_equivalence:
        control = source_dir / 'spu_reference.vhd'
        control.write_text(re.sub(r'\bspu\b', 'spu_reference', spu.read_text()))
        bind(control)
        text = bench.read_text().replace('ub : entity work.spu_t', 'ub : entity work.spu_reference')
        text = text.replace('    memory : for i in 0 to 1 generate', '    bb <= ba;\n    memory : for i in 0 to 1 generate')
        text = text.replace('if a.tick/=b.tick or a.irq/=b.irq or a.timeout/=b.timeout or a.dmareq/=b.dmareq or\n',
                            'if a.rd/=b.rd or a.dma/=b.dma or a.tick/=b.tick or a.irq/=b.irq or a.timeout/=b.timeout or a.dmareq/=b.dmareq or\n')
        anchor = '        -- A gets its traffic, then B gets different traffic'
        if text.count(anchor) != 1:
            raise ValueError('Ordinary guest bench anchor changed')
        text = text[:text.index(anchor)] + '''        compare <= true;
        traffic(ba, SEED);
        for i in 1 to 8192 loop tick; end loop;
        compare <= false;
        tick;
        report "ordinary guest mismatches=" & natural'image(mismatches);
'''
        condition = 'mismatches > 0' if args.guest_routing_fault else 'mismatches = 0'
        text += f'''        assert {condition} report "Ordinary guest equivalence/mutation failed" severity failure;
        report "PASS ordinary guest equivalence";
        finish;
    end process;
end architecture;
'''
        bench.write_text(text)
        ledger[str(bench)] = sha(bench)
    analyzes.append(('work', ram_model))
    if control is not None:
        analyzes.append(('work', control))
    analyzes += [('work', dut), ('work', bench)]
    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    flags = ['--std=08', '-frelaxed']
    with (out / 'compile.log').open('w') as log:
        for lib, source in analyzes:
            subprocess.run([args.ghdl, '-a', *flags, '--work=' + lib, str(source)],
                           cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run([args.ghdl, '-e', *flags, 'tb_spu_full_context'], cwd=out,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    command = [str(out / 'tb_spu_full_context'), f'-gSEED={args.seed}',
               f'-gOPERATIONS={args.operations}', f'-gSAMPLES={args.samples}',
               f'-gFAULT={args.fault}', '--assert-level=error', '--ieee-asserts=disable']
    if supports_abort:
        command.insert(1, f'-gABORT_WORDS={abort_words}')
    with (out / 'run.log').open('w') as log:
        rc = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                            timeout=3600).returncode
    drift = [path for path, digest in ledger.items() if sha(Path(path)) != digest]
    expected = ('PASS ordinary guest equivalence' if args.guest_equivalence else
                'PASS SPU save tap fault caught' if args.fault else 'PASS SPU save tap')
    passed = rc == 0 and not drift and expected in (out / 'run.log').read_text()
    report = dict(passed=passed, exit_code=rc, fault=args.fault, command=command,
                  scan_mode=scan_mode, abort_words=abort_words,
                  guest_equivalence=args.guest_equivalence, guest_routing_fault=args.guest_routing_fault,
                  inputs_changed=drift, sha256=ledger,
                  selected=False, hardware_verified=False, all_machine_state_covered=False,
                  scope='Native SPU, portable RAM models, address-derived external RAM; no settling')
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS' if passed else 'FAIL', out / 'report.json')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
