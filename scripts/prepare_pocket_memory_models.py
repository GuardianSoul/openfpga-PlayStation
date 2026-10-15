"""Add bounded PSRAM service times to private connected-system model copies.

The functional cache/DMA waveform implementation remains the checked MiSTer
model. A WAIT-stage budget serializes transactions, and scalar writes no longer
acknowledge on arrival. Instances can reserve a common paired-chip bus.
"""
from pathlib import Path
from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    original=ROOT/'sim/reference/mister_memory_models.sv';text=original.read_text()
    text=text.replace('module mister_sdram_model #(', 'module pocket_psram_model #(')
    text='''// Reservations use a common clk3x cycle epoch; participating instances
// must share that clock. Separate data arrays represent logical allocations,
// not independent physical buses. Host simulation only.
package pocket_psram_bus;
    longint unsigned available[0:3]='{default:0};
endpackage
'''+text
    text=text.replace('parameter MEMORY_BYTES=',
        'parameter CORE3_HZ=101606400, MEM_HZ=61875000, FIRST_TICKS=7, CDC_TICKS=6, CD_BUS_STRESS=0, HALFWORDS_PER_TICK=1, SHARED_BUS=-1, parameter MEMORY_BYTES=')
    marker='    integer refreshcnt=0;'
    assert text.count(marker)==1
    text=text.replace(marker,marker+'''
    integer service_wait=0;
    reg request_seen=0;
    integer cd_age=0, cd_pending=0;
    reg scalar_write_pending=0;
    longint unsigned serviced_bytes=0, occupied_cycles=0;
    longint unsigned service_cycle=0;
    initial if(SHARED_BUS < -1 || SHARED_BUS > 3 ||
        (HALFWORDS_PER_TICK!=1 && HALFWORDS_PER_TICK!=2)) $fatal(1,"Invalid PSRAM bus profile");
    function automatic integer extra_ticks(input integer halfwords, input integer baseline);
        longint unsigned required, start_cycle;
        begin
            required=((FIRST_TICKS+CDC_TICKS+(halfwords+HALFWORDS_PER_TICK-1)/HALFWORDS_PER_TICK)*64'(CORE3_HZ)+MEM_HZ-1)/MEM_HZ;
            start_cycle=service_cycle;
            if(SHARED_BUS>=0) begin
                if(pocket_psram_bus::available[SHARED_BUS]>start_cycle)
                    start_cycle=pocket_psram_bus::available[SHARED_BUS];
                pocket_psram_bus::available[SHARED_BUS]=start_cycle+required;
            end
            extra_ticks=start_cycle+required-service_cycle>baseline ?
                int'(start_cycle+required-service_cycle-baseline):0;
        end
    endfunction
''')
    marker='        case(state)\n            IDLE: begin'
    assert text.count(marker)==1
    text=text.replace(marker,'''        service_cycle++;
        if(state!=IDLE) occupied_cycles++;
        if(CD_BUS_STRESS) begin
            cd_age=cd_age+1;
            if(cd_age>=CORE3_HZ/150) begin cd_age=0;cd_pending=cd_pending+1176;end
        end
'''+marker)
    marker='        if(index3x && req) req_buffer<=1;'
    assert text.count(marker)==1
    text=text.replace(marker,'        if(!req) request_seen<=0;\n'
        '        if(index3x && req && !request_seen) req_buffer<=1;')
    text=text.replace('(req || req_buffer)', '(req_buffer || req && !request_seen)')
    marker='write_word(addr & 27\'h7fffffe,di,be);req_buffer<=0;'
    assert text.count(marker)==1
    text=text.replace(marker,marker+'request_seen<=1;')
    marker='req_buffer<=0;addr_buffer<=addr;rnw_buffer<=1;'
    assert text.count(marker)==1
    text=text.replace(marker,'req_buffer<=0;request_seen<=1;addr_buffer<=addr;rnw_buffer<=1;')
    marker='                if(DOREFRESH && (refresh_buffer || refreshcnt>780)) begin'
    assert text.count(marker)==1
    text=text.replace(marker,'''                if(CD_BUS_STRESS && cd_pending>0 && !req && !req_buffer && ram_dmafifo_empty) begin
                    service_wait<=extra_ticks(cd_pending>=16 ? 16:cd_pending,0);
                    serviced_bytes+=(cd_pending>=16 ? 16:cd_pending)*2;
                    cd_pending=cd_pending-(cd_pending>=16 ? 16:cd_pending);
                    rnw_buffer<=0;state<=WAIT;
                end else '''+marker.strip())
    marker="                    lastbank<=ram_dmafifo_adr[22:10];ram_dmafifo_read<=1;rnw_buffer<=0;state<=WAIT;"
    assert text.count(marker)==1
    text=text.replace(marker,marker+'\n                    service_wait<=extra_ticks(2,0);serviced_bytes+=4;')
    marker='req_buffer<=0;request_seen<=1;rnw_buffer<=0;done3x<=1;state<=WAIT;'
    assert text.count(marker)==1
    text=text.replace(marker,'req_buffer<=0;request_seen<=1;rnw_buffer<=0;scalar_write_pending<=1;state<=WAIT;\n'
        '                    service_wait<=extra_ticks(2,0);serviced_bytes+=4;')
    marker='                    dma_buffer<=ram_dma;reqprocessed3x<=ram_dma;dma_count3x<=ram_dmacnt;'
    assert text.count(marker)==1
    text=text.replace(marker,marker+'''
                    service_wait<=extra_ticks(ram_iscache ? 8:ram_dma ? (int'(ram_dmacnt)+1)*2:2,5);
                    serviced_bytes+=ram_iscache ? 16:ram_dma ? (int'(ram_dmacnt)+1)*4:4;
''')
    marker='            WAIT: state<=RW1;'
    assert text.count(marker)==1
    text=text.replace(marker,'            WAIT: if(service_wait>0) service_wait<=service_wait-1;\n'
        '                  else begin state<=RW1; if(scalar_write_pending) begin done3x<=1;scalar_write_pending<=0;end end')
    # Do not allow the reference's write-combining shortcut to bypass the
    # packet service budget for consecutive posted DMA words.
    marker='                ram_dmafifo_read<=1;state<=RW1;'
    assert text.count(marker)==1
    text=text.replace(marker,'                ram_dmafifo_read<=1;state<=WAIT;\n'
        '                service_wait<=extra_ticks(2,0);serviced_bytes+=4;')
    path=directory/'pocket_memory_models.sv';path.write_text(text)
    return {'path':str(path),'sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'layout':'selectable independent x16 chips or shared paired x32 bus',
        'memory_hz':61875000,'first_memory_ticks':7,'cdc_memory_ticks':6,
        'cd_bus_stress':'Optional raw 2x CD stream at 150x2352 bytes/s, interleaved in 32-byte chunks on the SPU chip',
        'limitations':['Conservative transaction model, not a PSRAM pin/electrical model',
            'Fixed first-access/CDC budget plus conservative functional pipeline overhead; not exact hardware latency',
            'Shared-bus reservations require a common clk3x and model FCFS contention, not the chosen hardware arbiter',
            'Internal refresh WAIT variability still needs coverage',
            'CD rings remain BRAM in the original RTL; their proposed off-chip contention is not asserted implemented']}
