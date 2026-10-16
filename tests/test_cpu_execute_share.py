import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_cpu_execute_share import execute_share_text, prepare_project
from tool_paths import ROOT


class ExecuteShareGuards(unittest.TestCase):
    def test_foreign_or_incomplete_cpu_is_rejected(self):
        for text in ('', 'architecture arch of cpu is',
                     (ROOT / 'references/PSX_MiSTer/rtl/cpu.vhd').read_text()):
            with self.subTest(length=len(text)), self.assertRaisesRegex(ValueError, 'exact current CPU'):
                execute_share_text(text)

    def test_outside_build_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'ignored build'):
                prepare_project(tmp)

    def test_compiled_project_is_rejected_before_reading_inputs(self):
        parent = ROOT / 'build/sim/cpu-execute-guards'
        parent.mkdir(parents=True, exist_ok=True)
        for marker in ('db', 'incremental_db', 'output_files', 'cpu-execute-share'):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(dir=parent) as tmp:
                (Path(tmp) / marker).mkdir()
                with self.assertRaisesRegex(ValueError, 'precede map/fit'):
                    prepare_project(tmp)

    def test_save_state_project_is_rejected(self):
        parent = ROOT / 'build/sim/cpu-execute-guards'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            (Path(tmp) / 'manifest.json').write_text(json.dumps({'reset_only': False}))
            with self.assertRaisesRegex(ValueError, 'reset-only'):
                prepare_project(tmp)
