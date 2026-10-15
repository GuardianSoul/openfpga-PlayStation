"""A Git update must not substitute new bytes for a measured source version."""
from pathlib import Path
import tempfile
import unittest
from scripts.record_pocket_measurements import verify_sources
from scripts.prepare_resource_targets import digest


class CompiledSourceArchiveTests(unittest.TestCase):
    def test_later_source_update_uses_only_the_exact_compiled_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); source=directory/'source.sv'
            source.write_text('module measured; endmodule\n')
            original_hash=digest(source)
            inputs=[dict(path=str(source),library='work',sha256=original_hash)]
            index_hash=verify_sources(directory,inputs)
            source.write_text('module changed; endmodule\n')
            self.assertEqual(index_hash,verify_sources(directory,inputs))
            snapshot=directory/'compiled-sources'/(original_hash+'.sv')
            snapshot.write_text('module changed; endmodule\n')
            with self.assertRaisesRegex(ValueError,'Changed compiled-source snapshot'):
                verify_sources(directory,inputs)

    def test_no_snapshot_cannot_excuse_a_changed_compiler_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); source=directory/'source.sv'
            source.write_text('module measured; endmodule\n')
            inputs=[dict(path=str(source),library='work',sha256=digest(source))]
            source.write_text('module changed; endmodule\n')
            with self.assertRaisesRegex(ValueError,'Changed source without matching snapshot'):
                verify_sources(directory,inputs)


if __name__=='__main__':unittest.main()
