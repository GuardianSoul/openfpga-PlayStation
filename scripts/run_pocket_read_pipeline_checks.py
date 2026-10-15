#!/usr/bin/env python3
"""Compare complete client-cycle traces and pin-level byte oracles after retiming."""
import json
from pathlib import Path
import subprocess
from memory_vectors import write_vectors
from prepare_pocket_read_pipeline import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    directory=ROOT/'build/sim/pocket-read-pipeline';directory.mkdir(parents=True,exist_ok=True)
    candidate=prepare(directory/'candidate');vector_file=directory/'vectors.txt';count=write_vectors(vector_file)
    original=ROOT/'rtl/memory/psx_sdr_phy.sv';template=ROOT/'sim/memory/tb_psx_mem_bridge.sv'
    text=template.read_text()
    if text.count('endmodule')!=1:raise ValueError('Memory oracle module changed')
    text=text.replace('endmodule','''integer trace_file;
initial trace_file=$fopen("client-trace.txt","w");
always @(negedge clk) begin
 $fwrite(trace_file,"%b %b %b %032x %b %08x %08x %08x %08x\\n",
 req_ready,rsp_valid,rsp_error,rsp_rdata,mem_rvalid,mem_rvalid?mem_rdata:32'b0,
 backend_reads,backend_writes,backend_refreshes);
end
endmodule''')
    bench=directory/'bench.sv';bench.write_text(text)
    common=[ROOT/'rtl/memory'/n for n in ('psx_mem_bridge.sv','psx_sdram.sv','psx_sdram_pins.sv')]
    common += [ROOT/'rtl/debug/psx_mem_profile.sv',bench,
        *[ROOT/'sim/memory'/n for n in ('sdr_command_model.sv','sdr_pin_model.sv','altddio_clock_model.sv')],
        ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv']
    compiler=find_tool('iverilog');runtime=find_tool('vvp');runs=[]
    for access,second_access,hz in ((2.5,2.5,2.5),(5.5,5.5,6.0),(5.5,2.5,6.0)):
        results=[]
        for name,phy in (('original',original),('retimed',Path(candidate['path']))):
            work=directory/f'{name}-{access}-{second_access}';work.mkdir(exist_ok=True)
            binary=work/'check.vvp'
            command=[compiler,'-g2012','-s','tb_psx_mem_bridge','-Ptb_psx_mem_bridge.CONTROLLER=2',
                f'-Ptb_psx_mem_bridge.PIN_T_AC={access}',f'-Ptb_psx_mem_bridge.PIN_T_AC_SECOND={second_access}',
                f'-Ptb_psx_mem_bridge.PIN_T_HZ={hz}','-o',str(binary),*map(str,common),str(phy)]
            with (work/'build.log').open('w')as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
            result=subprocess.run([runtime,str(binary),'+vectors='+str(vector_file)],cwd=work,capture_output=True,text=True,timeout=60)
            output=result.stdout+result.stderr;(work/'checks.log').write_text(output);print(name,output,end='')
            results.append({'passed':result.returncode==0 and 'transactions=3092' in output,
                'trace_sha256':digest(work/'client-trace.txt'),'trace_lines':len((work/'client-trace.txt').read_text().splitlines())})
        runs.append({'access_ns':access,'second_access_ns':second_access,'results':results,
            'cycle_traces_equal':results[0]['trace_sha256']==results[1]['trace_sha256']})
    report={'passed':all(all(r['passed'] for r in case['results']) and case['cycle_traces_equal'] for case in runs),
        'candidate':candidate,'template_sha256':digest(template),'vectors_sha256':digest(vector_file),
        'transactions_per_fixture':count,'runs':runs,'timing_verified':False,'hardware_verified':False}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Read pipeline cycle traces match:',report['passed'])
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
