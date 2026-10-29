#!/usr/bin/env python3
"""Audio sinc coefficients: phases 0..79 and phase 80 in separate RAMs.

psx_audio_sinc stores phases 0..80 (81 x 128 = 10,368 x 18, true dual
port); 128 entries past 10,240 cost Quartus 27 M10Ks for a table whose bits
need 19. Phase 80 is read only for phase 80 (port a) or phase 79 (port b),
never both in one result, so here phases 0..79 keep the dual-port table
(10,240 deep) and phase 80 gets a 128-entry table with one read port. Both
reads stay registered on the same clock as before; a registered select picks
the coefficient. The main table loads the coefficient file (its first 10,240
words); phase 80's 128 words are written to audio-sinc-split/phase80.hex18.

Standalone fits of the converter (28 M10K, 193 ALMs as it was):
  default packing: 24 M10K (-4), 207 ALMs (+14)
  --max-depth 2048: 22 M10K (-6), 259 ALMs (+66)
  --max-depth 1024: 22 M10K (-6), 319 ALMs (+126)
Proof: run_audio_sinc_split.py. Usage: prepare_audio_sinc_split.py PROJECT [--max-depth N]
"""
from pathlib import Path
import re
import sys

EDITS = [
    ('''    (* ramstyle = "M10K" *) reg [17:0] coefficients [0:10367];
''', '''    // audio-sinc-split: phases 0..79 here, phase 80 in its own table (one read port)
    (* ramstyle = "M10K"{max_depth} *) reg [17:0] coefficients [0:10239];
    (* ramstyle = "M10K" *) reg [17:0] phase80 [0:127];
'''),
    ('''    initial $readmemh(COEFFICIENT_FILE, coefficients);
''', '''    initial $readmemh(COEFFICIENT_FILE, coefficients);   // its first 10,240 words: phases 0..79
    initial $readmemh("{phase80}", phase80);   // words 10,240..10,367 of the same file
'''),
    ('''    reg [17:0] coefficient_a, coefficient_b;
''', '''    reg [17:0] main_a, main_b, phase80_q;
    reg use80_a = 1'b0, use80_b = 1'b0;
    wire [17:0] coefficient_a = use80_a ? phase80_q : main_a;
    wire [17:0] coefficient_b = use80_b ? phase80_q : main_b;
'''),
    ('''            coefficient_a <= coefficients[address_a];
            coefficient_b <= coefficients[address_b];
''', '''            main_a <= coefficients[address_a];   // past 10,239 only for phase 80, which use80_a replaces
            main_b <= coefficients[address_b];
            phase80_q <= phase80[issued[6:0]];   // phase 79 (port b) or 80 (port a): both taps are issued[6:0]
            use80_a <= phase_a == 8'd80;
            use80_b <= phase_b == 8'd80;
'''),
]


def phase80_file(coefficient_file, out):
    """Write words 10,240..10,367 (phase 80) of the coefficient file to out."""
    words = Path(coefficient_file).read_text().split()
    if len(words) != 10368: raise ValueError(f'audio-sinc-split: {coefficient_file} has {len(words)} words, not 10,368')
    Path(out).write_text('\n'.join(words[10240:]) + '\n')
    return Path(out)


def sinc_split_text(text, phase80, max_depth=None):
    if 'audio-sinc-split' in text: raise ValueError('audio-sinc-split already applied')
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError('audio-sinc-split: psx_audio_sinc changed: ' + old.strip()[:60])
        text = text.replace(old, new.replace('{phase80}', str(phase80)).replace('{max_depth}', f', max_depth = {max_depth}' if max_depth else ''))
    return text


def prepare_project(project, max_depth=None):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/psx_audio_sinc\.sv)"', q)
    if len(hits) != 1: raise ValueError('audio-sinc-split: expected one psx_audio_sinc.sv in the project')
    out = project/'audio-sinc-split'/'psx_audio_sinc.sv'; out.parent.mkdir(exist_ok=True)
    # the coefficient file core_top hands the converter (psx_audio_bridge COEFFICIENT_FILE)
    tops = re.findall(r'"([^"]*/core_top\.sv)"', q)
    if len(tops) != 1: raise ValueError('audio-sinc-split: expected one core_top.sv in the project')
    files = set(re.findall(r'COEFFICIENT_FILE\("([^"]+)"\)', Path(tops[0]).read_text()))
    if len(files) != 1: raise ValueError(f'audio-sinc-split: coefficient file in core_top: {files}')
    p80 = phase80_file(files.pop(), out.parent/'phase80.hex18')
    out.write_text(sinc_split_text(Path(hits[0]).read_text(), p80, max_depth))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path)
    parser.add_argument('--max-depth', type=int, choices=(1024, 2048),
                        help='M10K depth hint for the main table: 20 M10K instead of 22, for a wider output mux')
    args = parser.parse_args()
    print(prepare_project(args.project, args.max_depth))
