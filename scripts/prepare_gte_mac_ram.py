"""Prepare a source-bound, optional GTE MAC register-bank candidate.

Two four-word RAM copies serve the engine addend and CPU readback. Writes
use the active clk2x edge; a second, independent CPU write is forwarded and
drained on the unused edge. Requires the exact reviewed strict alternate
adapter, original arithmetic/microcode and reset-only configuration.
No release preset or QSF is changed. Whole-design LAB/timing and Pocket
qualification remain required; see docs/pocket-gte-mac-ram.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from measure_pocket_seeds import inputs
from prepare_resource_targets import UPSTREAM, digest

GUARDS = {'GTERegs.sv': 'e869586a902f5a62dc505410baaaed483db22216eec3167da1bf1ebfedfc4247', 'GTEEngine.sv': '5c7deffa97b5efe41a4ca28a05c0ea8ac1b8c608f50cb15765059ccb62a1ce5d', 'GTEComputePath.sv': '0e47f243fbffb31720867ac309a427bfcd261359ebf43bb60d7c84eeb8b499f9', 'laxer_mister_adapter.sv': '493073c26f4554f113394cf7c8832b02a7eb8af9782e29c4c5aff34dc7b63b69'}

def once(t,a,b):
 if t.count(a)!=1: raise ValueError('GTE MAC source anchor changed: '+a[:70])
 return t.replace(a,b,1)
def guard(t,name):
 if hashlib.sha256(t.encode()).hexdigest()!=GUARDS[name]:
  raise ValueError('Unreviewed GTE MAC dependency: '+name)

def regs_text(t):
 guard(t,'GTERegs.sv')
 t=once(t,'\tinput  E_REG  i_regID,','\tinput  [1:0] i_macRead,\n\toutput [31:0] o_macReadData,\n\tinput  E_REG  i_regID,')
 t=once(t,'reg signed [31:0] MAC0,MAC1,MAC2,MAC3;', '''// MAC values have two read consumers: selected addend and CPU readback.
// Same accepted CPU edge; an independent colliding CPU write is forwarded
// immediately and stored on the non-enabled clk2x edge.
(* ramstyle = "M10K, no_rw_check" *) reg [31:0] macEngine [0:3];
(* ramstyle = "M10K, no_rw_check" *) reg [31:0] macCPU [0:3];
reg [31:0] macEngine_q, macCPU_q;
reg [3:0] macValid;
reg macPending;
reg [1:0] macPendingAddr;
reg [31:0] macPendingData;
wire [1:0] macReadAddr = i_macRead == 0 ? 2'd1 : i_macRead == 1 ? 2'd2 : 2'd3;
wire [1:0] macCpuAddr = i_readRegID[1:0];
wire [31:0] macCpuValue = !macValid[macCpuAddr] ? 32'd0 :
    (macPending && macPendingAddr == macCpuAddr) ? macPendingData : macCPU_q;
// The pending CPU write drains before the next enabled engine edge.
assign o_macReadData = macValid[macReadAddr] ? macEngine_q : 32'd0;
wire signed [31:0] MAC0=macCpuValue, MAC1=macCpuValue, MAC2=macCpuValue, MAC3=macCpuValue;
always @(negedge i_clk) begin
    macEngine_q <= macEngine[macReadAddr];
    macCPU_q <= macCPU[macCpuAddr];
end''')
 for i in range(4):
  t=once(t,f'assign gteREG.MAC{i} = MAC{i};',f'assign gteREG.MAC{i} = 32\'d0; // Dedicated selected MAC read replaces the parallel internal export.')
 t=once(t,"\t\tMAC0  <= 32'd0; MAC1<= 32'd0; MAC2<= 32'd0; MAC3<= 32'd0;\n",'')
 for i in range(4):
  value='MAC0 'if i==0 else 'MAC13'
  pattern=rf'(?m)^\s*if \(\(\(i_regID == DR_MAC{i}\) & i_WritReg\) \| p_wb.wrMAC\[{i}\]\) MAC{i} <= p_wb.wrMAC\[{i}\] \? gteWR\.{value} *: i_dataIn;[^\n]*\n'
  t,n=re.subn(pattern,'',t)
  if n!=1: raise ValueError('GTE MAC write source changed: '+str(i))
 marker='// ----------------------------------------------------------------------------------------------\n// ---- CPU WRITE ONLY REGISTERS'
 if t.count(marker)!=1: raise ValueError('GTE CPU-write marker changed')
 logic='''// The initialized microcode issues at most one MAC engine write per tick.
wire macCpuWe = i_WritReg && i_regID >= DR_MAC0 && i_regID <= DR_MAC3;
wire macEngineWe = |p_wb.wrMAC;
wire [1:0] macEngineAddr = p_wb.wrMAC[0] ? 2'd0 : p_wb.wrMAC[1] ? 2'd1 : p_wb.wrMAC[2] ? 2'd2 : 2'd3;
wire [31:0] macEngineData = p_wb.wrMAC[0] ? gteWR.MAC0 : gteWR.MAC13;
// Exactly one physical write site per RAM. The active edge arbitrates
// engine vs CPU; the inactive edge drains an independently pending CPU word.
wire macStore = i_ce ? (i_nRst && (macEngineWe || macCpuWe)) : macPending;
wire [1:0] macStoreAddr = i_ce ? (macEngineWe ? macEngineAddr : i_regID[1:0]) : macPendingAddr;
wire [31:0] macStoreData = i_ce ? (macEngineWe ? macEngineData : i_dataIn) : macPendingData;
always @(posedge i_clk) if(macStore) begin
    macEngine[macStoreAddr] <= macStoreData;
    macCPU[macStoreAddr] <= macStoreData;
end
always @(posedge i_clk) begin
    if(i_ce) begin
        if(!i_nRst) begin macValid <= 4'd0; macPending <= 1'b0; end
        else begin
            macPending <= 1'b0;
            if(macEngineWe) begin
                macValid[macEngineAddr] <= 1'b1;
            end
            if(macCpuWe && (!macEngineWe || macEngineAddr != i_regID[1:0])) begin
                macValid[i_regID[1:0]] <= 1'b1;
                if(macEngineWe) begin
                    macPending <= 1'b1;
                    macPendingAddr <= i_regID[1:0];
                    macPendingData <= i_dataIn;
                end
            end
        end
    end else if(macPending) begin
        macPending <= 1'b0;
    end
end

'''
 return t.replace(marker,logic+marker)

def engine_text(t):
 guard(t,'GTEEngine.sv')
 t=once(t,'gteCtrl\t\t\tgteWR;','gteCtrl\t\t\tgteWR;\nwire [31:0] macReadData;')
 t=once(t,'PocketGTERegs GTERegs_inst (','PocketGTERegs GTERegs_inst (\n    .i_macRead(computeCtrl.addSel.id), .o_macReadData(macReadData),')
 t=once(t,'PocketGTEComputePath GTEComputePath_inst(','PocketGTEComputePath GTEComputePath_inst(\n    .i_macReadData(macReadData),')
 return t

def compute_text(t):
 guard(t,'GTEComputePath.sv')
 t=once(t,'\tinput\tSgteREG\t\t\ti_registers,','\tinput [31:0] i_macReadData,\n\tinput\tSgteREG\t\t\ti_registers,')
 for i in range(1,4):t=once(t,f'(i_registers.MAC{i})','(i_macReadData)')
 return t

TRANSFORMS={'GTERegs.sv':regs_text,'GTEEngine.sv':engine_text,'GTEComputePath.sv':compute_text}

def prepare(project, directory):
    project = Path(project).resolve()
    selected = {}
    for row in inputs(project):
        if row['assignment'] != 'SYSTEMVERILOG_FILE':
            continue
        name = Path(row['path']).name
        if name in GUARDS:
            if name in selected:
                raise ValueError('Ambiguous GTE MAC dependency: ' + name)
            selected[name] = Path(row['path'])
    if set(selected) != set(GUARDS):
        raise ValueError('Missing GTE MAC dependencies')
    for name, path in selected.items():
        if digest(path) != GUARDS[name]:
            raise ValueError('Unreviewed GTE MAC dependency: ' + name)
    changed = {name: transform(selected[name].read_text())
               for name, transform in TRANSFORMS.items()}
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, text in changed.items():
        output = directory / name
        output.write_text(text)
        rows.append(dict(source=str(selected[name]), source_sha256=GUARDS[name],
                         path=str(output), sha256=digest(output)))
    shutil.copyfile(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    if any(digest(p) != GUARDS[n] for n, p in selected.items()):
        raise ValueError('GTE dependency changed during preparation')
    report = dict(sources=rows, adapter=str(selected['laxer_mister_adapter.sv']),
                  adapter_sha256=GUARDS['laxer_mister_adapter.sv'],
                  helper_sha256=digest(Path(__file__)),
                  contract='Strict alternate engine enable; one engine MAC write per initialized microcode word; preserve independent CPU/engine writes via pending CPU word and idle-edge drain.',
                  reset_only_required=True, production_selected=False,
                  whole_design_timing_verified=False, hardware_verified=False)
    (directory / 'prepared.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.project, args.directory), indent=2))


if __name__ == '__main__':
    main()
