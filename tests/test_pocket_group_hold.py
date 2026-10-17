"""Fail-closed identities and lane geometry for the existing 53-cell profile."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tool_paths import ROOT, find_tool


class GroupHoldProfile(unittest.TestCase):
    def run_tcl(self, directory, body):
        quartus = find_tool('quartus_sh')
        if not quartus:
            self.skipTest('Quartus required for its collection and project APIs')
        source = (ROOT / 'scripts/audit_pocket_group_hold_cells.tcl').read_text()
        procedures = source[source.index('proc group53_key'):source.index('project_open apf_top')]
        script = directory / 'check.tcl'
        script.write_text('package require ::quartus::project\n' + procedures + body)
        result = subprocess.run([quartus, '-t', str(script)], cwd=directory,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_generated_leaves_unknowns_and_crossed_lanes(self):
        leaves = ['ic|psx|component_core|idma|pocket_hold_dma11|identity_lut',
                  'ic|psx|component_core|igpu|igpu_vram2cpu|pocket_hold_vram_empty|identity_lut']
        leaves += [f'ic|native_memory|controller|pocket_hold_sdr{i}_cell|identity_lut'
                   for i in range(32)]
        leaves += [f'ic|psx|component_core|icd_top|\\pocket_hold_cd_lba:{i}:cell|identity_lut'
                   for i in range(19)]
        body = '\nset leaves [list ' + ' '.join('{' + n + '}' for n in leaves) + ']\n'
        body += '''set keys {};foreach leaf $leaves {lappend keys [group53_key $leaf]}
if {[llength $keys]!=53 || [lsort $keys] ne [group53_expected]} {error "Incomplete group profile"}
foreach leaf {
 {ic|native_memory|controller|pocket_hold_sdr32_cell|identity_lut}
 {ic|psx|component_core|icd_top|pocket_hold_cd_lba:0:cell|identity_lut}
 {ic|psx|component_core|icd_top|\\pocket_hold_cd_lba:19:cell|identity_lut}
 {ic|psx|component_core|idma|pocket_hold_dma12|identity_lut}
 {other|pocket_hold_dma11|identity_lut}
} {if {![catch {group53_key $leaf} why] || ![string match {Unknown group identity leaf*} $why]} {error "Unknown leaf accepted"}}
set source {core_top:ic|psx_native_memory:native_memory|psx_sdram_ctrl:controller|wdata[20]}
set target {core_top:ic|psx_native_memory:native_memory|psx_sdram_ctrl:controller|dq_out[4]~_Duplicate_1}
group53_reg sdr 20 source $source
group53_reg sdr 20 target $target
foreach {bit direction name} [list 21 source $source 21 target $target] {
 if {![catch {group53_reg sdr $bit $direction $name} why] || ![string match {Group identity * wiring differs*} $why]} {error "Crossed lane accepted"}
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            self.run_tcl(Path(tmp), body)

    def test_real_project_reader_rejects_changed_identity_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            changed = directory / 'psx_hold_cell.sv'
            changed.write_text('module psx_hold_cell(input i, output o); assign o=~i; endmodule\n')
            (directory / 'fixture.qpf').write_text('PROJECT_REVISION = "fixture"\n')
            (directory / 'fixture.qsf').write_text(
                'set_global_assignment -name DEVICE 5CEBA4F23C8\n'
                f'set_global_assignment -name SYSTEMVERILOG_FILE "{changed}"\n')
            self.run_tcl(directory, '''project_open fixture
try {
 if {![catch {group53_validate_sources} why] ||
     $why ne {Group identity source identity differs psx_hold_cell.sv}} {
  error "Changed identity source did not fail its digest guard: $why"
 }
} finally {project_close -dont_export_assignments}
''')


if __name__ == '__main__':
    unittest.main()
