#!/usr/bin/env python3
"""Exercise RAM-backed coherent diagnostic snapshots across asynchronous clocks.

Compiles frozen copies in a fresh directory. Also requires three deliberately
broken RTL copies to fail the coherence/held-bank assertions. This is a
protocol simulation, not metastability, fitted timing, or Pocket qualification.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    parent = ROOT/'build/sim/status-cdc'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    originals = [ROOT/'rtl/pocket/psx_status_cdc.sv', ROOT/'sim/pocket/tb_psx_status_cdc.sv',
        Path(__file__).resolve(), ROOT/'scripts/tool_paths.py', ROOT/'scripts/prepare_resource_targets.py']
    archive = {}; protected = {}
    for i, original in enumerate(originals):
        sha = digest(original)
        copy = work/'input-archive'/str(i)/original.name
        copy.parent.mkdir(parents=True)
        shutil.copyfile(original, copy)
        if digest(copy) != sha:
            raise ValueError('Status CDC input changed during archive: '+str(original))
        archive[str(original)] = dict(path=str(copy), sha256=sha)
        protected[str(original)] = sha
        protected[str(copy)] = sha
    rtl, bench = [Path(archive[str(p)]['path']) for p in originals[:2]]
    compiler, simulator = find_tool('iverilog'), find_tool('vvp')
    runs = []

    def run(name, source, sh=14.76255, dh=6.734, phase=0, expected_fault=None):
        folder = work/name
        folder.mkdir()
        binary = folder/'tb.vvp'
        command = [compiler, '-g2012', '-s', 'tb', f'-Ptb.SH={sh}', f'-Ptb.DH={dh}',
            f'-Ptb.DP={phase}', '-o', str(binary), str(source), str(bench)]
        with (folder/'compile.log').open('w') as log:
            compiled = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        result = dict(name=name, rtl_sha256=digest(source), compiler_command=command,
            compiler_exit_code=compiled.returncode, accepted=False, expected_fault=expected_fault)
        if compiled.returncode == 0:
            log = folder/'run.log'
            with log.open('w') as output:
                completed = subprocess.run([simulator, str(binary)], stdout=output,
                    stderr=subprocess.STDOUT, timeout=60)
            text = log.read_text()
            result.update(exit_code=completed.returncode, output=text, log_sha256=digest(log),
                binary_sha256=digest(binary))
            result['accepted'] = (completed.returncode != 0 and expected_fault in text
                if expected_fault else completed.returncode == 0 and text.count('PASS STATUS_CDC') == 1
                and 'FATAL' not in text)
        runs.append(result)
        print(name, 'PASS' if result['accepted'] else 'FAIL', flush=True)

    profiles=[('host-fast', 14.76255, 6.734, 0),
            ('shifted', 14.76255, 6.734, 7.1), ('source-fast', 6.734, 14.76255, 2.3),
            ('slow-dest', 6.734, 31.7, 5.2)]
    for name, sh, dh, phase in profiles:
        run(name, rtl, sh, dh, phase)
    text = rtl.read_text()
    # Mapped RAM parameters: ADDRESS_REG_B=CLOCK1, OUTDATA_REG_B=UNREGISTERED.
    # This separate backend exposes a write to the currently read bank even
    # without another capture. Correct ping-pong ownership must prevent it.
    anchor='    always @(posedge dst_clk) if(capture) ram_data<=snapshots[request_sync];'
    if text.count(anchor)!=1:
        raise ValueError('Status RAM inference-model anchor changed')
    addressed=text.replace(anchor,'''    reg read_bank;
    always @(posedge dst_clk) if(capture) read_bank<=request_sync;
    always @* ram_data=snapshots[read_bank];''')
    memory_model=work/'address-registered-ram.sv';memory_model.write_text(addressed)
    protected[str(memory_model)]=digest(memory_model)
    for name,sh,dh,phase in profiles:
        run('mapped-model-'+name,memory_model,sh,dh,phase)
    faults = [
        ('overwrite', 'if(publish) snapshots[!request]<=src_data;',
            'if(!src_reset) snapshots[publish ? !request : request]<=src_data;',
            'Outstanding status RAM overwritten'),
        ('live-bus', 'ram_data<=snapshots[request_sync];',
            'ram_data<=src_data;', 'Torn or wrong status snapshot')]
    for name, before, after, marker in faults:
        if text.count(before) != 1:
            raise ValueError('Status CDC fault anchor changed: '+name)
        source = work/('fault-'+name+'.sv')
        source.write_text(text.replace(before, after))
        protected[str(source)] = digest(source)
        run('fault-'+name, source, expected_fault=marker)
    # Corrupt the previously delivered bank while a new bank is pending.
    # This specifically exercises the unregistered mapped RAM output, rather
    # than relying only on RTL's registered-read view of the memory.
    bad=addressed.replace('    assign dst_data=', '''    always @(posedge src_clk)
        if(!src_reset && !publish && request!=acknowledge && dst_valid)
            snapshots[acknowledge]<=src_data;
    assign dst_data=''',1)
    source=work/'fault-exposed-bank.sv';source.write_text(bad)
    protected[str(source)]=digest(source)
    run('fault-exposed-bank',source,sh=6.734,dh=31.7,phase=5.2,
        expected_fault='Exposed status word changed without a capture')
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    passed = identity and len(runs) == 11 and all(r['accepted'] for r in runs)
    report = dict(passed=passed, source_identity_verified=identity, source_sha256=protected,
        input_archives=archive, runs=runs, coherent_latest_status_verified=passed,
        fault_controls_rejected=passed, hardware_verified=False, timing_verified=False,
        limitations=['Intermediate source levels may coalesce; never use for pulses or lossless commands',
            'Registered-read and address-registered RAM models checked; physical RAM timing still separate',
            'Both domains must share asynchronous reset; local release takes three clocks',
            'No analog metastability or physical flight/skew/reset qualification in RTL simulation'])
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(work/'report.json', flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
