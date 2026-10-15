// SPDX-License-Identifier: GPL-2.0-only
// Read saved counters without advancing or evaluating the model.
#include <array>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include "Vpsx_checkpoint.h"
#include "Vpsx_checkpoint___024root.h"
#include "psx_checkpoint_host.h"

int main(int argc, char** argv) {
    try {
        if(argc != 2) throw std::runtime_error("Expected one verified checkpoint");
        auto context = std::make_unique<VerilatedContext>();
        auto model = std::make_unique<Vpsx_checkpoint>(context.get());
        VerilatedRestore stream; stream.open(argv[1]);
        if(!stream.isOpen()) throw std::runtime_error("Cannot read checkpoint");
        uint64_t magic, time, falling_edges, audio_frames, audio_nonzero;
        std::array<uint64_t,4> next;
        stream >> magic >> time >> falling_edges >> audio_frames >> audio_nonzero;
        if(magic != 0x5053584348500001ULL) throw std::runtime_error("Checkpoint format differs");
        for(auto& value : next) stream >> value;
        PsxCheckpointHost host; host.restore(stream);
        stream >> *model; stream.close();
        const auto& root = *model->rootp;
        std::cout << "{\"cycles\":" << model->cycles
          << ",\"reset_busy\":" << unsigned(model->reset_busy)
          << ",\"cpu_pc\":" << model->cpu_pc
          << ",\"sectors\":" << host.sectors
          << ",\"audio_frames\":" << audio_frames
          << ",\"audio_nonzero\":" << audio_nonzero
          << ",\"main_accepted_payload_bytes\":" << root.psx_checkpoint__DOT__main_memory__DOT__serviced_bytes
          << ",\"main_model_occupied_3x_cycles\":" << root.psx_checkpoint__DOT__main_memory__DOT__occupied_cycles
          << ",\"main_elapsed_3x_cycles\":" << root.psx_checkpoint__DOT__main_memory__DOT__service_cycle
          << ",\"spu_accepted_payload_bytes\":" << root.psx_checkpoint__DOT__spu_memory__DOT__serviced_bytes
          << ",\"spu_model_occupied_3x_cycles\":" << root.psx_checkpoint__DOT__spu_memory__DOT__occupied_cycles
          << ",\"spu_elapsed_3x_cycles\":" << root.psx_checkpoint__DOT__spu_memory__DOT__service_cycle
          << ",\"vram_completed_payload_bytes\":" << root.psx_checkpoint__DOT__video_memory__DOT__serviced_bytes
          << ",\"vram_busy_2x_cycles\":" << root.psx_checkpoint__DOT__video_memory__DOT__busy_core_cycles
          << ",\"vram_requests\":" << root.psx_checkpoint__DOT__video_memory__DOT__requests
          << ",\"vram_row_hits\":" << root.psx_checkpoint__DOT__video_memory__DOT__row_hits
          << ",\"vram_row_misses\":" << root.psx_checkpoint__DOT__video_memory__DOT__row_misses
          << ",\"vram_refreshes\":" << root.psx_checkpoint__DOT__video_memory__DOT__refreshes
          << "}\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
