"""PXS6 image/layout guards; native SPU and selected-CPU proofs run separately."""
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_spu_ring_paired import firmware_text,cpu_bench,native_bench,require_paired_spu
from build_staged_save_fw import SRC,image_parts,rom40_hex
from test_save_full_context import unpack


class PairedRingTest(unittest.TestCase):
    def test_layout_fits_staging_without_overwriting_guest_registers(self):
        text=firmware_text(SRC.read_text())
        self.assertIn('0x36535850',text)
        table=text.split('SPU_SS:\n')[1].split('SPU_SELECT_SS:')[0]
        values=[int(x) for x in re.findall(r'\d+',table)]
        addresses=[a for start,end in zip(values[::2],values[1::2]) for a in range(start,end)]
        self.assertEqual(addresses,[7,*range(32,63),*range(256,304),*range(320,370),*range(384,480)])
        self.assertFalse(set(addresses)&set(range(64,192)))
        self.assertEqual(len(set(addresses)&set(range(32,48))),16)
        scalar_save=text.split('        jal     spu_save\n',1)[1].split('        jal     put_data\n',1)[0]
        self.assertNotIn('PAGE+4*37',scalar_save)
        self.assertNotIn('PAGE+4*43',scalar_save)
        self.assertNotIn('PAGE+4*40',scalar_save)
        for bad in (text,SRC.read_text()+'\n'):
            with self.assertRaises(ValueError):firmware_text(bad)

    def test_mutated_spu_and_matching_mutated_report_are_rejected(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'spu.vhd';path.write_text('wrong SPU')
            with self.assertRaisesRegex(ValueError,'SPU differs'):
                require_paired_spu(path,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_paired_handler_packs_in_the_existing_rom_and_decodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ring.S';path.write_text(firmware_text(SRC.read_text()))
            image,stage=image_parts(source=path,ram_pages=8,vram_rows=5,spu_pages=2)
        header=stage[:4]+len(stage).to_bytes(4,'little');at=image[:0xff4].index(header)
        self.assertEqual(unpack(image[at+8:0xff4]+image[0x1c00:],len(stage)),stage)
        self.assertEqual(int.from_bytes(stage[:4],'little'),zlib.crc32(stage[4:]) or 1)
        rows=rom40_hex(image[:0x1000],image[0x1c00:]).splitlines()
        self.assertEqual(len(rows),1024)
        self.assertTrue(all(len(row)==10 for row in rows))
        self.assertEqual(bytes(int(row[:2],16) for row in rows),image[0x1c00:])
        self.assertEqual(b''.join(int(row[2:],16).to_bytes(4,'little') for row in rows),image[:0x1000])

    def test_fixture_uses_circulating_low_word_and_previous_format_rejection(self):
        root=SRC.parents[3]
        model=cpu_bench((root/'sim/reference/tb_staged_save_cpu.vhd').read_text())
        self.assertIn('w := ss(32);',model)
        self.assertIn('for k in 32 to 46 loop ss(k) := ss(k+1); end loop;',model)
        self.assertIn('page(ss_adr) := w; ss(47) := w;',model)
        self.assertIn('Expected a PXS6 source state',model)
        self.assertIn('x"35"; -- preceding indexed PXS5 format',model)
        native=native_bench((root/'sim/pocket/tb_spu_global_ring.vhd').read_text())
        self.assertIn('(address >= 48 and address <= 62)',native)
        with self.assertRaises(ValueError):native_bench(native)


if __name__=='__main__':unittest.main()
