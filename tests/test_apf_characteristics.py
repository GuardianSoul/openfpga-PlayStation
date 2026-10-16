"""Reject incomplete physical evidence and check pin aperture arithmetic."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_apf_characteristics import analyze, derive, parse_sheet


def sheet_text():
    sections = [
        ('Setup Times', False), ('Hold Times', False),
        ('Clock to Output Times', True), ('Minimum Clock to Output Times', True),
        ('Output Enable Times', True), ('Minimum Output Enable Times', True),
        ('Output Disable Times', True), ('Minimum Output Disable Times', True),
    ]
    inputs = [('bridge_1wire', 'clk_74a'), ('bridge_spiss', 'clk_74a'),
              ('bridge_spiss', 'bridge_spiclk'), ('bridge_spimiso', 'bridge_spiclk'),
              ('bridge_spimosi', 'bridge_spiclk')]
    outputs = [(pin, 'clk_74a') for pin in
               ('bridge_1wire', 'bridge_spiclk', 'bridge_spimiso', 'bridge_spimosi')]
    lines = []
    for title, output in sections:
        labels = ['0 to Hi-Z', '1 to Hi-Z'] if 'Disable' in title else ['Rise', 'Fall']
        lines += [f'; {title} ;', '; Data Port ; Clock Port ; ' + ' ; '.join(labels) +
                  ' ; Clock Edge ; Clock Reference ;']
        for pin, clock in outputs if output else inputs:
            # Distinct rising/falling values detect edge and min/max swaps.
            if not output:
                a, b = (-1.0, -0.5) if 'Hold' in title else (0.25, 0.5)
            else:
                a, b = (1, 2) if title.startswith('Minimum') else (3, 4)
                if 'Clock to Output' in title and pin == 'bridge_spiclk':
                    a, b = (2, 3) if title.startswith('Minimum') else (4, 5)
                if 'Clock to Output' in title and pin in ('bridge_spimiso', 'bridge_spimosi'):
                    a, b = (6, 7) if title.startswith('Minimum') else (8, 9)
            lines.append(f'; {pin} ; {clock} ; {a} ; {b} ; Rise ; {clock} ;')
    return '\n'.join(lines) + '\n'


def write_table(path, fields, rows):
    with path.open('w') as f:
        writer = csv.DictWriter(f, fields, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def fixture(directory):
    prefix = 'io_bridge_peripheral:ibs|'
    rx = [prefix + x for x in ('rx_byte_done', 'rx_latch_idx.state_bit_0', 'rx_latch_idx.state_bit_1')]
    rx += [prefix + f'rx_byte[{i}]' for i in range(8)]
    rx += [prefix + f'rx_dat[{i}]' for i in range(2, 8)]
    inputs = {
        'bridge_1wire': ['io_pad_controller:ipm|synch_3:s01|stage_1[0]'],
        'bridge_spiclk': rx,
        'bridge_spiss': rx + [prefix + 'synch_3:s02|stage_1[0]'],
        'bridge_spimiso': [prefix + 'rx_byte[0]'] + [prefix + f'rx_dat[{i}]' for i in (2, 4, 6)],
        'bridge_spimosi': [prefix + 'rx_byte[1]'] + [prefix + f'rx_dat[{i}]' for i in (3, 5, 7)],
    }
    outputs = {'bridge_1wire': ['io_pad_controller:ipm|pad_1wire~reg0', 'io_pad_controller:ipm|pad_1wire~en']}
    for port in ('bridge_spiclk', 'bridge_spimiso', 'bridge_spimosi'):
        stem = port.replace('bridge_', 'phy_')
        enable = 'phy_spimiso' if port == 'bridge_spimosi' else stem
        outputs[port] = [prefix + stem + '~reg0', prefix + enable + '~en']
    geometry = [dict(direction=direction, port=pin, register=reg)
                for direction, mapping in (('input', inputs), ('output', outputs))
                for pin, regs in mapping.items() for reg in regs]
    arcs = [r for r in geometry if (r['direction'], r['port']) != ('input', 'bridge_spiclk')]
    transit = []
    for corner in range(4):
        for index, row in enumerate(arcs):
            a, b = row['port'], row['register']
            if row['direction'] == 'output':
                a, b = b, a
            for minimum in (0, 1):
                transit.append(dict(corner=corner, arc=index, **row, minimum=minimum,
                                    **{'from': a, 'to': b}, delay_ns=1 if minimum else 2))
        model = ('Slow', 'Fast')[corner // 2] + ' 1100mV ' + ('85C', '0C')[corner % 2] + ' Model'
        (directory / f'corner{corner}-arc0-min0.txt').write_text(f'Delay Model:\n  {model}\n')
        (directory / f'corner{corner}-datasheet.txt').write_text(sheet_text())
    write_table(directory / 'clocks.tsv', ['clock', 'period_ns'],
                [dict(clock=c, period_ns=13.468) for c in ('clk_74a', 'bridge_spiclk')])
    write_table(directory / 'geometry.tsv', ['direction', 'port', 'register'], geometry)
    write_table(directory / 'transit.tsv',
                ['corner', 'arc', 'direction', 'port', 'register', 'minimum', 'from', 'to', 'delay_ns'], transit)


class APFCharacteristics(unittest.TestCase):
    def test_rising_peer_clock_and_both_data_edges(self):
        sheet = parse_sheet(sheet_text())
        result = derive(sheet, 13.468)
        serial = result['serial_pin_apertures']['bridge_spimosi']
        self.assertAlmostEqual(serial['available_setup_ns'], 6.468)
        self.assertAlmostEqual(serial['available_hold_ns'], 15.468)
        self.assertAlmostEqual(result['PAD_uninterrupted_low_pulses']['one']['low_min_ns'], 266.360)
        self.assertAlmostEqual(result['PAD_uninterrupted_low_pulses']['zero']['low_max_ns'], 539.720)
        self.assertEqual(result['output_driver_delays']['bridge_1wire']['enable_max_ns'], 4)
        # Negative first-flop intrinsic hold is retained, never clamped to zero.
        self.assertEqual(sheet[('hold', 'bridge_1wire', 'clk_74a')]['first_ns'], -1)

    def test_datasheet_faults_fail_closed(self):
        original = sheet_text()
        faults = [
            original.replace('; Setup Times ;', '; Unknown Times ;'),
            original.replace('; Rise ; clk_74a ;', '; Fall ; clk_74a ;', 1),
            original.replace('; Clock Reference ;', '; Other Reference ;', 1),
            original.replace('; Rise ; clk_74a ;', '; Rise ; other_clock ;', 1),
            original.replace('; 0.25 ; 0.5 ;', '; nan ; 0.5 ;', 1),
            original.replace('; 0.25 ; 0.5 ;', '; inf ; 0.5 ;', 1),
            original.replace('; Minimum Clock to Output Times ;', '; Clock to Output Times ;'),
            original.replace('; 6 ; 7 ;', '; 60 ; 70 ;', 1),
            original + '; bridge_1wire ; clk_74a ; 1 ; 2 ; Rise ; clk_74a ;\n',
            original.replace('; bridge_1wire ; clk_74a ; 0.25 ; 0.5 ; Rise ; clk_74a ;\n', '', 1),
        ]
        for index, text in enumerate(faults):
            with self.subTest(fault=index), self.assertRaises(ValueError):
                parse_sheet(text)

    def test_complete_four_corner_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixture(directory)
            result = analyze(directory)
        self.assertEqual(len(result['datasheet_rows']), 136)
        self.assertEqual(len(result['transit_rows']), 280)
        self.assertEqual(len(result['geometry']), 52)
        self.assertAlmostEqual(result['serial_available_setup_min_ns'], 6.468)
        self.assertFalse(result['path_requirements_added'])
        self.assertFalse(result['board_qualified'])

    def test_transit_and_model_coverage_faults(self):
        for fault in ('missing', 'duplicate', 'endpoint', 'index', 'delay', 'columns', 'geometry', 'clock', 'model'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                fixture(directory)
                path = directory / 'transit.tsv'
                lines = path.read_text().splitlines()
                if fault == 'missing': lines.pop()
                if fault == 'duplicate': lines.append(lines[-1])
                if fault == 'endpoint': lines[1] = lines[1].replace('stage_1[0]', 'wrong_stage[0]', 1)
                if fault == 'index': lines[1] = lines[1].replace('0\t0\t', '0\t99\t', 1)
                if fault == 'delay': lines[2] = lines[2][:-1] + '3'
                if fault == 'columns': lines[0] += '\textra'
                path.write_text('\n'.join(lines) + '\n')
                if fault == 'geometry':
                    p = directory / 'geometry.tsv'
                    p.write_text('\n'.join(p.read_text().splitlines()[:-1]) + '\n')
                if fault == 'clock':
                    p = directory / 'clocks.tsv'
                    p.write_text(p.read_text().replace('13.468', '12.0'))
                if fault == 'model':
                    p = directory / 'corner3-arc0-min0.txt'
                    p.write_text((directory / 'corner2-arc0-min0.txt').read_text())
                with self.assertRaises(ValueError):
                    analyze(directory)


if __name__ == '__main__':
    unittest.main()
