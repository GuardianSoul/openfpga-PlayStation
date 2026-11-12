#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""AREA PROBE ONLY (not a build, not qualified): lean-audio SPU save port.

Upper bound of what the SPU save port costs for seamless sound after a load. Made dead:
decoded-sample bank save/restore (SS 128-255 + page register), reverb work/history RAM
save/restore (SS 48-62), the DAC latch word (SS 7), and the ring fields that only shape
the sound right after a load (reverb d1-d4, down-sample pair, part A/B, rvLastValid,
cd_next L/R). Kept: voice records, volume and envelope-counter RAM words, and a 7-word
ring of capture position, transfer address, reverb address/side, noise LFSR,
ADSR/repeat write latches, irqSaved, ENDX, IRQ9, CURVOL L/R.
The firmware and psx_save_exp1 are left as they are, so the result cannot run.
Usage: probe_spu_lean_audio.py SOURCE --output OUT
"""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

KEEP = [('capturePosition', 10, 'unsigned'), ('ramTransferAddr', 19, 'unsigned'), ('reverbCurrentAddress', 18, 'unsigned'),
        ('reverbRight', 1, 'bit'), ('noiseLevel', 16, 'unsigned'), ('noiseCnt', 32, 'unsigned'),
        ('voice_adsrWrite', 24, 'slv'), ('voice_repeatWrite', 24, 'slv'), ('irqSaved', 1, 'bit'),
        ('ENDX', 32, 'slv'), ('IRQ9', 1, 'bit'), ('CURVOL_L', 16, 'slv'), ('CURVOL_R', 16, 'slv')]
# --exact-voices (probe 2): also keep the decoded-sample banks and the pending CD sample, so
# voices, capture buffers, pitch modulation and ENDX/IRQ timing stay exact; only the reverb
# input filter history, the reverb work RAM and the DAC word are dropped.
EXACT_VOICES = False
BITS = WORDS = PAD = 0


def layout():
    global BITS, WORDS, PAD
    if EXACT_VOICES and KEEP[-1][0] != 'cd_next_right':
        KEEP.extend([('cd_next_left', 17, 'signed'), ('cd_next_right', 17, 'signed')])
    BITS = sum(w for _, w, _ in KEEP); WORDS = -(-BITS // 32); PAD = WORDS * 32 - BITS
    if PAD == 0:
        WORDS += 1; PAD = 32


def once(text, old, new, regex=False):
    n = len(re.findall(old, text, flags=re.S)) if regex else text.count(old)
    if n != 1:
        raise ValueError(f'expected one match, found {n}: {old[:70]!r}')
    return re.sub(old, lambda m: new, text, flags=re.S) if regex else text.replace(old, new)


def lean(t):
    top = WORDS * 32 - 1
    t = once(t, 'signal global_scan_pad : std_logic_vector(5 downto 0)', f'signal global_scan_pad : std_logic_vector({PAD - 1} downto 0)')
    t = once(t, 'signal global_scan : std_logic_vector(511 downto 0);', f'signal global_scan : std_logic_vector({top} downto 0);')
    t = once(t, 'signal global_scan_next : std_logic_vector(511 downto 0);', f'signal global_scan_next : std_logic_vector({top} downto 0);')
    t = once(t, 'signal global_scan_index : integer range 0 to 15 := 0;', f'signal global_scan_index : integer range 0 to {WORDS - 1} := 0;')
    cat = ' & '.join(['global_scan_pad'] + [n if k in ('slv', 'bit') else f'std_logic_vector({n})' for n, _, k in reversed(KEEP)])
    t = once(t, r'   global_scan <= global_scan_pad & [^\n]*;\n', f'   global_scan <= {cat};\n', regex=True)
    t = once(t, 'SS_Adr >= 32 and SS_Adr <= 47 else', f'SS_Adr >= 32 and SS_Adr <= {31 + WORDS} else')
    t = t.replace('global_scan(511 downto 32)', f'global_scan({top} downto 32)')
    lines, lo = [], 0
    for n, w, k in KEEP:
        sl = f'global_scan_next({lo})' if k == 'bit' else f'global_scan_next({lo + w - 1} downto {lo})'
        lines.append(f'            {n} <= {k + "(" + sl + ")" if k in ("unsigned", "signed") else sl};\n'); lo += w
    lines.append(f'            global_scan_pad <= global_scan_next({top} downto {lo});\n')
    t = once(t, r'            capturePosition <= unsigned\(global_scan_next\(9 downto 0\)\);\n.*?global_scan_pad <= global_scan_next\(511 downto 506\);\n',
             ''.join(lines), regex=True)
    t = once(t, 'if global_scan_index = 15 then', f'if global_scan_index = {WORDS - 1} then')
    t = once(t, "            if SS_Adr >= 48 and SS_Adr <= 62 then\n               rvw_we := '1'; rvw_addr := to_integer(SS_Adr(3 downto 0)); rvw_data := SS_DataWrite(15 downto 0);\n            end if;\n", '')
    if EXACT_VOICES:
        t = once(t, "            if SS_Adr = 7 then sound_out_left <= SS_DataWrite(15 downto 0); sound_out_right <= SS_DataWrite(31 downto 16); end if;\n", '')
        t = once(t, "   rv_work_raddr <= to_integer(SS_Adr(3 downto 0)) when save_spu_quiet = '1' and SS_Adr >= 48 and SS_Adr <= 62 else rv_work_engine_raddr;",
                 '   rv_work_raddr <= rv_work_engine_raddr;')
        t = once(t, '            elsif SS_Adr = 7 then\n               SS_DataRead <= sound_out_right & sound_out_left;\n', '')
        t = once(t, '            elsif SS_Adr >= 48 and SS_Adr <= 62 then\n               SS_DataRead <= x"0000" & rv_work_q;\n', '')
        return t
    t = once(t, "            if SS_Adr = 63 then sample_save_page <= unsigned(SS_DataWrite(1 downto 0)); end if;\n", '')
    t = once(t, "            if SS_Adr = 7 then sound_out_left <= SS_DataWrite(15 downto 0); sound_out_right <= SS_DataWrite(31 downto 16); end if;\n", '')
    t = once(t, "   sample_save_scan <= '1' when save_spu_quiet = '1' and SS_Adr(8 downto 7) = \"01\" else '0';", "   sample_save_scan <= '0';")
    t = once(t, "   rv_work_raddr <= to_integer(SS_Adr(3 downto 0)) when save_spu_quiet = '1' and SS_Adr >= 48 and SS_Adr <= 62 else rv_work_engine_raddr;",
             '   rv_work_raddr <= rv_work_engine_raddr;')
    t = once(t, r'            elsif SS_Adr = 7 then\n.*?               SS_DataRead <= x"0000" & rv_work_q;\n', '', regex=True)
    return t


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    layout()
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    qsf = (output / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if 'set_global_assignment' in l and '/spu.vhd"' in l})
    assert len(hits) == 1 and Path(hits[0]).is_relative_to(output), hits
    p = Path(hits[0]); p.write_text(lean(p.read_text()))
    current = inputs(output)
    changed = {a['path'] for a, b in zip(result['quartus_sources'], current) if a != b}
    assert changed == {str(p)}, changed
    hold_guard(output, current)
    result['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    (output / 'PROBE-ONLY.txt').write_text('Area probe: lean-audio SPU save port. Not runnable, never assemble.\n')
    return dict(source=str(source), changed=sorted(changed), ring_bits=BITS, ring_words=WORDS)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--exact-voices', action='store_true')
    a = ap.parse_args()
    EXACT_VOICES = a.exact_voices
    print(prepare(a.source, a.output))
