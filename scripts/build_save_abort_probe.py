#!/usr/bin/env python3
"""Build an entry/return diagnostic that refuses every save and load.

It uses the existing NOOP stub, with its save result changed to error.
It copies no state and never enters the save loops. Production firmware is
unchanged. The expected hardware result is an error followed by a running game.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

def probe_source(text, early_finish=False):
    pattern = re.compile(r'        \.ifdef  NOOP +# NOOP builds[^\n]*\n.*?        \.endif\n', re.S)
    blocks = list(pattern.finditer(text))
    if len(blocks) != 1:
        raise ValueError('expected one NOOP entry block')
    block = blocks[0].group()
    block, count = re.subn(r'(        li      \$t0, )1( +# is refused;)', r'\g<1>2\2', block)
    if count != 1:
        raise ValueError('expected one NOOP save-success result')
    result = '        .equ    NOOP, 1\n' + text[:blocks[0].start()] + block + text[blocks[0].end():]
    if early_finish:
        entry = '\nentry:\n        lui     $k0, 0xBF00\n'
        late = '1:      j       restore\n        sb      $t0, FINISH($k0)\n'
        if result.count(entry) != 1 or result.count(late) != 1:
            raise ValueError('expected one entry and one NOOP completion for early FINISH')
        result = result.replace(entry, entry +
                                '        li      $k1, 2\n'
                                '        sb      $k1, FINISH($k0) # report before staging/EPC read\n')
        result = result.replace(late, '1:      j       restore\n'
                                '        nop                     # FINISH already sent exactly once\n')
    return result


def build(output, early_finish=False, kernel_gprs=False):
    from build_save_fw import SRC, byte_hex, image, rom1, rom2
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'save_abort.S'
    text = probe_source(SRC.read_text(), early_finish=early_finish)
    if kernel_gprs:
        from prepare_save_kernel_gprs import firmware_text
        text = firmware_text(text)
    source.write_text(text)
    data = image(source)
    first, second = rom1(data), rom2(data)
    (output / 'psx_save_fw_abort.hex').write_text(byte_hex(first))
    (output / 'psx_save_fw_abort2.hex').write_text(byte_hex(second))
    (output / 'psx_save_rom40.hex').write_text(''.join(
        f'{second[i]:02x}{int.from_bytes(first[4*i:4*i+4], "little"):08x}\n' for i in range(1024)))
    report = dict(kind='save-entry-error-probe',
                  early_finish=early_finish,
                  kernel_gprs=kernel_gprs,
                  expected='Save/load reports error; guest continues unchanged. No state is copied.',
                  source_sha256=hashlib.sha256(SRC.read_bytes()).hexdigest(),
                  sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()})
    (output / 'probe.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--early-finish', action='store_true',
                        help='report error before staging registers or inspecting EPC')
    parser.add_argument('--kernel-gprs', action='store_true',
                        help='requires the paired experimental CPU; preserve k0/k1 in staging RAM')
    args = parser.parse_args()
    print(json.dumps(build(args.output, early_finish=args.early_finish,
                           kernel_gprs=args.kernel_gprs), indent=2))
