"""Read generated preservation targets through Quartus's actual QSF reader."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_resource_targets import digest
from prepare_vram_response_enable import apply_response_enable
from tool_paths import ROOT, find_tool


class ResponseAssignments(unittest.TestCase):
    def test_imported_targets_and_legacy_literal_brace_control(self):
        quartus = find_tool('quartus_sh')
        if not quartus:
            self.skipTest('Quartus required for its QSF import semantics')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            core = directory / 'core_top.sv'
            # Only generator anchors are needed: project_open imports settings,
            # without analyzing or compiling this structural fixture.
            core.write_text('''module core_top;
    wire v_valid,v_ready,v_write,v_rsp,v_rsp_ready,v_error;
    psx_vram_adapter vram_commands (.rsp_data(v_read),.rsp_error(v_error),.fault(vram_fault));
    psx_mem_cdc vram_cdc (.s_rsp_valid(v_rsp),.s_rsp_ready(v_rsp_ready),.unused());
endmodule
''')
            sources = [dict(path=str(p), sha256=digest(p)) for p in (
                core, ROOT / 'rtl/memory/psx_mem_cdc.sv',
                ROOT / 'rtl/pocket/psx_vram_adapter.sv')]
            qsf = directory / 'apf_top.qsf'
            qsf.write_text('set_global_assignment -name DEVICE 5CEBA4F23C8\n' +
                ''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{s["path"]}"\n'
                        for s in sources))
            (directory / 'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
            manifest = apply_response_enable(dict(sources=sources, project_hashes={}), directory)
            self.assertEqual(digest(qsf), manifest['project_hashes']['apf_top.qsf'])
            generated = qsf.read_text()
            script = directory / 'read.tcl'
            script.write_text('''package require ::quartus::project
project_open apf_top
try {
    foreach name {DONT_MERGE_REGISTER PRESERVE_REGISTER} {
        set count 0
        foreach_in_collection a [get_all_instance_assignments -name $name] {
            if {[lindex $a 2] ne {*|vram_commands|*bank_enable*} ||
                [lindex $a 4] ne {ON}} {
                error "Invalid imported response-enable target"
            }
            incr count
        }
        if {$count != 1} {error "Missing imported response-enable target"}
    }
    puts RESPONSE_QSF_IMPORT_PASS
} finally {project_close -dont_export_assignments}
''')
            for legacy in (False, True):
                with self.subTest(legacy_literal_braces=legacy):
                    text = generated.replace('"*|vram_commands|*bank_enable*"',
                                             '{*|vram_commands|*bank_enable*}') if legacy else generated
                    qsf.write_text(text)
                    result = subprocess.run([quartus, '-t', str(script)], cwd=directory,
                                            capture_output=True, text=True, timeout=30)
                    output = result.stdout + result.stderr
                    if legacy:
                        self.assertNotEqual(result.returncode, 0, output)
                        self.assertIn('Invalid imported response-enable target', output)
                    else:
                        self.assertEqual(result.returncode, 0, output)
                        self.assertIn('RESPONSE_QSF_IMPORT_PASS', output)
                    self.assertEqual(text, qsf.read_text())


if __name__ == '__main__':
    unittest.main()
