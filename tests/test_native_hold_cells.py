from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_native_hold_cells as cells
from prepare_resource_targets import digest
from tool_paths import ROOT


class NativeIdentityPreparation(unittest.TestCase):
    def temporary(self):
        parent = ROOT/'build/sim/native-identity-guards'
        parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=parent)

    def fixture(self, p):
        # Transaction/identity fixture only. The selected real RTL and vendor
        # primitive are checked by run_native_hold_cells, not by this fixture.
        core = p/'core_top.sv'
        core.write_text('module core_top; endmodule\n')
        reviewed = {}
        for name in cells.REVIEWED:
            f = p/name
            f.write_text('-- identity fixture '+name+'\n')
            reviewed[name] = digest(f)
        (p/'apf_top.qsf').write_text(
            f'set_global_assignment -name SYSTEMVERILOG_FILE "{core}"\n'
            + ''.join(f'set_global_assignment -name {"VHDL_FILE" if name.endswith(".vhd") else "SYSTEMVERILOG_FILE"} "{p/name}"\n' for name in reviewed))
        (p/'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
        (p/'pocket.sdc').write_text('derive_clock_uncertainty\nset pocket_cpu_hold_ns 0.15\n')
        return digest(core), reviewed

    def test_qsf_redirects_sources_without_changing_original_or_sdc(self):
        with self.temporary() as tmp:
            p = Path(tmp)
            top, reviewed = self.fixture(p)
            before = {f: digest(f) for f in p.iterdir()}
            with patch.dict(cells.REVIEWED, reviewed, clear=True), \
                    patch.object(cells, 'dma_text', return_value='-- transformed DMA\n'), \
                    patch.object(cells, 'sdram_text', return_value='// transformed SDRAM\n'):
                result = cells.prepare_project(p, top)
            self.assertEqual(result['CPU_hold_reserve_ns'], .15)
            self.assertEqual(result['additional_guest_cycles'], 0)
            self.assertIsNone(result['assumed_fixed_cell_delay_ns'])
            self.assertEqual(len(result['added']), 3)
            self.assertTrue(result['original_clocks_and_all_timing_constraints_retained'])
            for f, sha in before.items():
                if f.name != 'apf_top.qsf':
                    self.assertEqual(digest(f), sha)
            q = (p/'apf_top.qsf').read_text()
            self.assertIn('native-hold-cells/dma.vhd', q)
            self.assertIn('native-hold-cells/psx_sdram_ctrl.sv', q)
            self.assertNotIn('SDC_FILE', q)
            with self.assertRaisesRegex(ValueError, 'before map/fit'):
                cells.prepare_project(p, top)

    def test_identity_or_reserve_failure_leaves_project_untouched(self):
        for fault in ('top', 'source', 'reserve', 'unquoted'):
            with self.subTest(fault=fault), self.temporary() as tmp:
                p = Path(tmp)
                top, reviewed = self.fixture(p)
                if fault == 'top':
                    top = '0'*64
                if fault == 'source':
                    (p/'dma.vhd').write_text('-- foreign source\n')
                if fault == 'reserve':
                    (p/'pocket.sdc').write_text('derive_clock_uncertainty\n')
                if fault == 'unquoted':
                    q = p/'apf_top.qsf'
                    q.write_text(q.read_text().replace('"'+str(p/'dma.vhd')+'"', str(p/'dma.vhd')))
                before = {f: digest(f) for f in p.iterdir()}
                with patch.dict(cells.REVIEWED, reviewed, clear=True), self.assertRaises(ValueError):
                    cells.prepare_project(p, top)
                self.assertFalse((p/'native-hold-cells').exists())
                self.assertTrue(all(digest(f) == sha for f, sha in before.items()))

    def test_compiled_or_previous_route_project_rejects(self):
        for name in ('db', 'incremental_db', 'output_files', 'native-hold-route'):
            with self.subTest(name=name), self.temporary() as tmp:
                p = Path(tmp)
                (p/name).mkdir()
                with self.assertRaisesRegex(ValueError, 'before map/fit'):
                    cells.prepare_project(p, '0'*64)

    def test_unknown_transform_sources_and_external_directory_reject(self):
        for transform in (cells.dma_text, cells.sdram_text):
            with self.assertRaisesRegex(ValueError, 'Unreviewed'):
                transform('unreviewed')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'ignored build'):
                cells.prepare_project(tmp, '0'*64)
