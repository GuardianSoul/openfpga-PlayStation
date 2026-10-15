#!/usr/bin/env python3
"""Show resources from a Quartus fitter summary; does not run synthesis."""
import argparse
import json
from pathlib import Path
import re

DEFAULT = Path(__file__).resolve().parents[1] / 'references/PlayStation_on_FPGA/Pocket/ap_core.fit.summary'
FIELDS = {
    'alms': 'Logic utilization (in ALMs)',
    'ram_blocks': 'Total RAM Blocks',
    'dsp_blocks': 'Total DSP Blocks',
    'memory_bits': 'Total block memory bits',
}


def parse_summary(text):
    result = {}
    for key, label in FIELDS.items():
        match = re.search(r'^' + re.escape(label) + r'\s*:\s*([\d,]+)\s*/\s*([\d,]+)', text, re.MULTILINE)
        if not match:
            raise ValueError(f'Missing resource field: {label}')
        used, capacity = (int(v.replace(',', '')) for v in match.groups())
        if capacity <= 0 or used > capacity:
            raise ValueError(f'Invalid resource counts: {label}')
        result[key] = {'used': used, 'capacity': capacity,
                       'free': capacity - used, 'percent': round(100 * used / capacity, 2)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', nargs='?', type=Path, default=DEFAULT)
    args = parser.parse_args()
    try:
        resources = parse_summary(args.summary.read_text())
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps({'source': str(args.summary), 'resources': resources,
                      'note': 'Existing report only; no new build or timing verification.'}, indent=2))


if __name__ == '__main__':
    main()
