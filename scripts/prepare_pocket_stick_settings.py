"""Edits for prepare_pocket_early --stick-settings (needs --dualshock,
--pad-combos and --shoulder-setting; 2026-10-17).

Menu lists for the handheld, which has no sticks (package --stick-settings):
  "D-pad" (0x5000001C): D-pad, left stick or right stick;
  "SELECT + D-pad" (0x50000020): none, D-pad, face buttons, left stick
  (default) or right stick;
  "SELECT + Y/A" becomes "SELECT + A/B/X/Y" with more options: the face
  buttons, the D-pad, left and right stick (X up, B down, Y left, A right);
  "A/B/X/Y" (0x50000024): the face buttons themselves, or the left or right
  stick (DualShock on only).
rtl/pocket/psx_pocket_stick_map.sv does the mapping between the pad
(psx_pocket_pad_combo, which also holds back Select+D-pad and Select+X/B as
needed) and the PSX wrapper's pad_sticks and D-pad inputs.
"""

STICK_LISTS = (('D-Pad', 0x5000001C, ('D-Pad', 'Left Analog Stick', 'Right Analog Stick', 'Face Buttons'), 0),
               ('SELECT + D-Pad', 0x50000020, ('None', 'Left Analog Stick', 'Right Analog Stick', 'D-Pad', 'Face Buttons'), 1))
# Menu order of "SELECT + D-Pad" by value: None, D-Pad, Face Buttons (Select +
# Up/Down/Left/Right press X/B/Y/A: Triangle, Cross, Square, Circle), then the
# sticks (2026-10-17).
SEL_DPAD_ORDER = (0, 3, 4, 1, 2)
# Bit widths of the "D-Pad" and "SELECT + D-Pad" registers.
STICK_BITS = (2, 3)
# "A/B/X/Y" (0x50000024): the plain face buttons as buttons or as a stick
# (X up, B down, Y left, A right; listed above "D-Pad", 2026-10-17).
FACE_STICK = ('A/B/X/Y', 0x50000024, ('A/B/X/Y', 'Left Analog Stick', 'Right Analog Stick', 'D-Pad'), 0)
FACE_NAME = 'SELECT + A/B/X/Y'
# Value 6, "A/B/X/Y": Select + X/B/Y/A press the face buttons themselves, so a
# game still gets them while "A/B/X/Y" is a stick. Defaults:
# A/B/X/Y the face buttons, SELECT + A/B/X/Y the right stick (value 5).
FACE_EXTRA = ('Left Analog Stick', 'Right Analog Stick', 'A/B/X/Y')
# "SELECT + A/B/X/Y" options as (value, name), in menu order: "Face
# Buttons" (Select + A/B/X/Y press Circle/Cross/Triangle/Square) right after
# None, then "D-Pad" (value 7: Select + X up, B down, Y left, A right;
# 2026-10-17), then the shoulder pairs named by what Y and A press.
FACE_OPTIONS = ((0, 'None'), (6, 'Face Buttons'), (7, 'D-Pad'), (1, 'Y: L1 | A: R1'), (2, 'Y: L2 | A: R2'),
                (3, 'Y: L3 | A: R3'), (4, 'Left Analog Stick'), (5, 'Right Analog Stick'))
FACE_DEFAULT = 5
FACE_ROW = 3   # shoulder_set3: SHOULDER_COMBOS[1] ("SELECT + Y/A")

KEYS = {'Up': 0, 'Down': 1, 'Left': 2, 'Right': 3}
FACE_KEYS = {'Triangle': 6, 'Cross': 5, 'Square': 7, 'Circle': 4}   # Pocket X, B, Y, A


def setting(name, address, default, bits):
    """A menu list register in clk_74a; the analog packet carries its value
    to clk1x (the write may place the value in any byte)."""
    take = '|'.join(f'bridge_wr_data[{8*k+bits-1}:{8*k}]' for k in range(4))
    return (f"    reg [{bits-1}:0] {name}_set={bits}'d{default};\n"
            f"    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h{address:08x}) {name}_set<={take};\n")


def stick_settings_text(text):
    """core_top after --dualshock, --pad-combos and --shoulder-setting."""
    edits = []
    # The face row widens to 3 bits for its two stick options.
    n = FACE_ROW
    for old in (f'reg [1:0] shoulder_set{n}=', f'reg [1:0] shoulder_meta{n}=', f'shoulder_sel{n}=2\'d'):
        if text.count(old) != 1: raise ValueError('shoulder row anchor not found: ' + old)
    text = text.replace(f'reg [1:0] shoulder_set{n}=2\'d3', f'reg [2:0] shoulder_set{n}=3\'d{FACE_DEFAULT}')
    text = text.replace(f'reg [1:0] shoulder_meta{n}=2\'d', f'reg [2:0] shoulder_meta{n}=3\'d')
    text = text.replace(f'shoulder_sel{n}=2\'d', f'shoulder_sel{n}=3\'d')
    old = f'shoulder_set{n}<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];'
    edits.append((old, f'shoulder_set{n}<=bridge_wr_data[2:0]|bridge_wr_data[10:8]|bridge_wr_data[18:16]|bridge_wr_data[26:24];'))
    # The logic goes right after the shoulder rows' registers (it reads the
    # face row); its output wires are declared with pad_sticks, and the D-pad
    # wires become plain declarations assigned here, so nothing is used
    # before it is declared whatever order the base core_top has.
    regs = ''.join(setting(name, address, default, bits)
                   for name, (_, address, _, default), bits in zip(('dpad_mode', 'sel_dpad'), STICK_LISTS, STICK_BITS))
    regs += setting('face_stick', FACE_STICK[1], FACE_STICK[3], 2)
    block = ('    // --stick-settings: D-pad and Select combo stick lists (psx_pocket_stick_map).\n' + regs
             + "    assign pad_stick_config_in={face_stick_set,shoulder_set0,shoulder_set1,shoulder_set2,shoulder_set3,sel_dpad_set,dpad_mode_set};\n"
             + "    wire [1:0] dpad_mode_sel=pad_stick_config_out[1:0];\n"
             + "    wire [2:0] sel_dpad_sel=pad_stick_config_out[4:2];\n"
             + "    assign shoulder_sel3=pad_stick_config_out[7:5];\n"
             + "    assign shoulder_sel2=pad_stick_config_out[9:8];\n"
             + "    assign shoulder_sel1=pad_stick_config_out[11:10];\n"
             + "    assign shoulder_sel0=pad_stick_config_out[13:12];\n"
             + "    wire [1:0] face_stick_sel=pad_stick_config_out[15:14];\n"
             + "    // A/B/X/Y as a stick only with DualShock on, as the D-pad always; then the buttons are held back.\n"
             + "    wire face_to_stick=pad_dualshock && face_stick_sel!=2'd0 || face_stick_sel==2'd3;\n"
             + "    wire [3:0] dpad_face;   // D-Pad or Select+D-Pad = Face Buttons: up X, down B, left Y, right A\n"
             + ''.join(f'    assign joypad1_Key{k}=pocket_pad_buttons[{b}] & !face_to_stick | (shoulder_sel{n}==3\'d6 & pad_combos[{c}]) | dpad_face[{d}];\n'
                       for (k, b), c, d in zip(FACE_KEYS.items(), (4, 5, 2, 3), (0, 1, 2, 3)))
             + f"    assign pad_combo_enable={{{{2{{shoulder_sel{n}[2]}}}}, 4'b1111}};   // X/B too for the stick options\n"
             + "    assign pad_dpad_combo_enable=sel_dpad_sel!=3'd0;\n"
             + ''.join(f'    assign joypad1_Key{k}=pad_dpad_mapped[{b}];\n' for k, b in KEYS.items())
             + f'''    psx_pocket_stick_map stick_map (.dualshock(pad_dualshock),.l3r3(joypad1_KeyL3 & joypad1_KeyR3),.dpad_mode(dpad_mode_sel),.sel_dpad_mode(sel_dpad_sel),
        .face_mode(shoulder_sel{n}),.face_stick(face_stick_sel),
        .face_dirs(face_to_stick ? {{pocket_pad_buttons[4],pocket_pad_buttons[7],pocket_pad_buttons[5],pocket_pad_buttons[6]}} : 4'd0),
        .dpad(pocket_pad_buttons[3:0]),.dpad_combo(pad_dpad_combos),
        .face_combo({{pad_combos[3],pad_combos[2],pad_combos[5],pad_combos[4]}}),
        .sticks_in(pad_sticks),.sticks_out(pad_sticks_mapped),.dpad_out(pad_dpad_mapped),.face_out(dpad_face));
''')
    for old in ("    assign pad_combo_enable=6'b001111;   // Select+L/R/Y/A = L2/R2/L3/R3\n",
                "    assign pad_dpad_combo_enable=1'b0;   // Select+D-pad: --stick-settings\n"):
        edits.append((old, ''))
    edits.append(('.pad_sticks(pad_sticks),', '.pad_sticks(pad_sticks_mapped),'))
    edits.append(('    wire pad_dualshock; wire [31:0] pad_sticks;\n',
                  '    wire pad_dualshock; wire [31:0] pad_sticks;\n    wire [31:0] pad_sticks_mapped; wire [3:0] pad_dpad_mapped;\n'
                  '    wire [15:0] pad_stick_config_in, pad_stick_config_out;\n'
                  '    wire [1:0] shoulder_sel0, shoulder_sel1, shoulder_sel2; wire [2:0] shoulder_sel3;\n'))
    edits.append(('    psx_pocket_analog pad_analog (',
                  "    psx_pocket_analog #(.PASS_CONFIG(1),.CONFIG_W(16),.CONFIG_RESET(16'h02A4)) pad_analog ("))
    edits.append(('.sticks(pad_sticks));',
                  '.sticks(pad_sticks),.config_in(pad_stick_config_in),.config_out(pad_stick_config_out));'))
    for key, bit in list(KEYS.items()) + list(FACE_KEYS.items()):
        edits.append((f'    wire joypad1_Key{key}=pocket_pad_buttons[{bit}];', f'    wire joypad1_Key{key};'))
    for old, new in edits:
        if text.count(old) != 1: raise ValueError('core_top anchor not found: ' + old.strip()[:70])
        text = text.replace(old, new)
    # All input-menu fields share the analog packet. Preserve each host
    # register/write decoder; replace only its separate CPU-side samplers.
    for row, width, default in ((0, 2, 0), (1, 2, 0), (2, 2, 2), (3, 3, 3)):
        declaration = (f'    (* async_reg="true" *) reg [{width-1}:0] shoulder_meta{row}={width}\'d{default}, '
                       f'shoulder_sel{row}={width}\'d{default};\n')
        sampler = f'    always @(posedge clk1x) begin shoulder_meta{row}<=shoulder_set{row}; shoulder_sel{row}<=shoulder_meta{row}; end\n'
        if text.count(declaration) != 1 or text.count(sampler) != 1:
            raise ValueError('shoulder row synchronizer not found: ' + str(row))
        text = text.replace(declaration, '')
        text = text.replace(sampler, block if row == 3 else '')
    return text
