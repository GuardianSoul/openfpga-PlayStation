"""Fail-closed selection of exact RAM storage adaptations."""
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from generate_pocket_audio_coeffs import generate
from prepare_audio_rom_packed import adapt_text, verify_bank
from prepare_dpram_sdp import inventory, primitive
from tool_paths import ROOT


class RamPacking(unittest.TestCase):
    def instance(self, **updates):
        ports = dict(clock_a='write_clk', clock_b='read_clk', clken_a='we_ce', clken_b='re_ce',
                     address_a='write_address(9 downto 2)', address_b='read_address',
                     data_a='data(31 downto 0)', wren_a='write_enable', wren_b="'0'",
                     q_a='open', q_b='read_data')
        ports.update(updates)
        return 'storage: entity mem.dpram generic map (addr_width=>8, data_width=>32) port map ('+', '.join(k+'=>'+v for k, v in ports.items())+');'

    def test_only_syntactically_unused_ports_qualify(self):
        for ports, expected in (
            ({}, 'Awrite-Bread'),
            ({'q_a': 'readback'}, None),
            ({'wren_b': 'disabled_signal'}, None),
            ({'wren_b': 'not enabled'}, None),
            ({'q_b': 'open'}, None),
            ({'q_a': 'read_data', 'q_b': 'open', 'wren_a': "'0'", 'wren_b': 'we'}, 'Bwrite-Aread'),
        ):
            with self.subTest(ports=ports):
                self.assertEqual(inventory(self.instance(**ports))[0]['orientation'], expected)

    def test_comments_offsets_and_nested_associations(self):
        text = '-- fake: entity mem.dpram port map (bad);\n'+self.instance(data_a='pick(a,b(3 downto 0))')
        row, = inventory(text)
        self.assertEqual(text[row['start']:row['end']], 'dpram')
        self.assertEqual(row['ports']['data_a'], 'pick(a,b(3 downto 0))')
        self.assertEqual(inventory('note <= "-- entity mem.dpram";'), [])
        self.assertEqual(inventory(self.instance().replace('mem.dpram', 'mem.dpram_dif')), [])
        self.assertEqual(inventory(self.instance().replace('q_a=>open, ', ''))[0]['orientation'], 'Awrite-Bread')

    def test_unknown_contract_rejects(self):
        for text in (self.instance()+self.instance().replace('q_a=>open', 'q_a=>open, mystery=>value'),
                     self.instance().replace('addr_width=>8', 'addr_width=>8, data_width=>16'),
                     self.instance().replace('q_b=>read_data', 'q_b=>read_data, q_b=>other')):
            with self.assertRaises(ValueError):
                inventory(text)

    def test_primitive_changes_only_mode_and_wiring(self):
        text = (ROOT/'references/PSX_MiSTer/rtl/dpram.vhd').read_text()
        for reverse in (False, True):
            helper = primitive(text, reverse)
            self.assertIn('operation_mode => "DUAL_PORT"', helper)
            self.assertIn('address_reg_b => "CLOCK1"', helper)
            self.assertIn('outdata_reg_b => "UNREGISTERED"', helper)
            self.assertNotIn('entity dpram_dif', helper)
        self.assertIn('clock0 => clock_b', primitive(text, True))
        self.assertIn('clock1 => clock_a', primitive(text, True))

    def test_coefficient_range_contract_and_duplicate(self):
        with tempfile.TemporaryDirectory() as work:
            record = generate(work)
            self.assertTrue(verify_bank(record['compact_path'])['all_words_reconstruct_exactly'])
            path = Path(record['compact_path'])
            words = path.read_text().splitlines()
            words[0] = '00040'  # +64 cannot fit the outer band's signed seven bits.
            path.write_text('\n'.join(words)+'\n')
            with self.assertRaises(ValueError):
                verify_bank(path)
        source = (ROOT/'rtl/audio/psx_audio_sinc.sv').read_text()
        with self.assertRaises(ValueError):
            adapt_text(adapt_text(source))


if __name__ == '__main__':
    unittest.main()
