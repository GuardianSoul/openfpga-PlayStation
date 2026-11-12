#!/usr/bin/env python3
"""Area-first synthesis and fitter packing for the save-states build.

OPTIMIZATION_MODE "AGGRESSIVE AREA" and FITTER_AGGRESSIVE_ROUTABILITY_OPTIMIZATION
ALWAYS (as the NDS project uses). ss058i-aa (#926): the same netlist size as #917
(29,014 ALUTs), 18,359 ALMs needed instead of 18,506, 1,856 LABs instead of
1,875, from fitter packing alone. AGGRESSIVE AREA also turns on physical
synthesis for combinational logic, so release_timing decides whether it ships.
Usage: prepare_area_settings.py PROJECT
"""
from pathlib import Path
import sys

# Only the fitter's routability packing; OPTIMIZATION_MODE stays as the
# release QSF sets it. With AGGRESSIVE AREA too (ss058m #930, 1,844 LABs)
# quartus_fit re-exported the QSF, so the frozen-fit gate could not verify the
# fit (its -5.9 ns general[1] setup is the video crossing rel058c has too, not
# a cost of the setting, despite what 3bb8415's message says). Routability
# alone: ss058n #932, 1,848 LABs, verified.
SETTINGS = {'FITTER_AGGRESSIVE_ROUTABILITY_OPTIMIZATION': 'ALWAYS'}


def settings_text(text):
    lines = [l for l in text.split('\n')
             if not any(f'-name {k} ' in l for k in SETTINGS)]
    while lines and lines[-1] == '':
        lines.pop()
    lines += [f'set_global_assignment -name {k} {v}' for k, v in SETTINGS.items()]
    return '\n'.join(lines) + '\n'


def main():
    qsf = Path(sys.argv[1]) / 'apf_top.qsf'
    qsf.write_text(settings_text(qsf.read_text()))
    print(f'area settings: {qsf}')


if __name__ == '__main__':
    main()
