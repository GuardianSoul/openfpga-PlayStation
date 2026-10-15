// SPDX-License-Identifier: GPL-2.0-only
// Observe a possible half-cycle RAM read; no model writes or evaluations.
#define PsxCheckpointObserver PsxCheckpointEventObserver
#include "psx_checkpoint_observer.h"
#undef PsxCheckpointObserver

class PsxCheckpointObserver {
    PsxCheckpointEventObserver event_observer;
    std::ofstream tags;
    bool previous_clock, sampled=false;
    uint32_t sampled_address=0, sampled_data=0;
public:
    explicit PsxCheckpointObserver(const Vpsx_checkpoint& model, uint64_t time, uint32_t watch)
      : event_observer(model,time,watch), tags("tag-read-window.csv"),
        previous_clock(model.clk1x) {
        if(!tags) throw std::runtime_error("Cannot create tag read window");
        tags << "time_fs,cycles,pc_hex,enabled,sampled,cached,stall_hex,"
                "falling_address_hex,current_address_hex,falling_data_hex,current_data_hex\n";
    }
    void before(const Vpsx_checkpoint& model, uint64_t time, bool rising, bool rising2x) {
        event_observer.before(model,time,rising,rising2x);
        if(!rising) return;
        const auto& root=*model.rootp;
        const unsigned segment=root.OBS_CPU_FETCH_ADDR>>29;
        tags << time << ',' << model.cycles << ',' << std::hex << model.cpu_pc << std::dec
            << ',' << bool(root.OBS_CE && !root.OBS_RESET) << ',' << sampled
            << ',' << (segment==0 || segment==4) << ',' << std::hex << unsigned(root.OBS_CPU_STALL)
            << ',' << sampled_address << ',' << unsigned(root.OBS_CPU_TAG_ADDR)
            << ',' << sampled_data << ',' << root.OBS_CPU_TAG_DATA << std::dec << '\n';
    }
    void after(const Vpsx_checkpoint& model, uint64_t time) {
        event_observer.after(model,time);
        if(previous_clock && !model.clk1x) {
            const auto& root=*model.rootp;
            sampled_data=root.OBS_CPU_TAG_DATA;
            sampled_address=root.OBS_CPU_TAG_ADDR;
            sampled=true;
        }
        previous_clock=model.clk1x;
    }
};
