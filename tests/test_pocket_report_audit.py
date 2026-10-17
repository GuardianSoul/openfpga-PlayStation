"""Report replicas and error exits must preserve full timing coverage and inputs."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tool_paths import ROOT, find_tool


class ReportAudit(unittest.TestCase):
    def quartus(self):
        path = find_tool('quartus_sh')
        if not path:
            self.skipTest('Quartus required for its project and interpreter APIs')
        return Path(path)

    def test_initialization_copies_and_invalid_geometry(self):
        text = (ROOT / 'scripts/audit_pocket_video_reports_timing.tcl').read_text()
        procedure = text[text.index('proc reports_audit_initialized'):text.index('project_open apf_top')]
        owner = ('core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|'
                 'gpu:igpu|gpu_videoout:igpu_videoout|gpu_videoout_async:igpu_videoout_async|'
                 'psx_video_reports_cdc:video_reports|dst_initialized')
        cases = [([owner], False, True), ([owner, owner + '~DUPLICATE'], False, True),
                 ([owner + '~DUPLICATE'], False, False), ([], False, False),
                 ([owner, owner + '~UNKNOWN'], False, False),
                 ([owner.replace('core_top:ic', 'core_top:other')], False, False),
                 ([owner, owner + '~DUPLICATE'], True, False),
                 ([owner] + [owner + f'~DUPLICATE{i}' for i in range(4)], False, False)]
        stubs = '''proc get_registers {args} {return $::nodes}
proc get_node_info {field node} {
 if {$field eq "-name"} {return $node}
 if {$field eq "-cell"} {if {$::alias} {return same_cell};return $node}
 error "Unexpected node query"
}
proc foreach_in_collection {v c body} {
 foreach n $c {uplevel 1 [list set $v $n];uplevel 1 $body}
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'replicas.tcl'
            for nodes, alias, good in cases:
                body = ('\nset nodes [list ' + ' '.join('{' + n + '}' for n in nodes) + ']\n'
                        f'set alias {int(alias)}\n'
                        'set failed [catch {reports_audit_initialized} why]\n'
                        f'if {{$failed != {int(not good)}}} {{error "Wrong replica verdict: $why"}}\n'
                        'if {$failed && ![string match {*report initialization*} $why]} {error "Wrong guard reason: $why"}\n')
                script.write_text(stubs + procedure + body)
                result = subprocess.run([str(self.quartus()), '-t', str(script)],
                                        capture_output=True, text=True, timeout=30)
                with self.subTest(nodes=nodes, aliased_cells=alias):
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_loader_prevents_actual_quartus_export_after_audit_error(self):
        executable = self.quartus().parent / 'quartus_sta'
        loader = ROOT / 'scripts/run_pocket_timing_audit.tcl'
        with tempfile.TemporaryDirectory() as tmp:
            for wrapped in (False, True):
                directory = Path(tmp) / str(wrapped); directory.mkdir()
                qsf = directory / 'apf_top.qsf'
                before = ('set_global_assignment -name DEVICE 5CEBA4F23C8\n'
                          'set_global_assignment -name NUM_PARALLEL_PROCESSORS 8\n')
                qsf.write_text(before)
                (directory / 'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
                faulty = directory / 'fault.tcl'
                faulty.write_text('''package require ::quartus::project
project_open apf_top
set_global_assignment -name NUM_PARALLEL_PROCESSORS 7
error PLANTED_AUDIT_FAILURE
''')
                command = [str(executable), '-t', str(loader), str(faulty)] if wrapped else [str(executable), '-t', str(faulty)]
                result = subprocess.run(command, cwd=directory, capture_output=True,
                                        text=True, timeout=30)
                with self.subTest(protected_loader=wrapped):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('PLANTED_AUDIT_FAILURE', result.stdout + result.stderr)
                    if wrapped:
                        self.assertEqual(before, qsf.read_text())
                    else:
                        self.assertNotEqual(before, qsf.read_text())
                        self.assertIn('NUM_PARALLEL_PROCESSORS 7', qsf.read_text())


if __name__ == '__main__':
    unittest.main()
