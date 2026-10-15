#!/usr/bin/env python3
"""Rerun the GTE adapter and CPU/GTE probes with gte-m10k.

The adapted engine gets the full table chain (gte-matrix-ram, gte-add-table,
gte-vector-ram), gte-multicycle's strict alternate-edge enable and then the
falling-edge block RAM reads. The adapter probe's reference engine ticks on
the same strict schedule, so reads, busy timing and command results must
still match exactly; the CPU probe checks executed GTE programs end to end.
A planted read of the wrong matrix row must fail. (A rising-edge read
would also be equivalent under the strict enable: the address holds across
the non-enabled edge.)
"""
from pathlib import Path

from prepare_gte_add_table import add_table_text
from prepare_gte_m10k import gte_m10k_text
from prepare_gte_matrix_ram import compute_path_text, engine_text
from prepare_gte_multicycle import adapter_text, STRICT
from prepare_gte_vector_ram import vector_ram_text
from prepare_resource_targets import digest
import run_cpu_gte_adapter
import run_gte_adapter


def transformer(fault=None):
    def transform(sources):
        by_name = {Path(s['path']).name: s for s in sources}
        for name, change in (('laxer_mister_adapter.sv', adapter_text),
                             ('GTEEngine.sv', engine_text),
                             ('GTEComputePath.sv', lambda t: gte_m10k_text(vector_ram_text(add_table_text(compute_path_text(t)))))):
            if name not in by_name: raise SystemExit('Expected GTE source ' + name)
            path = Path(by_name[name]['path']); text = change(path.read_text())
            if fault and name == 'GTEComputePath.sv': text = fault(text)
            path.write_text(text); by_name[name]['sha256'] = digest(path)
        if STRICT not in Path(by_name['laxer_mister_adapter.sv']['path']).read_text(): raise SystemExit('Strict enable missing')
    return transform


def main():
    run_gte_adapter.main('gte-m10k', transformer(), ('-DSTRICT_ENABLE',))
    run_cpu_gte_adapter.main('cpu-gte-m10k', transformer())
    late = lambda t: t.replace('matRam1_q <= matRam1[matAddr1];', 'matRam1_q <= matRam1[matAddr1 ^ 4\'d1];')
    try:
        run_gte_adapter.main('gte-m10k-fault', transformer(late), ('-DSTRICT_ENABLE',))
    except (SystemExit, Exception) as error:
        print('Planted wrong-row table read rejected:', str(error).splitlines()[0][:120] if str(error) else type(error).__name__)
    else:
        raise SystemExit('Planted wrong-row table read not detected')
    print('GTE compute-path tables in falling-edge block RAM: adapter and CPU/GTE probes pass')


if __name__ == '__main__': main()
