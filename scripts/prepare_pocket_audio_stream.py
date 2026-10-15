"""Private SPU-completion sideport and narrowly scoped Pocket audio SDC.

Adds no clocks/latency or arithmetic to original SPU outputs. The valid pulse
publishes a completed stereo pair, captured by the FIFO on the next clk1x
edge. The SPU start/cadence tick cannot substitute for that completion pulse.
"""
from pathlib import Path
import json
import re
import shutil

from prepare_resource_targets import digest
from build_pocket import source
from generate_pocket_audio_coeffs import generate
from tool_paths import ROOT


def replace_once(text,old,new):
    if text.count(old)!=1: raise ValueError('Audio adaptation anchor changed: '+old[:100])
    return text.replace(old,new)


def capture_spu_text(text):
    text=replace_once(text,
        "      sound_out_right      : out std_logic_vector(15 downto 0) := (others => '0');",
        "      sound_out_right      : out std_logic_vector(15 downto 0) := (others => '0');\n"
        "      sound_out_valid      : out std_logic := '0';")
    # Unique main-process default assignment, outside both reset and CE.
    text=replace_once(text,"         FifoIn_Wr               <= '0';",
        "         sound_out_valid <= '0';\n         FifoIn_Wr               <= '0';")
    text=replace_once(text,"               when 3 =>\n                  if (soundmulresult15 < -32768) then sound_out_right",
        "               when 3 =>\n                  sound_out_valid <= '1';\n"
        "                  if (soundmulresult15 < -32768) then sound_out_right")
    return text


def capture_spu_source(directory,original):
    original=Path(original).resolve(); directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'spu.vhd';path.write_text(capture_spu_text(original.read_text()))
    shutil.copy2(ROOT/'references/PSX_MiSTer/LICENSE',directory/'COPYING.GPL2')
    return {'path':str(path),'sha256':digest(path),'library':'work','assignment':'VHDL_FILE',
            'original':str(original),'original_sha256':digest(original),
            'transform':'Completed stereo pair valid at original endProcStep=3, default cleared outside CE',
            'old_port_cycles_changed':False,'game_verified':False,'hardware_verified':False}


def audio_sdc(instance='audio_stream',reset_port='reset_async',reset_register=None):
    """TimeQuest25.1: explicit node exceptions + net/skew bounds, no groups.

    set_max_delay has no -datapath_only here. Use supported max/min exceptions
    for asynchronous sampling paths plus independent net-delay/skew reports.
    Gray paths are NOT false-pathed (that would disable skew analysis).
    Flight/skew20ns < minimum pointer-launch period29.525ns, with margin.
    """
    if (not re.fullmatch(r'[a-zA-Z_][\w|]*',instance) or
        bool(reset_port)==bool(reset_register) or
        (reset_port and not re.fullmatch(r'\w+',reset_port)) or
        (reset_register and not re.fullmatch(r'[\w|*]+',reset_register))):
        raise ValueError('Literal audio hierarchy/reset name required')
    prefix='*'+instance+'*|'
    text=[(ROOT/'scripts/pocket_audio_scope.tcl').read_text()]
    for name in ('wr_gray','wr_gray_meta','rd_gray','rd_gray_meta'):
        collection=(f'[pocket_audio_gray_bus {{{prefix}}} {name[:2]}]' if name in ('wr_gray','rd_gray')
                    else f'[get_registers {{{prefix}{name}[*]}}]')
        text += [f'set audio_{name} {collection}']
        if name.endswith('_meta'):
            text += [f'if {{[get_collection_size $audio_{name}] != 7}} {{error "Missing audio {name} bus"}}']
    for origin,destination in (('wr_gray','wr_gray_meta'),('rd_gray','rd_gray_meta')):
        args=f'-from $audio_{origin} -to $audio_{destination}'
        text += [f'set_max_delay {args} 20.0',f'set_min_delay {args} 0.0',
                 f'set_net_delay -max {args} 20.0',f'set_max_skew {args} 20.0']
    for name in ('running_meta','overflow_meta'):
        text += [f'set audio_{name} [get_registers {{{prefix}{name}}}]',
                 f'if {{[get_collection_size $audio_{name}] != 1}} {{error "Missing audio {name}"}}',
                 f'set_false_path -to $audio_{name}']
    reset_from=f'[get_ports {reset_port}]' if reset_port else f'[get_registers {{{reset_register}}}]'
    text += [f'set audio_reset_receivers [get_registers {{{prefix}src_reset_pipe[*] {prefix}audio_reset_pipe[*]}}]',
             'if {[get_collection_size $audio_reset_receivers] != 6} {error "Missing audio reset synchronizers"}',
             f'set audio_reset_origin {reset_from}',
             'if {[get_collection_size $audio_reset_origin] != 1} {error "Missing registered audio reset origin"}',
             'set_false_path -from $audio_reset_origin -to $audio_reset_receivers',
             '# Meta -> sync, local reset recovery/removal and all same-domain paths remain timed.']
    return text


def apply_audio_stream(manifest,directory):
    """Opt-in final-source adaptation of a newly prepared physical budget top.

    Only writes within the caller's fresh build directory; pinned/default
    sources, helper files and old checkpoints are not changed. Require the
    existing reset-only budget contract; save-state output adapter not tested.
    """
    directory=Path(directory).resolve()
    if manifest.get('kind')!='physical-psx-budget' or not manifest.get('reset_only') or 'pocket_audio_stream' in manifest:
        raise ValueError('Audio stream requires a fresh reset-only physical budget preparation')
    if any(digest(Path(s['path']))!=s['sha256'] for s in manifest['sources']):
        raise ValueError('Audio preparation input source identity changed')
    if any(digest(directory/name)!=sha for name,sha in manifest['project_hashes'].items()):
        raise ValueError('Audio preparation input project identity changed')
    prepared=directory/'pocket-audio';prepared.mkdir(parents=True,exist_ok=False)
    before={s['path']:s['sha256'] for s in manifest['sources']}
    qsf=directory/'apf_top.qsf';qtext=qsf.read_text();replacements={}
    for name in ('spu.vhd','psx_top.vhd','psx_mister_psx_top_resource.vhd'):
        matches=[s for s in manifest['sources'] if Path(s['path']).name==name]
        if len(matches)!=1:raise ValueError('Expected exactly one final '+name)
        prior=matches[0];text=Path(prior['path']).read_text();path=prepared/name
        if name=='spu.vhd':
            replacement=capture_spu_source(prepared/'spu',prior['path']);path=Path(replacement['path'])
        else:
            if name=='psx_top.vhd':
                text=replace_once(text,
                    "      sound_out_right       : out std_logic_vector(15 downto 0) := (others => '0');",
                    "      sound_out_right       : out std_logic_vector(15 downto 0) := (others => '0');\n"
                    "      sound_out_valid       : out std_logic := '0';\n      sound_running         : out std_logic := '0';")
                text=replace_once(text,'begin \n   \n   -- reset',
                    "begin \n   process (clk1x)\n   begin\n      if rising_edge(clk1x) then\n"
                    "         sound_running <= ce and not reset_intern;\n      end if;\n   end process;\n   \n   -- reset")
                text=replace_once(text,'      sound_out_right      => sound_out_right,',
                    '      sound_out_right      => sound_out_right,\n      sound_out_valid      => sound_out_valid,')
            else:
                text=replace_once(text,'    sound_out_right : out std_logic_vector(15 downto 0)\n',
                    '    sound_out_right : out std_logic_vector(15 downto 0);\n'
                    '    sound_out_valid : out std_logic;\n    sound_running : out std_logic\n')
                text=replace_once(text,'        sound_out_right => sound_out_right,',
                    '        sound_out_right => sound_out_right,\n        sound_out_valid => sound_out_valid,\n'
                    '        sound_running => sound_running,')
            path.write_text(text);replacement=source(path)
        qtext=replace_once(qtext,prior['path'],str(path));replacements[prior['path']]=replacement
    coefficients=generate(prepared/'coefficients')
    core=directory/'core_top.sv';ctext=core.read_text()
    old='''    wire audio_sclk;
    mf_audio_pll audio_pll (.refclk(clk_74b),.rst(!power_reset_n),
        .outclk_0(audio_mclk),.outclk_1(audio_sclk),.locked());
    sound_i2s #(.SIGNED_INPUT(1)) sound (.audio_sclk(audio_sclk),
        .audio_l(sound_out_left), .audio_r(sound_out_right), .audio_lrck(audio_lrck), .audio_dac(audio_dac));'''
    new='''    wire audio_sclk, audio_locked, sound_out_valid, sound_running;
    mf_audio_pll audio_pll (.refclk(clk_74b),.rst(!power_reset_n),
        .outclk_0(audio_mclk),.outclk_1(audio_sclk),.locked(audio_locked));
    (* async_reg="true", preserve, dont_merge *) reg audio_lock_meta=0, audio_lock_sync=0;
    (* preserve, dont_merge *) reg audio_reset_async=1;
    always @(posedge clk1x or negedge cpu_locked) begin
        if(!cpu_locked) begin audio_lock_meta<=0;audio_lock_sync<=0;audio_reset_async<=1;end
        else begin
            audio_lock_meta<=audio_locked;audio_lock_sync<=audio_lock_meta;
            audio_reset_async<=ram_refresh || !audio_lock_sync;
        end
    end
    wire audio_fault;
    wire [7:0] audio_status;
    psx_audio_bridge #(.COEFFICIENT_FILE("'''+coefficients['compact_path']+'''")) audio_stream (
        .src_clk(clk1x),.audio_clk(audio_mclk),.reset_async(audio_reset_async),
        .src_running(sound_running),.src_valid(sound_out_valid),.src_frame({sound_out_right,sound_out_left}),
        .src_full(),.src_overflow(),.frame_tick(),.output_frame(),.buffered_frames(),.fault(audio_fault),.status(audio_status),
        .rate_correction(),.audio_lrck(audio_lrck),.audio_dac(audio_dac));'''
    ctext=replace_once(ctext,old,new)
    ctext=replace_once(ctext,'.sound_out_right(sound_out_right));',
        '.sound_out_right(sound_out_right),.sound_out_valid(sound_out_valid),.sound_running(sound_running));')
    # A sticky fault has a synchronized single-bit diagnostic status.
    ctext=replace_once(ctext,'    reg [31:0] diagnostic_read;',
        '''    (* async_reg="true", preserve, dont_merge *) reg audio_fault_meta=0, audio_fault_sync=0;
    always @(posedge clk_74a) begin audio_fault_meta<=audio_fault;audio_fault_sync<=audio_fault_meta;end
    reg [31:0] diagnostic_read;''')
    ctext=replace_once(ctext,'        case(bridge_addr)\n',
        "        case(bridge_addr)\n        32'h13000000: diagnostic_read={31'b0,audio_fault_sync};\n")
    core.write_text(ctext)
    added=[]
    for name in ('psx_audio_sinc.sv','psx_audio_rate.sv','psx_audio_bridge.sv','psx_audio_i2s.sv'):
        copied=prepared/name;copied.write_bytes((ROOT/'rtl/audio'/name).read_bytes());added.append(source(copied))
    qtext+=''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{s["path"]}" -library work\n' for s in added)
    qsf.write_text(qtext)
    sdc=directory/'pocket.sdc'
    constraints=audio_sdc(reset_port=None,reset_register='*|audio_reset_async')
    constraints += ['set_false_path -to [get_registers {*|audio_lock_meta *|audio_fault_meta}]']
    sdc.write_text(sdc.read_text()+'\n# Atomic stereo audio stream; no whole-domain cuts\n'+'\n'.join(constraints)+'\n')
    manifest['sources']=[replacements.get(s['path'],s) for s in manifest['sources']]+added
    for s in manifest['sources']:
        if s['path']==str(core):s['sha256']=digest(core)
    for name in manifest['project_hashes']:manifest['project_hashes'][name]=digest(directory/name)
    manifest['pocket_audio_stream']={'coefficients':coefficients,'replacements':replacements,
        'original_prepared_sources_sha256':before,'preparer_sha256':digest(Path(__file__)),
        'output_rate':48000,'source_rate':44100,'fifo_frames':64,'prefill_frames':32,
        'spu_sample_completion':'endProcStep=3, registered valid, next clk1x captures completed pair',
        'fault_diagnostic_address':'0x13000000',
        'status_bits':{'fatal':0,'overflow':1,'starvation_seen':2,'deadline':3,'not_empty':4,'FIR_ready':5,'active':6,'running':7},
        'starvation_recovery':'Retain FIFO/FIR/phase, refill32 frames; overflow/deadline remain fatal',
        'full_top_fit_verified':False,
        'full_top_timing_verified':False,'game_verified':False,'hardware_verified':False}
    manifest['limitations'].append('Audio stream opt-in: component-tested; full-top clocks/reset/game/board verification pending')
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
