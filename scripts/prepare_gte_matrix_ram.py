"""Read the GTE rotation, light and light-colour matrices from LUT RAM.

Each GTESelPath unit k multiplies column k of the selected matrix and picks
one row per step, so one value per unit per cycle is needed. Three 16 x 16
asynchronous-read MLABs (one per column unit, address {matrix, row}) are
written by CTC2 on the original clock edge; a CTC2 writes two horizontally
adjacent elements, which always land in different units. Valid bits reproduce
the reset clear. The garbage matrix (mx = 3) keeps its original inputs, and
R13/R22, which it and other paths use, stay in registers. The register file's
other matrix flip-flops lose their readers. Requires gte-control-ram for CFC2.
"""
import json
from pathlib import Path
import re

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

BASES = {0: 0x20, 1: 0x28, 2: 0x30}  # rotation, light, light colour


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned GTE source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def write_decode():
    """Per unit: one write enable, address and data, decoded from the CTC2 ID."""
    cases = {1: [], 2: [], 3: []}
    for matrix, base in BASES.items():
        for element in range(9):
            reg_id = base + element // 2
            half = '[31:16]' if element % 2 else '[15:0]'
            unit = element % 3 + 1
            cases[unit].append(f"\t\t6'h{reg_id:02X}: begin matWe{unit} = 1'b1; matWa{unit} = {{2'd{matrix}, 2'd{element // 3}}}; matWd{unit} = i_matWrData{half}; end")
    lines = ['reg matWe1, matWe2, matWe3;', 'reg [3:0] matWa1, matWa2, matWa3;', 'reg [15:0] matWd1, matWd2, matWd3;', 'always @(*) begin']
    for unit in (1, 2, 3):
        lines += [f"\tmatWe{unit} = 1'b0; matWa{unit} = 4'd0; matWd{unit} = 16'd0;", '\tcase (i_matWrID)', *cases[unit], '\tdefault: ;', '\tendcase']
    lines.append('end')
    return '\n'.join(lines)


def compute_path_text(text):
    text = _replace(text, '\tinput	SgteREG			i_registers,\n',
                    '\tinput	SgteREG			i_registers,\n\tinput					i_matWr,\n\tinput	[5:0]			i_matWrID,\n\tinput	[31:0]			i_matWrData,\n')
    ram = f'''
// Matrix columns in LUT RAM: unit k reads matrix[mat][row][column k].
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] matRam1 [0:15];
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] matRam2 [0:15];
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] matRam3 [0:15];
reg [15:0] matValid1, matValid2, matValid3;
{write_decode()}
always @(posedge i_clk) if (i_ce)
begin
	if (i_nRst == 1'b0) begin
		matValid1 <= 16'd0; matValid2 <= 16'd0; matValid3 <= 16'd0;
	end else if (i_matWr) begin
		if (matWe1) begin matRam1[matWa1] <= matWd1; matValid1[matWa1] <= 1'b1; end
		if (matWe2) begin matRam2[matWa2] <= matWd2; matValid2[matWa2] <= 1'b1; end
		if (matWe3) begin matRam3[matWa3] <= matWd3; matValid3[matWa3] <= 1'b1; end
	end
end
wire [1:0] matSel1 = isMVMVA ? i_instrParam.mx : i_computeCtrl.sel1.mat;
wire [1:0] matSel2 = isMVMVA ? i_instrParam.mx : i_computeCtrl.sel2.mat;
wire [1:0] matSel3 = isMVMVA ? i_instrParam.mx : i_computeCtrl.sel3.mat;
wire [3:0] matAddr1 = {{matSel1, i_computeCtrl.sel1.selLeft[1:0]}};
wire [3:0] matAddr2 = {{matSel2, i_computeCtrl.sel2.selLeft[1:0]}};
wire [3:0] matAddr3 = {{matSel3, i_computeCtrl.sel3.selLeft[1:0]}};
wire [15:0] matQ1 = matValid1[matAddr1] ? matRam1[matAddr1] : 16'd0;
wire [15:0] matQ2 = matValid2[matAddr2] ? matRam2[matAddr2] : 16'd0;
wire [15:0] matQ3 = matValid3[matAddr3] ? matRam3[matAddr3] : 16'd0;
'''
    marker = '\nwire signed [43:0]	outAddSel;'
    text = _replace(text, marker, ram + marker)
    names = {1: ('R11', 'R21', 'R31', 'L11', 'L21', 'L31', 'LR1', 'LG1', 'LB1'),
             2: ('R12', 'R22', 'R32', 'L12', 'L22', 'L32', 'LR2', 'LG2', 'LB2'),
             3: ('R13', 'R23', 'R33', 'L13', 'L23', 'L33', 'LR3', 'LG3', 'LB3')}
    for unit, regs in names.items():
        start = text.index(f'GTESelPath SelMuxUnit{unit} (')
        end = text.index(');', start)
        block = text[start:end]
        for port, reg in zip(('MAT0_C0', 'MAT0_C1', 'MAT0_C2', 'MAT1_C0', 'MAT1_C1', 'MAT1_C2', 'MAT2_C0', 'MAT2_C1', 'MAT2_C2'), regs):
            old = f'.{port} (i_registers.{reg})'
            if block.count(old) != 1: raise ValueError(f'Unit {unit} port changed: {port}')
            block = block.replace(old, f'.{port} (matQ{unit})')
        text = text[:start] + block + text[end:]
    return text


def engine_text(text):
    return _replace(text, '\t.i_registers	(gteREG),			// Values  from Register Module.\n',
                    '\t.i_registers	(gteREG),			// Values  from Register Module.\n\t.i_matWr		(i_WritReg),\n\t.i_matWrID		(i_regID),\n\t.i_matWrData	(i_dataIn),\n')


def apply_gte_matrix_ram(manifest):
    target = manifest['targets']['psx_top']
    if 'gte_control_ram' not in target: raise ValueError('GTE matrix RAM requires gte-control-ram')
    directory = Path(target['directory']); out = directory / 'gte-matrix-ram'; out.mkdir(parents=True, exist_ok=True)
    qsf = directory / (target['top'] + '.qsf'); qtext = qsf.read_text()
    for name, transform in (('GTEComputePath.sv', compute_path_text), ('GTEEngine.sv', engine_text)):
        index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == name)
        previous = Path(target['quartus_sources'][index]['path'])
        path = out / name; path.write_text(transform(previous.read_text()))
        target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
            original=str(previous), original_sha256=digest(previous), transform='GTE matrix columns in LUT RAM')
        if qtext.count(str(previous)) != 1: raise ValueError('Expected one assignment: ' + name)
        qtext = qtext.replace(str(previous), str(path))
    qsf.write_text(qtext); target['project_sha256']['.qsf'] = digest(qsf)
    target['gte_matrix_ram'] = True; manifest['gte_matrix_ram'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
