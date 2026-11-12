#!/usr/bin/env python3
"""Own private-context breakpoint isolation, applied to a closed diagnostic graph."""
import argparse
import hashlib
import json
from pathlib import Path

OLD_TRACE_CPU='7044a34b0704cdc31e4204d809507322aa90a84c7901bea7a8637b1f8bd3aea2'
FIXED_TRACE_CPU='edfa7bf661028d9ca2b52bd4a48b6aa4067b9ea406872f16584c8b7eac59ca4c'
OLD_KERNEL_CPU='e3ffe9423b0ca01e054f29bacdbbd135bd523cd2f76cf222e6c08c0ba14c4704'
FIXED_KERNEL_CPU='a65699418f65efc9ec6ab6b1997b8b51bfd2265e5914cf1b5e993e8bc5e06988'
ERROR_ROM='f5d18e2f4a9316628b73bca65958ceefb591df0f1c8ae3d406b7fa4799f9df63'
OLD_LOGGER='058cfc11ad9559cc0de8e7ab0323f555814b6adce73f305fba8a88bf59107cb5'
FIXED_LOGGER='3a15144c0cc0fd4036598a211f5783bd96ea3204a990253ff45be1fd560eb41f'
BEFORE="   EXEBreakpoint <= '1' when (("
AFTER="   EXEBreakpoint <= '1' when (exceptionTrap = '0') and (("


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cpu_text(text):
    # This context must persist until RFE, rather than only the interrupt edge.
    if text.count('elsif opcode1 = x"42000010" then -- RFE ends hidden context')!=1:
        raise ValueError('Private debug isolation requires persistent hidden context')
    if text.count(BEFORE)!=1:
        raise ValueError('Expected one unmodified instruction-breakpoint condition')
    return text.replace(BEFORE,AFTER)


def compact_readout_text(text):
    if hashlib.sha256(text.encode()).hexdigest()!=OLD_LOGGER:
        raise ValueError('Requires the qualified format-5 logger')
    text=text.replace("        host_events,32'd0,32'd0,","        32'd0,32'd0,32'd0,")
    text=text.replace("        32'd8,32'd768,32'd5,MAGIC};",
                      "        128'd0}; // fixed header words are selected directly below")
    old="        else if (index_d[2:0]<6) read_data=q[index_d[2:0]*32 +: 32];\n        else read_data=0;"
    new="        else case (index_d[2:0])\n"+''.join(
        f"            3'd{i}: read_data=q[{32*i+31}:{32*i}];\n" for i in range(6))+"            default: read_data=0;\n        endcase"
    if text.count(old)!=1:raise ValueError('RAM read mux anchor changed')
    text=text.replace(old,new)
    if hashlib.sha256(text.encode()).hexdigest()!=FIXED_LOGGER:
        raise ValueError('Unexpected compact logger fingerprint')
    return text


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source,output=Path(source).resolve(),Path(output).resolve()
    result=snapshot(source,source/'fit-inputs.json',output,2)
    cpu=output/'cpu-div-shared/cpu.vhd'
    before=sha(cpu)
    expected={OLD_TRACE_CPU:FIXED_TRACE_CPU,OLD_KERNEL_CPU:FIXED_KERNEL_CPU}
    if before not in expected or sha(output/'save-states/psx_save_rom40.hex')!=ERROR_ROM:
        raise ValueError('Requires the exact qualified kernel-preserving error-only graph')
    old=cpu.read_text();new=cpu_text(old)
    assert new.replace(AFTER,BEFORE)==old
    cpu.write_text(new)
    if sha(cpu)!=expected[before]:
        raise ValueError('Unexpected fixed CPU fingerprint')
    wanted={str(cpu)}
    if before==OLD_TRACE_CPU:
        logger=output/'save-states/psx_save_trace.sv'
        compact=compact_readout_text(logger.read_text())
        if compact!=(Path(__file__).resolve().parents[1]/'rtl/pocket/psx_save_trace.sv').read_text():
            raise ValueError('Selected compact logger differs from repository source')
        logger.write_text(compact)
        wanted.add(str(logger))
    previous=result['quartus_sources']
    current=inputs(output)
    changed=[a['path'] for a,b in zip(previous,current) if a!=b]
    if set(changed)!=wanted or len(previous)!=len(current):
        raise ValueError('Private debug fix changed other selected inputs')
    hold_guard(output,current)
    result['quartus_sources']=current
    (output/'fit-inputs.json').write_text(json.dumps(result,indent=2)+'\n')
    report=dict(before_cpu_sha256=before,after_cpu_sha256=sha(cpu),changed=changed,
                cpu_inverse_byte_exact=True,compact_readout=before==OLD_TRACE_CPU,
                guest_breakpoints_preserved=True,
                source=str(source),selected=False)
    (output/'save-debug-context.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    print(json.dumps(prepare(a.source,a.output),indent=2))
