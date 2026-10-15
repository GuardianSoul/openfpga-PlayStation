"""Read the GTE translation, background, far-colour and offset vectors from LUT RAM.

TRX/Y/Z, RBK/GBK/BBK, RFC/GFC/BFC, OFX, OFY and DQB (twelve 32-bit control
registers, CTC2-written) are read only by GTESelAddPath, which picks one of
them per cycle from its selector (sel 0/1/2/8, with the MVMVA cv override)
and component id. One 16 x 32 asynchronous-read MLAB written on the original
clock edge holds them at {class, component}; every one of those SelAddPath
inputs receives the addressed word, so its selection returns exactly the
original register. Valid bits reproduce the reset clear; CFC2 readback
already comes from gte-control-ram's shadow. Requires gte-matrix-ram (its
CTC2 write ports).
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

# CTC2 register id -> table address {class, component}.
WRITES = {0x25: 0, 0x26: 1, 0x27: 2, 0x2D: 4, 0x2E: 5, 0x2F: 6, 0x35: 8, 0x36: 9, 0x37: 10,
          0x38: 12, 0x39: 13, 0x3C: 14}
PORTS = {'TRX': 'TRX', 'TRY': 'TRY', 'TRZ': 'TRZ', 'RBK': 'RBK', 'GBK': 'GBK', 'BBK': 'BBK',
         'RFC': 'RFC', 'GFC': 'GFC', 'BFC': 'BFC', 'OF0': 'OFX', 'OF1': 'OFY', 'DQB': 'DQB'}


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned GTE source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def add_table_text(text):
    if 'i_matWrData' not in text: raise ValueError('GTE add table requires gte-matrix-ram')
    cases = '\n'.join(f"\t6'h{rid:02X}: begin addWe = 1'b1; addWa = 4'd{addr}; end" for rid, addr in WRITES.items())
    ram = f'''
// Translation/background/far-colour/offset vectors in LUT RAM (gte-add-table).
(* ramstyle = "MLAB, no_rw_check" *) reg [31:0] addRam [0:15];
reg [15:0] addValid;
reg addWe; reg [3:0] addWa;
always @(*) begin
	addWe = 1'b0; addWa = 4'd0;
	case (i_matWrID)
{cases}
	default: ;
	endcase
end
always @(posedge i_clk) if (i_ce)
begin
	if (i_nRst == 1'b0) addValid <= 16'd0;
	else if (i_matWr && addWe) begin addRam[addWa] <= i_matWrData; addValid[addWa] <= 1'b1; end
end
wire [3:0] addSelEff = isMVMVA && (i_computeCtrl.addSel.sel != 4'd3) ? {{2'd0, i_instrParam.cv}} : i_computeCtrl.addSel.sel;
wire [1:0] addClass  = (addSelEff == 4'd8) ? 2'd3 : addSelEff[1:0];
wire [1:0] addComp   = (i_computeCtrl.addSel.id == 2'd0) ? 2'd0 : (i_computeCtrl.addSel.id == 2'd1) ? 2'd1 : 2'd2;
wire [3:0] addAddr   = {{addClass, addComp}};
wire [31:0] addQ     = addValid[addAddr] ? addRam[addAddr] : 32'd0;
'''
    marker = '\nwire signed [43:0]	outAddSel;'
    text = _replace(text, marker, ram + marker)
    start = text.index('GTESelAddPath selAddInst (')
    end = text.index(');', start)
    block = text[start:end]
    for port, reg in PORTS.items():
        old_start = block.index(f'.{port}')
        old_end = block.index(')', old_start) + 1
        old = block[old_start:old_end]
        if f'i_registers.{reg})' not in old.replace('\t', ' ').replace('  ', ' ').replace(' ', '') and f'i_registers.{reg}' not in old:
            raise ValueError('SelAddPath port changed: ' + port)
        block = block[:old_start] + f'.{port}\t\t(addQ)' + block[old_end:]
    return text[:start] + block + text[end:]


def apply_gte_add_table(manifest):
    target = manifest['targets']['psx_top']
    if 'gte_matrix_ram' not in target: raise ValueError('GTE add table requires gte-matrix-ram')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'GTEComputePath.sv')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gte-add-table'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'GTEComputePath.sv'; path.write_text(add_table_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='SelAddPath vectors in LUT RAM')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one GTEComputePath assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gte_add_table'] = True; manifest['gte_add_table'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
