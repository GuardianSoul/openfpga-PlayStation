"""Copy project inputs without inheriting another compiler job's verdicts."""
from pathlib import Path
import shutil


ROOT_JOB_EVIDENCE = frozenset({
    'result.json', 'timing-inputs.json', 'compile-report.json',
    'physical-result.json', 'map-result.json', 'fit-report.json',
    'apf_top.qsf.precompile',
})


def project_copy_ignore(source):
    root = Path(source).resolve()
    outputs = shutil.ignore_patterns(
        'db', 'incremental_db', 'output_files', 'input-archive',
        '*.rpt', '*.summary', '*.log')

    def ignore(directory, names):
        skipped = set(outputs(directory, names))
        if Path(directory).resolve() == root:
            # Input manifests and nested JSON resources remain inputs. A new
            # compiler job must create its own top-level results and target.
            skipped.update(ROOT_JOB_EVIDENCE.intersection(names))
        return skipped

    return ignore
