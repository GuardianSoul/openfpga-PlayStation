#!/usr/bin/env python3
"""Verify the Pocket output FIR against integer vectors and spectral checks.

No guest software/data required. Does not validate a clock-crossing bridge,
physical timing, resource fit or Pocket sound output.
"""
import argparse
import json
import math
from pathlib import Path
import random
import subprocess
import tempfile

import numpy as np

from generate_pocket_audio_coeffs import generate, PHASES, Q, TAPS
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def load_coefficients(path):
    raw = np.array([int(line, 16) for line in path.read_text().splitlines()], dtype=np.int64)
    return np.where(raw == 0x20000, 131072, np.where(raw & 0x20000, raw-0x40000, raw)).reshape(161, 128)


def quality(rows):
    # Coherent one-tenth-second tones; every 160-phase cycle is covered.
    # This measures reconstructed output, including resampling images, rather
    # than mistaking a per-phase input-Nyquist response for an output filter.
    count = 4800
    n = np.arange(count, dtype=np.int64)
    times = n*147
    phase = times % 160
    newest = times // 160
    tones = []
    for frequency in (1000, 10000, 18000, 20000, 21500, 22000):
        samples = np.sin(2*np.pi*frequency/44100*(newest[:, None]-np.arange(128)[None, :]))
        output = np.sum(samples*rows[phase]/131072, axis=1)
        spectrum = np.abs(np.fft.rfft(output))*2/count
        bin_index = frequency//10
        gain = spectrum[bin_index]
        spectrum[bin_index] = 0
        tones.append({'frequency_hz':frequency, 'gain_db':20*math.log10(gain),
                      'largest_image_or_spur_dbc':20*math.log10(max(spectrum)/gain)})
    sums = []
    for fine in (0, 1, 32768, 65535):
        sums.extend(np.sum(rows[:-1]+((rows[1:]-rows[:-1])*fine+32768)//65536, axis=1).tolist())
    ripple = max(abs(value/131072-1) for value in sums)
    # Check fractional-delay gain/phase at every bank boundary and representative
    # fine positions too, independently of the exact nominal phase sequence.
    transfer = []
    tap_positions = np.arange(128)
    for frequency in (1000,10000,18000,20000):
        omega = 2*np.pi*frequency/44100
        gains, phase_errors = [], []
        for fine in (0,1,32768,65535):
            coeffs = rows[:-1]+((rows[1:]-rows[:-1])*fine+32768)//65536
            response = coeffs @ np.exp(-1j*omega*tap_positions)/131072
            ideal = np.exp(1j*omega*((np.arange(160)+fine/65536)/160-64))
            gains.extend((20*np.log10(np.abs(response))).tolist())
            phase_errors.extend(np.angle(response/ideal).tolist())
        transfer.append({'frequency_hz':frequency,'maximum_gain_error_db':max(abs(v) for v in gains),
                         'maximum_fractional_delay_phase_error_rad':max(abs(v) for v in phase_errors)})
    passed = (max(abs(t['gain_db']) for t in tones[:4]) < 0.02
              and max(t['largest_image_or_spur_dbc'] for t in tones[:4]) < -70
              and ripple < 0.0005 and max(t['maximum_gain_error_db'] for t in transfer)<0.02
              and max(t['maximum_fractional_delay_phase_error_rad'] for t in transfer)<0.001)
    return {'passed':passed, 'coherent_samples':count, 'tones':tones,
            'fine_interpolation_dc_gain_error_max':ripple,'all_phase_fine_transfer':transfer,
            'passband_limit_hz':20000, 'passband_gain_limit_db':0.02,
            'image_spur_limit_dbc':-70, 'scope':'Floating input, integer coefficients; no clock servo or 16-bit output quantization'}


def write_vectors(path, rows):
    rng = random.Random(64044100)
    history = [(0,0)]*128
    records = []
    outputs = 0
    def reset():
        history[:] = [(0,0)]*128
        records.append('2 0 0 0\n')
    def append(left, right):
        history.insert(0, (left,right)); history.pop()
        records.append(f'0 {((right & 65535)<<16)|(left & 65535):08x} 0 0\n')
    def result(phase, fine):
        nonlocal outputs
        coeffs = rows[phase]+((rows[phase+1]-rows[phase])*fine+32768)//65536
        values = []
        for channel in range(2):
            total = sum(int(c)*h[channel] for c,h in zip(coeffs,history))
            values.append(max(-32768,min(32767,(total+65536)//131072)))
        frame = ((values[1] & 65535)<<16)|(values[0] & 65535)
        records.append(f'1 {phase:x} {fine:x} {frame:08x}\n'); outputs += 1
    # Unity sentinel, ring wrapping, independent channels and saturation.
    for pattern in ('impulse','constant','alternating','random'):
        reset()
        for i in range(260):
            if pattern == 'impulse': pair = (32767,-32768) if i == 0 else (0,0)
            elif pattern == 'constant': pair = (32767,-32768)
            elif pattern == 'alternating': pair = (32767 if i % 2 else -32768, 1234)
            else: pair = (rng.randrange(-32768,32768),rng.randrange(-32768,32768))
            append(*pair)
            result(i % 160, (0,1,32768,65535)[i % 4])
            if i % 33 == 0: result(0,0)
    # All coarse phases at all fine boundary/rounding cases on full histories.
    for phase in range(160):
        for fine in (0,1,32767,32768,65534,65535): result(phase,fine)
    # Exact nominal-ratio sequence, preserving zero-input-advance frames.
    reset(); position = -1
    for n in range(1200):
        now, phase = divmod(n*147,160)
        while position < now:
            position += 1
            append(round(29000*math.sin(2*math.pi*1000*position/44100)),
                   round(31000*math.sin(2*math.pi*19000*position/44100)))
        result(phase,0)
    path.write_text(''.join(records))
    return outputs


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime: raise ValueError('Icarus required')
    parent = ROOT/'build/sim/pocket-audio-sinc'; parent.mkdir(parents=True,exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    coefficient_record = generate(work/'coefficients')
    coefficient_path = Path(coefficient_record['path'])
    compact_path = Path(coefficient_record['compact_path'])
    rows = load_coefficients(coefficient_path)
    spectral = quality(rows)
    vectors = work/'vectors.txt'; expected_outputs = write_vectors(vectors,rows)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl', type=Path, default=ROOT/'rtl/audio/psx_audio_sinc.sv', help='converter source (e.g. rtl/audio/psx_audio_sinc_mac.sv)')
    rtl = parser.parse_args().rtl.resolve(); bench = ROOT/'sim/pocket/tb_audio_sinc.sv'
    protected = {str(path):digest(path) for path in (rtl,bench,Path(__file__),ROOT/'scripts/generate_pocket_audio_coeffs.py')}
    def run(name,source):
        binary = work/(name+'.vvp')
        command = [compiler,'-g2012','-s','tb_audio_sinc',
                   '-DCOEFFICIENT_FILE="'+str(compact_path)+'"','-o',str(binary),str(source),str(bench)]
        with (work/(name+'-build.log')).open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        result = subprocess.run([runtime,str(binary),'+VECTORS='+str(vectors)],capture_output=True,text=True,timeout=120)
        output = result.stdout+result.stderr
        log = work/(name+'.log'); log.write_text(output)
        print(name+': '+output,end='',flush=True)
        return {'exit_code':result.returncode,'stdout':output,'log_sha256':digest(log),
                'passed':result.returncode==0 and f'{expected_outputs} stereo results' in output}
    original = run('original',rtl)
    mutations = []
    for name,before,after in (
        ('wrong-tap','newest - issued[6:0]','newest + issued[6:0]'),
        ('wrong-phase','8\'d159 - phase','8\'d158 - phase'),
        ('wrong-channel','history_3[31:16]','history_3[15:0]')):
        text = rtl.read_text()
        if before not in text: raise ValueError('Mutation anchor missing')
        source = work/(name+'.sv'); source.write_text(text.replace(before,after))
        result = run(name,source); result['name']=name
        result['rejected']=result['exit_code']!=0 and 'Audio result' in result['stdout']
        mutations.append(result)
    identity = all(digest(Path(p))==sha for p,sha in protected.items())
    report = {'passed':original['passed'] and spectral['passed'] and identity and all(m['rejected'] for m in mutations),
              'source_identity_verified':identity,'protected_sha256':protected,'coefficients':coefficient_record,
              'vectors_sha256':digest(vectors),'stereo_results':expected_outputs,'spectral':spectral,
              'rtl':original,'mutations':mutations,'fit_verified':False,'timing_verified':False,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Spectral:',json.dumps(spectral),flush=True); print(work,flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__=='__main__': main()
