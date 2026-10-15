"""Read the GTE input vectors V0-V2 from LUT RAM.

VX0..VZ2 (MTC2/LWC2-written data registers, never written by commands) are
read only by the three GTESelPath units: unit 1 reads the X component of the
vector chosen by vcompo (= vec for MVMVA, else the microcode field), unit 2
Y and unit 3 Z. One 4 x 16 asynchronous-read MLAB per unit, written on the
original clock edge from the same CTC2/MTC2 write port as gte-matrix-ram,
feeds all three V inputs of its unit with the addressed component, so the
unit's selection returns exactly the original register. Valid bits
reproduce the reset clear; MFC2 readback already comes from gte-control-ram's
data shadow. Requires gte-matrix-ram.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned GTE source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def vector_ram_text(text):
    if 'i_matWrData' not in text: raise ValueError('GTE vector RAM requires gte-matrix-ram')
    ram = '''
// Input vectors V0-V2 in LUT RAM, one 4 x 16 table per SelPath unit (gte-vector-ram).
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] vecRam1 [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] vecRam2 [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] vecRam3 [0:3];
reg [3:0] vecValid1, vecValid2, vecValid3;
// MTC2 0/2/4 write VXYn (X low, Y high), 1/3/5 write VZn.
wire       vecXY = i_matWr && (i_matWrID <= 6'h05) && !i_matWrID[0];
wire       vecZ  = i_matWr && (i_matWrID <= 6'h05) &&  i_matWrID[0];
wire [1:0] vecWa = i_matWrID[2:1];
always @(posedge i_clk) if (i_ce)
begin
	if (i_nRst == 1'b0) begin
		vecValid1 <= 4'd0; vecValid2 <= 4'd0; vecValid3 <= 4'd0;
	end else begin
		if (vecXY) begin vecRam1[vecWa] <= i_matWrData[15:0]; vecValid1[vecWa] <= 1'b1;
		                 vecRam2[vecWa] <= i_matWrData[31:16]; vecValid2[vecWa] <= 1'b1; end
		if (vecZ)  begin vecRam3[vecWa] <= i_matWrData[15:0]; vecValid3[vecWa] <= 1'b1; end
	end
end
wire [1:0] vecSel1 = isMVMVA ? i_instrParam.vec : i_computeCtrl.sel1.vcompo;
wire [1:0] vecSel2 = isMVMVA ? i_instrParam.vec : i_computeCtrl.sel2.vcompo;
wire [1:0] vecSel3 = isMVMVA ? i_instrParam.vec : i_computeCtrl.sel3.vcompo;
wire [15:0] vecQ1 = vecValid1[vecSel1] ? vecRam1[vecSel1] : 16'd0;
wire [15:0] vecQ2 = vecValid2[vecSel2] ? vecRam2[vecSel2] : 16'd0;
wire [15:0] vecQ3 = vecValid3[vecSel3] ? vecRam3[vecSel3] : 16'd0;
'''
    marker = '\nwire signed [43:0]	outAddSel;'
    text = _replace(text, marker, ram + marker)
    for unit, axis in ((1, 'X'), (2, 'Y'), (3, 'Z')):
        start = text.index(f'GTESelPath SelMuxUnit{unit} (')
        end = text.index(');', start)
        block = text[start:end]
        for n in range(3):
            old = f'.V{n}c     (i_registers.V{axis}{n})'
            if block.count(old) != 1: raise ValueError(f'Unit {unit} vector port changed')
            block = block.replace(old, f'.V{n}c     (vecQ{unit})')
        text = text[:start] + block + text[end:]
    return text


def apply_gte_vector_ram(manifest):
    target = manifest['targets']['psx_top']
    if 'gte_matrix_ram' not in target: raise ValueError('GTE vector RAM requires gte-matrix-ram')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'GTEComputePath.sv')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gte-vector-ram'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'GTEComputePath.sv'; path.write_text(vector_ram_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='SelPath input vectors in LUT RAM')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one GTEComputePath assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gte_vector_ram'] = True; manifest['gte_vector_ram'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
