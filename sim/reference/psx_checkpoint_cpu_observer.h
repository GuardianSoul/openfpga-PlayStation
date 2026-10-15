// SPDX-License-Identifier: GPL-2.0-only
// Additional read-only CPU-edge conditions; no evaluation or model writes.
#define PsxCheckpointObserver PsxCheckpointEventObserver
#include "psx_checkpoint_observer.h"
#undef PsxCheckpointObserver

class PsxCheckpointObserver {
    PsxCheckpointEventObserver event_observer;
    std::ofstream cpu;
public:
    explicit PsxCheckpointObserver(const Vpsx_checkpoint& model, uint64_t time, uint32_t watch)
      : event_observer(model,time,watch), cpu("cpu-conditions.csv") {
        if(!cpu) throw std::runtime_error("Cannot create CPU conditions");
        cpu << "time_fs,cycles,pc_hex,enabled,paused,stall_hex,memory_busy,memory_is_data,"
               "cache_hit,request,done,dma_gpu_active,gpustat_hex,ram_request,ram_read,ram_address_hex\n";
    }
    void before(const Vpsx_checkpoint& model, uint64_t time, bool rising, bool rising2x) {
        event_observer.before(model,time,rising,rising2x);
        if(!rising) return;
        const auto& root=*model.rootp;
        cpu << time << ',' << model.cycles << ',' << std::hex << model.cpu_pc << std::dec
            << ',' << bool(root.OBS_CE && !root.OBS_RESET)
            << ',' << bool(root.OBS_CPU_PAUSED) << ',' << std::hex << unsigned(root.OBS_CPU_STALL)
            << std::dec << ',' << bool(root.OBS_CPU_MEM_BUSY) << ',' << bool(root.OBS_CPU_MEM_DATA)
            << ',' << bool(root.OBS_CPU_CACHE_HIT) << ',' << bool(root.OBS_MEM_REQUEST)
            << ',' << bool(root.OBS_MEM_DONE) << ',' << bool(root.OBS_DMA_WAIT)
            << ',' << std::hex << root.OBS_GPU_STATUS << std::dec
            << ',' << bool(root.OBS_RAM_ENABLE) << ',' << bool(root.OBS_RAM_READ)
            << ',' << std::hex << root.OBS_RAM_ADDRESS << std::dec << '\n';
    }
    void after(const Vpsx_checkpoint& model, uint64_t time) {
        event_observer.after(model,time);
    }
};
