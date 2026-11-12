#!/usr/bin/env python3
"""Qualify direct pending RAM writes, with the original latch as a payload oracle."""
from pathlib import Path
import json
import argparse
import sys
from prepare_spu_sample_save import transform as samples
from prepare_spu_ring_paired import spu_text
from prepare_spu_pending_feed import transform as feed
from run_spu_guest_bank import bench
from run_spu_full_context import main as check
ROOT=Path(__file__).resolve().parents[1]


def transform(text,fault):
    return feed(spu_text(samples(text)))


if __name__=='__main__':
    own=argparse.ArgumentParser(add_help=False)
    own.add_argument('--payload-target',choices=('record','volume','counter','decode','copy'),default='record')
    selected,rest=own.parse_known_args()
    if selected.payload_target!='record' and '--guest-routing-fault' not in rest:
        own.error('payload target requires --guest-routing-fault')
    sys.argv=[sys.argv[0],*rest]
    mutations={
      'record':('std_logic_vector(voice.currentAddr);\n   -- synthesis translate_off','std_logic_vector(not voice.currentAddr);\n   -- synthesis translate_off','Record-feed payload differs'),
      'volume':('else std_logic_vector(envVolume);\n   EnvCounterRam_dataA','else std_logic_vector(not envVolume);\n   EnvCounterRam_dataA','Volume-feed payload differs'),
      'counter':('else envTicksNew & std_logic_vector(envTicks);','else (not envTicksNew) & std_logic_vector(envTicks);','Counter-feed payload differs'),
      'decode':('std_logic_vector(voice.adpcmLast0) when adpcm_feed_mode = 1','std_logic_vector(not voice.adpcmLast0) when adpcm_feed_mode = 1','Sample-feed payload differs'),
      'copy':('else adpcm_ram_q_b(i);','else not adpcm_ram_q_b(i);','Sample-feed payload differs'),
    }
    before,after,witness=mutations[selected.payload_target]
    rc=check(spu_transform=transform,bench_source=ROOT/'sim/pocket/tb_spu_global_ring.vhd',
        bench_transform=bench,extra_inputs=(Path(__file__),ROOT/'scripts/prepare_spu_record_feed.py',ROOT/'scripts/prepare_spu_pending_feed.py',
        ROOT/'scripts/prepare_spu_ring_paired.py',ROOT/'scripts/prepare_spu_global_ring.py',ROOT/'scripts/run_spu_guest_bank.py'),
        fault_choices=(0,1,2,3,5,6),guest_mutation=(before,after),
        supports_abort=True,scan_mode='PXS6-pending-direct-feed')
    if '--guest-routing-fault' in sys.argv:
        out=Path(sys.argv[sys.argv.index('--output')+1]);report=json.loads((out/'report.json').read_text())
        caught=bool(rc and report['exit_code']!=0 and not report['inputs_changed'] and
                    witness+' from source latch' in (out/'run.log').read_text())
        report['expected_payload_mutation_caught']=caught
        report['payload_target']=selected.payload_target
        report['passed']=caught
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print('PASS payload mutation caught' if caught else 'FAIL payload mutation not caught')
        rc=0 if caught else 1
    raise SystemExit(rc)
