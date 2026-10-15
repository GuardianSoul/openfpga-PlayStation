"""Serve GTE control-register CPU readback (CFC2) from a LUT-RAM shadow.

Control registers 0x20-0x3E are written only by CTC2, one per cycle. Each write
also stores the value CFC2 returns (raw, or with the 16-bit field
sign-extended for RT33, L33, LB3, H, DQA, ZSF3 and ZSF4) into a 32 x 32
asynchronous-read MLAB with a valid bit per entry for the reset clear. FLAG
(0x3F) and the data bank still read from their registers. The registers the
compute path uses are unchanged; only the 31-way readback multiplexer goes.
"""
import json
from pathlib import Path
import re

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

SIGN_EXTENDED = (0x24, 0x2C, 0x34, 0x3A, 0x3B, 0x3D, 0x3E)


def control_ram_text(text):
    start = text.index('\t\tcase ({1\'b1, pRegID[4:0]})')
    end = text.index("\t\tdefault     : vOut = { FLAG_31, FLAGS, 12'd0 };", start)
    end = text.index('endcase', end) + len('endcase')
    cases = re.findall(r'\n\t\t(CR_\w+)\s*: vOut = ', text[start:end])
    if len(cases) != 31: raise ValueError('Pinned GTE control readback changed')
    text = text[:start] + "\t\tvOut = (pRegID[4:0] == 5'h1F) ? { FLAG_31, FLAGS, 12'd0 } : ctrlReadback;" + text[end:]
    marker = 'wire [5:0] pRegID = i_readRegID;'
    if text.count(marker) != 1: raise ValueError('Pinned GTE read port changed')
    signs = ' || '.join(f"i_regID[4:0] == 5'h{reg & 0x1F:02X}" for reg in SIGN_EXTENDED)
    shadow = f'''// CFC2 readback shadow: one CTC2 write per cycle, asynchronous read.
(* ramstyle = "MLAB, no_rw_check" *) reg [31:0] ctrlShadow [0:31];
reg [31:0] ctrlValid;
wire ctrlSignExtend = {signs};
always @(posedge i_clk) if (i_ce)
begin
	if (i_nRst == 1'b0)
		ctrlValid <= 32'd0;
	else if (i_WritReg && i_regID[5] && i_regID[4:0] != 5'h1F) begin
		ctrlShadow[i_regID[4:0]] <= ctrlSignExtend ? {{{{16{{i_dataIn[15]}}}}, i_dataIn[15:0]}} : i_dataIn;
		ctrlValid[i_regID[4:0]]  <= 1'b1;
	end
end
'''
    text = text.replace(marker, shadow + marker + "\nwire [31:0] ctrlReadback = ctrlValid[i_readRegID[4:0]] ? ctrlShadow[i_readRegID[4:0]] : 32'd0;")
    return text


def apply_gte_control_ram(manifest):
    target = manifest['targets']['psx_top']
    if 'gte_adapter' not in target: raise ValueError('GTE control RAM requires the GTE adapter')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'GTERegs.sv')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gte-control-ram'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'GTERegs.sv'; path.write_text(data_ram_text(control_ram_text(previous.read_text())))
    copy = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
                original=str(previous), original_sha256=digest(previous),
                transform='CFC2 readback of control registers and MFC2 readback of CPU-only data registers from LUT-RAM shadows')
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(str(previous)) != 1: raise ValueError('Expected one GTERegs assignment')
    qsf.write_text(text.replace(str(previous), str(path)))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['gte_control_ram'] = copy; manifest['gte_control_ram'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


# CPU-only data registers: VXY0/VZ0, VXY1/VZ1, VXY2/VZ2, RGBC, RES1, LZCS.
DATA_REGS = {0x00: 'DR_VXY0', 0x01: 'DR_VZ0_', 0x02: 'DR_VXY1', 0x03: 'DR_VZ1_', 0x04: 'DR_VXY2',
             0x05: 'DR_VZ2_', 0x06: 'DR_RGBC', 0x17: 'DR_RES1', 0x1E: 'DR_LZCS'}
DATA_SIGN_EXTENDED = (0x01, 0x03, 0x05)


def data_ram_text(text):
    """MFC2 readback of CPU-only data registers from a second MTC2-written shadow.

    Applied after control_ram_text. GTE commands never write these registers,
    so one MTC2 per cycle keeps the shadow exact.
    """
    for reg, name in DATA_REGS.items():
        pattern = rf'(\n\t\t{name}\s*: vOut = )[^;]*;'
        if len(re.findall(pattern, text)) != 1: raise ValueError('Pinned GTE data readback changed: ' + name)
        text = re.sub(pattern, r'\1dataReadback;', text)
    match = ' || '.join(f"i_regID[4:0] == 5'h{reg:02X}" for reg in DATA_REGS)
    signs = ' || '.join(f"i_regID[4:0] == 5'h{reg:02X}" for reg in DATA_SIGN_EXTENDED)
    marker = 'wire [5:0] pRegID = i_readRegID;'
    shadow = f'''// MFC2 readback shadow of CPU-only data registers.
(* ramstyle = "MLAB, no_rw_check" *) reg [31:0] dataShadow [0:31];
reg [31:0] dataValid;
wire dataTracked = {match};
wire dataSignExtend = {signs};
always @(posedge i_clk) if (i_ce)
begin
	if (i_nRst == 1'b0)
		dataValid <= 32'd0;
	else if (i_WritReg && !i_regID[5] && dataTracked) begin
		dataShadow[i_regID[4:0]] <= dataSignExtend ? {{{{16{{i_dataIn[15]}}}}, i_dataIn[15:0]}} : i_dataIn;
		dataValid[i_regID[4:0]]  <= 1'b1;
	end
end
'''
    if text.count(marker) != 1: raise ValueError('Pinned GTE read port changed')
    return text.replace(marker, shadow + marker + "\nwire [31:0] dataReadback = dataValid[i_readRegID[4:0]] ? dataShadow[i_readRegID[4:0]] : 32'd0;")
