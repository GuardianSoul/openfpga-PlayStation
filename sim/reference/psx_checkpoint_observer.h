// SPDX-License-Identifier: GPL-2.0-only
// Read existing compiled-model members only. No extra eval or model writes.
// OBS_* bindings are generated from this model's protected Verilog/header.
#include "Vpsx_checkpoint___024root.h"

class PsxCheckpointObserver {
    std::ofstream events;
    uint32_t previous_reports, previous_status, previous_irq;
    uint64_t cpu_samples=0, enabled_samples=0, paused_samples=0;
    uint64_t memory_wait_samples=0, gpu_dma_wait_samples=0;
    uint32_t watch_address;
    void row(const Vpsx_checkpoint& model, uint64_t time, const char* event,
             uint32_t address, uint32_t value) {
        const auto& root=*model.rootp;
        events << time << ',' << model.cycles << ',' << event << ','
               << std::hex << address << ',' << value << ',' << root.OBS_GPU_STATUS
               << ',' << root.OBS_VIDEO_REPORTS << ',' << model.cpu_pc << std::dec
               << ',' << cpu_samples << ',' << enabled_samples << ',' << paused_samples
               << ',' << memory_wait_samples << ',' << gpu_dma_wait_samples << '\n';
    }
public:
    explicit PsxCheckpointObserver(const Vpsx_checkpoint& model, uint64_t time, uint32_t watch)
      : events("hardware-events.csv"),
        previous_reports(model.rootp->OBS_VIDEO_REPORTS),
        previous_status(model.rootp->OBS_GPU_STATUS),
        previous_irq(model.rootp->OBS_IRQ_INPUT |
            (model.rootp->OBS_IRQ_REQUEST ? 0x10000U : 0)), watch_address(watch) {
        if(!events) throw std::runtime_error("Cannot create observer events");
        events << "time_fs,cycles,event,address_hex,value_hex,gpustat_hex,raw_video_reports_hex,pc_hex,"
                  "cpu_samples,enabled_samples,paused_samples,memory_wait_samples,gpu_dma_wait_samples\n";
        row(model,time,"INITIAL",0,0);
    }
    void before(const Vpsx_checkpoint& model, uint64_t time, bool rising, bool rising2x) {
        const auto& root=*model.rootp;
        const bool enabled=root.OBS_CE && !root.OBS_RESET;
        if(rising) {
            cpu_samples++;
            enabled_samples+=enabled;
            paused_samples+=bool(root.OBS_CPU_PAUSED);
            memory_wait_samples+=bool(root.OBS_MEM_REQUEST && !root.OBS_MEM_DONE);
            gpu_dma_wait_samples+=bool(root.OBS_DMA_WAIT);
            if(enabled && root.OBS_GPU_WRITE && (root.OBS_BUS_ADDRESS & 15)==4)
                row(model,time,"GP1",root.OBS_BUS_ADDRESS,root.OBS_BUS_DATA);
            if(enabled && root.OBS_GPU_READ)
                row(model,time,"GPU_CPU_READ",root.OBS_BUS_ADDRESS,
                    (root.OBS_BUS_ADDRESS & 15)==4 ? root.OBS_GPU_STATUS : 0);
            if(enabled && root.OBS_IRQ_WRITE)
                row(model,time,"IRQ_CPU_WRITE",root.OBS_BUS_ADDRESS,root.OBS_BUS_DATA);
            if(enabled && root.OBS_DMA_BUS_WRITE)
                row(model,time,"DMA_CPU_WRITE",root.OBS_BUS_ADDRESS,root.OBS_BUS_DATA);
            if(watch_address!=0xffffffffU && root.OBS_RAM_ENABLE && !root.OBS_RAM_READ
                && root.OBS_RAM_ADDRESS==watch_address)
                row(model,time,"RAM_WRITE_PRESENT_1X",root.OBS_RAM_ADDRESS,root.OBS_RAM_DATA);
        }
        if(rising2x && enabled && root.OBS_INDEX2X) {
            // DMA wins over the CPU GP0 input when both are presented, exactly
            // as in gpu.vhd's input-latch process. FIFO reset/full acceptance
            // is not inferred here; these are input-latch events.
            if(root.OBS_DMA_GPU_WRITE)
                row(model,time,"DMA_GP0_LATCH",0,root.OBS_DMA_GPU_DATA);
            else if(root.OBS_GPU_WRITE && (root.OBS_BUS_ADDRESS & 15)==0)
                row(model,time,"CPU_GP0_LATCH",0,root.OBS_BUS_DATA);
        }
    }
    void after(const Vpsx_checkpoint& model, uint64_t time) {
        const auto& root=*model.rootp;
        const uint32_t reports=root.OBS_VIDEO_REPORTS;
        const uint32_t status=root.OBS_GPU_STATUS;
        const uint32_t irq=root.OBS_IRQ_INPUT;
        if((reports & 2) && !(previous_reports & 2))
            row(model,time,"RAW_VBLANK",0,reports);
        if((irq & 1) && !(previous_irq & 1))
            row(model,time,"CPU_VBLANK_INPUT",0,irq);
        if((reports ^ previous_reports) & 0xc0)
            row(model,time,"RAW_DISPLAY_FIELD",0,reports);
        if((status ^ previous_status) & 0x80402000U)
            row(model,time,"GPU_STATUS_FIELD",0,status);
        if(bool(root.OBS_IRQ_REQUEST)!=bool(previous_irq & 0x10000))
            row(model,time,"CPU_IRQ_REQUEST",0,root.OBS_IRQ_REQUEST);
        previous_reports=reports;
        previous_status=status;
        previous_irq=irq | (root.OBS_IRQ_REQUEST ? 0x10000U : 0);
    }
};
