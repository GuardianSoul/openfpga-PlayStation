import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_native_hold_route as route
from prepare_resource_targets import digest
from tool_paths import ROOT


class NativeHoldPreparation(unittest.TestCase):
    def temporary(self):
        parent = ROOT/'build/sim/native-hold-guards'
        parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=parent)

    def fixture(self, p):
        # Identity/transaction fixture only. Actual RTL/net topology is tested
        # separately against the source-bound Quartus mapped/routed database.
        core = p/'core_top.sv'
        core.write_text('module core_top; endmodule\n')
        reviewed = {}
        for name in route.REVIEWED:
            f = p/name
            f.write_text('-- identity fixture '+name+'\n')
            reviewed[name] = digest(f)
        (p/'apf_top.qsf').write_text(
            f'set_global_assignment -name SYSTEMVERILOG_FILE "{core}"\n'
            + ''.join(f'set_global_assignment -name {"VHDL_FILE" if name.endswith(".vhd") else "SYSTEMVERILOG_FILE"} "{p/name}"\n' for name in reviewed))
        (p/'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
        (p/'pocket.sdc').write_text('derive_clock_uncertainty\nset pocket_cpu_hold_ns 0.15\n')
        return digest(core), reviewed

    def test_only_qsf_and_new_constraints_change(self):
        with self.temporary() as tmp:
            p = Path(tmp)
            top, reviewed = self.fixture(p)
            before = {f: digest(f) for f in p.iterdir()}
            q = (p/'apf_top.qsf').read_text()
            with patch.dict(route.REVIEWED, reviewed, clear=True):
                result = route.prepare_project(p, top)
            self.assertEqual(result['CPU_hold_reserve_ns'], .15)
            self.assertFalse(result['exceptions_added'])
            self.assertTrue(result['original_setup_hold_retained'])
            self.assertTrue((p/'apf_top.qsf').read_text().startswith(q))
            for f, sha in before.items():
                if f.name != 'apf_top.qsf':
                    self.assertEqual(digest(f), sha)
            with self.assertRaisesRegex(ValueError, 'precede map/fit'):
                route.prepare_project(p, top)

    def test_foreign_source_top_and_missing_reserve_reject_before_writing(self):
        for fault in ('top', 'source', 'reserve'):
            with self.subTest(fault=fault), self.temporary() as tmp:
                p = Path(tmp)
                top, reviewed = self.fixture(p)
                if fault == 'top':
                    top = '0'*64
                if fault == 'source':
                    (p/'dma.vhd').write_text('-- foreign source\n')
                if fault == 'reserve':
                    (p/'pocket.sdc').write_text('derive_clock_uncertainty\n')
                before = {f: digest(f) for f in p.iterdir()}
                with patch.dict(route.REVIEWED, reviewed, clear=True), self.assertRaises(ValueError):
                    route.prepare_project(p, top)
                self.assertFalse((p/'native-hold-route').exists())
                self.assertTrue(all(digest(f) == sha for f, sha in before.items()))

    def test_invalid_minimum_and_compiled_project_reject(self):
        with self.temporary() as tmp:
            p = Path(tmp)
            for value in (math.nan, math.inf, -.1, 0, 2):
                for key in ('dma_ns', 'sdr_ns'):
                    with self.subTest(value=value, key=key), self.assertRaises(ValueError):
                        route.prepare_project(p, '0'*64, **{key: value})
            (p/'db').mkdir()
            with self.assertRaisesRegex(ValueError, 'precede map/fit'):
                route.prepare_project(p, '0'*64)

    def test_outside_ignored_build_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'ignored build'):
                route.prepare_project(tmp, '0'*64)
