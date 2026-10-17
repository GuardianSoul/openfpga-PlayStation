"""Edits for prepare_pocket_early --stick-settings (needs --dualshock,
--pad-combos and --shoulder-setting; 2026-10-17).

Menu lists for the handheld, which has no sticks (package --stick-settings):
  "D-pad" (0x5000001C): D-pad, left stick or right stick;
  "SELECT + D-pad" (0x50000020): none, left stick (default), right stick or
  D-pad;
  "SELECT + Y/A" becomes "SELECT + A/B/X/Y" with two more options, left and
  right stick (X up, B down, Y left, A right).
rtl/pocket/psx_pocket_stick_map.sv does the mapping between the pad
(psx_pocket_pad_combo, which also holds back Select+D-pad and Select+X/B as
needed) and the PSX wrapper's pad_sticks and D-pad inputs.
"""

STICK_LISTS = (('D-pad', 0x5000001C, ('D-pad', 'Left stick', 'Right stick'), 0),
               ('SELECT + D-pad', 0x50000020, ('None', 'Left stick', 'Right stick', 'D-pad'), 1))
FACE_NAME = 'SELECT + A/B/X/Y'
FACE_EXTRA = ('Left stick', 'Right stick')
FACE_ROW = 3   # shoulder_set3: SHOULDER_COMBOS[1] ("SELECT + Y/A")

KEYS = {'Up': 0, 'Down': 1, 'Left': 2, 'Right': 3}


def setting(name, address, default, bits):
    """A menu list register in clk_74a, synchronized into clk1x like the
    shoulder rows (the value may arrive in any byte of the write)."""
    take = '|'.join(f'bridge_wr_data[{8*k+bits-1}:{8*k}]' for k in range(4))
    return (f"    reg [{bits-1}:0] {name}_set={bits}'d{default};\n"
            f"    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h{address:08x}) {name}_set<={take};\n"
            f"    (* async_reg=\"true\" *) reg [{bits-1}:0] {name}_meta={bits}'d{default}, {name}_sel={bits}'d{default};\n"
            f"    always @(posedge clk1x) begin {name}_meta<={name}_set; {name}_sel<={name}_meta; end\n")


def stick_settings_text(text):
    """core_top after --dualshock, --pad-combos and --shoulder-setting."""
    edits = []
    # The face row widens to 3 bits for its two stick options.
    n = FACE_ROW
    for old in (f'reg [1:0] shoulder_set{n}=', f'reg [1:0] shoulder_meta{n}=', f'shoulder_sel{n}=2\'d'):
        if text.count(old) != 1: raise ValueError('shoulder row anchor not found: ' + old)
    text = text.replace(f'reg [1:0] shoulder_set{n}=2\'d', f'reg [2:0] shoulder_set{n}=3\'d')
    text = text.replace(f'reg [1:0] shoulder_meta{n}=2\'d', f'reg [2:0] shoulder_meta{n}=3\'d')
    text = text.replace(f'shoulder_sel{n}=2\'d', f'shoulder_sel{n}=3\'d')
    old = f'shoulder_set{n}<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];'
    edits.append((old, f'shoulder_set{n}<=bridge_wr_data[2:0]|bridge_wr_data[10:8]|bridge_wr_data[18:16]|bridge_wr_data[26:24];'))
    # The logic goes right after the shoulder rows' registers (it reads the
    # face row); its output wires are declared with pad_sticks, and the D-pad
    # wires become plain declarations assigned here, so nothing is used
    # before it is declared whatever order the base core_top has.
    regs = ''.join(setting(name, address, default, 2) for name, (_, address, _, default) in zip(('dpad_mode', 'sel_dpad'), STICK_LISTS))
    block = ('    // --stick-settings: D-pad and Select combo stick lists (psx_pocket_stick_map).\n' + regs
             + f"    assign pad_combo_enable={{{{2{{shoulder_sel{n}[2]}}}}, 4'b1111}};   // X/B too for the stick options\n"
             + "    assign pad_dpad_combo_enable=sel_dpad_sel!=2'd0;\n"
             + ''.join(f'    assign joypad1_Key{k}=pad_dpad_mapped[{b}];\n' for k, b in KEYS.items())
             + f'''    psx_pocket_stick_map stick_map (.dualshock(pad_dualshock),.l3r3(joypad1_KeyL3 & joypad1_KeyR3),.dpad_mode(dpad_mode_sel),.sel_dpad_mode(sel_dpad_sel),
        .face_mode(shoulder_sel{n}),.dpad(pocket_pad_buttons[3:0]),.dpad_combo(pad_dpad_combos),
        .face_combo({{pad_combos[3],pad_combos[2],pad_combos[5],pad_combos[4]}}),
        .sticks_in(pad_sticks),.sticks_out(pad_sticks_mapped),.dpad_out(pad_dpad_mapped));
''')
    for old in ("    assign pad_combo_enable=6'b001111;   // Select+L/R/Y/A = L2/R2/L3/R3\n",
                "    assign pad_dpad_combo_enable=1'b0;   // Select+D-pad: --stick-settings\n"):
        edits.append((old, ''))
    edits.append(('.pad_sticks(pad_sticks),', '.pad_sticks(pad_sticks_mapped),'))
    edits.append(('    wire pad_dualshock; wire [31:0] pad_sticks;\n',
                  '    wire pad_dualshock; wire [31:0] pad_sticks;\n    wire [31:0] pad_sticks_mapped; wire [3:0] pad_dpad_mapped;\n'))
    for key, bit in KEYS.items():
        edits.append((f'    wire joypad1_Key{key}=pocket_pad_buttons[{bit}];', f'    wire joypad1_Key{key};'))
    for old, new in edits:
        if text.count(old) != 1: raise ValueError('core_top anchor not found: ' + old.strip()[:70])
        text = text.replace(old, new)
    last = f'    always @(posedge clk1x) begin shoulder_meta{n}<=shoulder_set{n}; shoulder_sel{n}<=shoulder_meta{n}; end\n'
    if text.count(last) != 1: raise ValueError('shoulder row synchronizer not found')
    return text.replace(last, last + block)
