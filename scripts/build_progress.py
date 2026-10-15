#!/usr/bin/env python3
"""Print the stage and an estimated percentage of Quartus compiles in build dirs.

Stage weights are typical PSXEarly full-build durations (minutes): synthesis
1.5, fitter preparation 0.4, placement preparation 1.6, placement 1.0,
routing 6.4, post-fit 1.0, assembler 0.5, timing 1.5.
"""
import argparse
from pathlib import Path

STAGES = [  # (name, minutes, file, marker that ends the stage)
    ('synthesis', 1.5, 'map.log', 'Quartus Prime Analysis & Synthesis was successful'),
    ('fit prep', 0.4, 'fit.log', 'Fitter preparation operations ending'),
    ('placement prep', 1.6, 'fit.log', 'Fitter placement preparation operations ending'),
    ('placement', 1.0, 'fit.log', 'Fitter placement operations ending'),
    ('routing', 6.4, 'fit.log', 'Fitter routing operations ending'),
    ('post-fit', 1.0, 'fit.log', 'Quartus Prime Fitter was successful'),
    ('assembler', 0.5, 'asm.log', 'Quartus Prime Assembler was successful'),
    ('timing', 1.5, 'sta.log', 'Quartus Prime Timing Analyzer was successful'),
]
TOTAL = sum(s[1] for s in STAGES)


def progress(directory):
    done = 0.0
    for name, minutes, log, marker in STAGES:
        path = directory/log
        text = path.read_text(errors='replace') if path.exists() else ''
        if 'Error (' in text and 'was unsuccessful' in text: return f'failed in {name}', done / TOTAL
        if marker not in text: return name, done / TOTAL
        done += minutes
    return 'done', 1.0


def bar(fraction, width=20):
    filled = round(fraction * width)
    return '█' * filled + '░' * (width - filled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dirs', nargs='+', type=Path)
    args = parser.parse_args()
    for d in args.dirs:
        stage, fraction = progress(d)
        print(f'{d.name}: {bar(fraction)} {fraction * 100:3.0f}% {stage}')


if __name__ == '__main__':
    main()
