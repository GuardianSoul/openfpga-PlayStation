// SPDX-License-Identifier: GPL-2.0-only
// Serializable host-side equivalent of psx_disc_host.sv's sector protocol.
#pragma once
#include <array>
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <string>
#include "verilated_save.h"

struct PsxCheckpointHost {
    enum Phase : uint32_t { WAIT_START, HEADER_ON, HEADER_OFF, MOUNT,
                            IDLE, DELAY, ACK_WAIT, WORDS, TRAILER };
    uint32_t phase=WAIT_START, header=0, wait=0, word=0, sectors=0;
    uint32_t requested_lba=0, start_wait=0;
    bool saw_busy=false, mounted=false, ack=false, write=false, track_write=false;
    uint16_t data=0, track_addr=0;
    uint32_t track_data=0;
    std::array<uint16_t,1176> words{};
    std::array<uint32_t,400> metadata{};
    uint32_t sector_limit=0, index_sector=0, delay=0;
    std::ifstream image;
    std::ofstream trace;

    void open(const std::string& path, const std::string& metadata_path,
              uint32_t count, uint32_t index, uint32_t latency) {
        sector_limit=count; index_sector=index; delay=latency;
        image.open(path,std::ios::binary);
        if(!image) throw std::runtime_error("Cannot open local disc image");
        std::ifstream table(metadata_path);
        for(auto& value:metadata)
            if(!(table>>std::hex>>value)) throw std::runtime_error("Incomplete disc metadata");
        trace.open("disc-requests.csv");
        if(!trace) throw std::runtime_error("Cannot open sector trace");
    }

    // Called at the same falling edges as the original procedural host.
    void tick(bool busy, bool request, uint32_t lba) {
        switch(phase) {
        case WAIT_START:
            if(busy) saw_busy=true;
            if(saw_busy && !busy && ++start_wait==32) phase=HEADER_ON;
            break;
        case HEADER_ON:
            track_addr=header; track_data=metadata[header]; track_write=true;
            phase=HEADER_OFF; break;
        case HEADER_OFF:
            track_write=false;
            phase=++header==400 ? MOUNT:HEADER_ON; break;
        case MOUNT:
            mounted=true; phase=IDLE; break;
        case IDLE:
            if(!request) break;
            requested_lba=lba;
            if(trace.is_open()) { trace<<sectors<<','<<lba<<'\n'; trace.flush(); }
            words.fill(lba<150 ? 0:0xaaaa);
            if(lba>=150 && uint64_t(lba)<uint64_t(150)+sector_limit) {
                const uint64_t offset=(uint64_t(index_sector)+lba-150)*2352;
                image.clear(); image.seekg(offset);
                std::array<unsigned char,2352> raw{};
                if(!image.read(reinterpret_cast<char*>(raw.data()),raw.size()))
                    throw std::runtime_error("Disc sector read failed");
                for(unsigned i=0;i<1176;i++) words[i]=raw[i*2]|uint16_t(raw[i*2+1])<<8;
            }
            wait=delay;
            if(wait) phase=DELAY;
            else { ack=true; wait=2; phase=ACK_WAIT; }
            break;
        case DELAY:
            if(--wait==0) { ack=true; wait=2; phase=ACK_WAIT; }
            break;
        case ACK_WAIT:
            if(--wait==0) { word=0; data=words[word++]; write=true; phase=WORDS; }
            break;
        case WORDS:
            if(word<1176) data=words[word++];
            else { write=false; wait=2; phase=TRAILER; }
            break;
        case TRAILER:
            if(--wait==0) {
                ack=false; sectors++; phase=IDLE;
                if(request) throw std::runtime_error("Overlapping disc request");
            }
            break;
        default: throw std::runtime_error("Invalid restored disc phase");
        }
    }

    void save(VerilatedSerialize& out) const {
        out<<phase<<header<<wait<<word<<sectors<<requested_lba<<start_wait;
        out<<saw_busy<<mounted<<ack<<write<<track_write<<data<<track_addr<<track_data;
        for(auto value:words) out<<value;
    }
    void restore(VerilatedDeserialize& in) {
        in>>phase>>header>>wait>>word>>sectors>>requested_lba>>start_wait;
        in>>saw_busy>>mounted>>ack>>write>>track_write>>data>>track_addr>>track_data;
        for(auto& value:words) in>>value;
        if(phase>TRAILER || header>400 || word>1176)
            throw std::runtime_error("Invalid restored disc state");
    }
};
