.PHONY: test test-python test-rtl test-references test-laxer test-gte-adapter test-cpu-gte-adapter test-cd-arithmetic test-no-turbo-cache preflight resources resource-targets resource-fit resource-cluster resource-peripherals resource-system resource-system-area resource-system-reset resource-platform resource-laxer-gte inspect-assets diagnose-bios

test: test-python test-ghdl-signed-shifts test-rtl test-references test-laxer test-gte-adapter test-cpu-gte-adapter test-cd-arithmetic test-no-turbo-cache test-system-encodings test-sim-memory-models test-disc-host test-checkpoint-host test-cd-controller test-spu-cd-mix test-spu-voices test-spu-context-ram test-gpu-divider-unit test-gpu-read-math test-gpu-coordinates test-gpu-borrowed test-dma-channel-ram test-spu-reverb-table test-gte-control-ram test-gte-matrix-ram test-gpu-poly-share test-gpu-vertex-ram test-gpu-tag-generation test-gte-multicycle test-spu-reverb-resample test-mdec-idct-pipe test-spu-guest-m10k test-gpu-mul-narrow test-gpu-half-pipe test-gte-add-table test-dma-chcr-ram test-gpu-mul-retime test-gpu-no-watchdog test-memcard-window test-gpu-area-reg test-spu-static-readback test-main-lean test-dma-m10k test-gte-m10k test-gte-shadow-m10k test-spu-engine-m10k test-gpu-read-late test-memctrl-lean test-cd-sector-pack test-cd-disc-swap test-disc-swap-seq test-cd-xa test-mdec-reference test-gte-reference test-joypad-digital test-joypad-dualshock test-joypad-dualshock-lean test-joypad-pad-table test-ntsc-fixed-vblank test-joypad-replay test-psx-apf-video test-config-cdc test-video-config-cdc test-sdram-selector test-native-sdram-spec test-spu-adsr-lean test-gpu-dy-ram test-gpu-sort-reread test-gpu-recip test-gpu-poly-stepadd test-gpu-poly-diffmux test-vram-psram test-memcard-slot test-memorymux-fifo-ram

.PHONY: test-sdram-selector test-sdram-ctrl release
.PHONY: test-native-sdram-spec check-native-sdram-spec
test-native-sdram-spec:
	python3 scripts/run_native_sdram_spec.py --self-test

# Strict specification gate; intentionally fails the current CAS2/101.6MHz
# composition. Passing detector controls is not memory timing closure.
check-native-sdram-spec:
	python3 scripts/run_native_sdram_spec.py

release:
	@test -n "$(RC_PROJECT)" -a -n "$(RC_PACKAGE)" -a -n "$(VERSION)" || \
		(echo 'make release RC_PROJECT=<fitted project> RC_PACKAGE=<package_pocket_early.py output> VERSION=x.y.z'; exit 1)
	python3 scripts/release_timing.py "$(RC_PROJECT)"
	python3 scripts/package_release.py "$(RC_PACKAGE)" --version $(VERSION)
	python3 scripts/release_source.py "$(RC_PROJECT)" --version $(VERSION)

.PHONY: test-spu-cd-queue
test: test-spu-cd-queue
test-spu-cd-queue:
	python3 scripts/run_spu_cd_queue.py
	! python3 scripts/run_spu_cd_queue.py --fault > build/spu-cd-queue-fault.log 2>&1

.PHONY: test-spu-ch3-cache
test: test-spu-ch3-cache
test-spu-ch3-cache:
	python3 scripts/run_spu_ch3_cache.py
	python3 scripts/run_spu_ch3_cheat.py

test-sdram-ctrl:
	python3 scripts/run_sdram_ctrl.py
	python3 scripts/run_sdram_ctrl.py --cas 3 --ops 2000 --seed 2 --compare-cl2
	python3 scripts/run_sdram_ctrl.py --cas 3 --ops 2000 --seed 3 --spu-first
	python3 scripts/run_sdram_ctrl.py --cas 3 --ops 300 --dclk 0.5 --tret 0.5 --expect-fail
	python3 scripts/run_sdram_ctrl.py --cas 2 --ops 300 --dclk 5 --tret 5 --no-reference --expect-fail
	python3 scripts/run_sdram_ctrl.py --cas 3 --ops 2000 --seed 4 --open-row
	python3 scripts/run_sdram_ctrl.py --cas 3 --ops 2000 --seed 6 --open-row --spu-first
	python3 scripts/run_sdram_ctrl.py --cas 3 --ops 300 --dclk 0.5 --tret 0.5 --open-row --expect-fail

test-cue-mount:
	python3 scripts/run_cue_mount.py

test-sdram-selector:
	python3 scripts/run_sdram_selector.py

.PHONY: test-gpu-cache-controls
test-gpu-cache-controls:
	python3 scripts/run_gpu_cache_controls.py

.PHONY: test-gpu-payload-timing
test-gpu-payload-timing:
	python3 scripts/run_gpu_payload_timing.py

.PHONY: test-video-config-cdc test-video-config-pipeline
test-video-config-cdc:
	python3 scripts/run_video_config_cdc.py

test-video-config-pipeline:
	python3 scripts/run_video_config_pipeline.py

.PHONY: test-config-cdc test-config-cdc-system
test-config-cdc:
	python3 scripts/run_config_cdc.py

.PHONY: test-status-cdc
test: test-status-cdc
test-status-cdc:
	python3 scripts/run_status_cdc.py

test-config-cdc-system:
	python3 scripts/run_shared_sdram_system.py --reset-only --release-init --config-cdc

.PHONY: test-gpu-borrowed
test-gpu-borrowed:
	python3 scripts/run_gpu_divider_pool.py --borrowed-inputs
	python3 scripts/run_gpu_line_share.py --borrowed-inputs

.PHONY: test-gpu-coordinates
test-gpu-coordinates:
	python3 scripts/run_gpu_line_share.py --coordinates

.PHONY: test-ghdl-signed-shifts test-joypad-digital

test-joypad-digital:
	python3 scripts/run_joypad_digital_check.py

.PHONY: test-psx-apf-video
test-psx-apf-video:
	python3 scripts/run_psx_apf_video.py

.PHONY: test-apf-blank-cdc
test: test-apf-blank-cdc
test-apf-blank-cdc:
	python3 scripts/run_apf_blank_cdc.py


test-ghdl-signed-shifts:
	python3 scripts/run_ghdl_shifts.py

.PHONY: test-system-encodings test-system test-system-fast test-sim-memory-models test-gpu-primitives test-gpu-line-share test-system-memory

.PHONY: test-disc-host test-cd-controller test-spu-cd-mix

test-disc-host:
	python3 scripts/run_disc_host.py

.PHONY: test-checkpoint-host test-system-checkpoint

test-checkpoint-host: test-disc-host
	python3 scripts/run_checkpoint_host.py

test-system-checkpoint:
	python3 scripts/run_checkpoint_checks.py

.PHONY: test-system-checkpoint-full-reset
test-system-checkpoint-full-reset:
	python3 scripts/run_checkpoint_checks.py --full-reset

.PHONY: test-pocket-checkpoint-full-reset
test-pocket-checkpoint-full-reset:
	python3 scripts/run_checkpoint_checks.py --candidate --full-reset --no-disc-lid-closed

test-cd-controller:
	python3 scripts/run_cd_controller.py

test-spu-cd-mix:
	python3 scripts/run_spu_cd_mix.py

.PHONY: test-spu-voices

test-spu-voices:
	python3 scripts/run_spu_cd_mix.py --voices

.PHONY: test-dma-channel-ram test-spu-reverb-table test-gte-control-ram test-gte-matrix-ram test-gpu-poly-share test-gpu-vertex-ram test-gpu-tag-generation test-gte-multicycle test-spu-reverb-resample test-mdec-idct-pipe test-spu-guest-m10k test-gpu-mul-narrow test-gpu-half-pipe test-gte-add-table test-dma-chcr-ram test-gpu-mul-retime test-gpu-no-watchdog test-memcard-window test-cd-disc-swap test-disc-swap-seq test-cd-xa test-cd-xa-stream test-mdec-reference test-gte-reference
test-disc-swap-seq:
	python3 scripts/run_disc_swap_seq.py

test-cd-xa-stream:
	python3 scripts/run_cd_xa_stream.py

test-gte-reference:
	python3 scripts/run_gte_reference.py

test-mdec-reference:
	python3 scripts/run_mdec_reference.py

test-cd-xa:
	python3 scripts/run_cd_xa.py

test-cd-disc-swap:
	python3 scripts/run_cd_disc_swap.py

test-vram-psram:
	python3 scripts/run_vram_psram.py --quick

test-memcard-slot:
	python3 scripts/run_memcard_slot.py --quick

test-memorymux-fifo-ram:
	python3 scripts/run_memorymux_fifo_ram.py --quick

test-memcard-window:
	python3 scripts/run_memcard_window.py

.PHONY: test-vram-psram test-memcard-slot test-memorymux-fifo-ram

.PHONY: test-save-exp1
test: test-save-exp1
test-save-exp1:
	python3 scripts/run_save_exp1.py

# Slow (GHDL SPU benches): run after changing the SPU save tap or the SPU chain.
.PHONY: test-spu-save-tap
test-spu-save-tap:
	python3 scripts/run_spu_save_tap.py

# Slow (GHDL CPU bench) and needs llvm-mc/ld.lld: run after changing the
# save handler, the trap or the EXP1 fetch path.
.PHONY: test-save-trap-cpu
test-save-trap-cpu:
	python3 scripts/build_save_fw.py --check
	python3 scripts/run_save_trap_cpu.py

.PHONY: test-apf-bridge
test: test-apf-bridge
test-apf-bridge:
	python3 scripts/run_apf_bridge_checks.py

.PHONY: test-apf-pad
test: test-apf-pad
test-apf-pad:
	python3 scripts/run_apf_pad_checks.py

.PHONY: test-gpu-area-reg test-spu-static-readback test-main-lean test-dma-m10k test-gte-m10k test-gte-shadow-m10k test-spu-engine-m10k test-gpu-read-late test-memctrl-lean test-cd-sector-pack test-spu-adsr-lean test-gpu-dy-ram
test-dma-m10k:
	python3 scripts/run_dma_m10k.py

test-gte-m10k:
	python3 scripts/run_gte_m10k.py

test-gte-shadow-m10k:
	python3 scripts/run_gte_shadow_m10k.py

test-spu-engine-m10k:
	python3 scripts/run_spu_engine_m10k.py

test-gpu-read-late:
	cd scripts && python3 run_gpu_read_late.py

test-memctrl-lean:
	python3 scripts/run_memctrl_lean.py

test-cd-sector-pack:
	python3 scripts/run_cd_sector_pack.py

test-spu-adsr-lean:
	python3 scripts/run_spu_adsr_voices.py
	! python3 scripts/run_spu_adsr_voices.py --fault > build/spu-adsr-lean-fault.log 2>&1

test-gpu-dy-ram:
	cd scripts && python3 run_gpu_dy_ram.py

.PHONY: test-gpu-sort-reread
test-gpu-sort-reread:
	cd scripts && python3 run_gpu_sort_reread.py

.PHONY: test-gpu-recip
test-gpu-recip:
	cd scripts && python3 run_gpu_recip_unit.py && python3 run_gpu_recip.py

.PHONY: test-gpu-poly-stepadd
test-gpu-poly-stepadd:
	cd scripts && python3 run_gpu_poly_stepadd.py

.PHONY: test-gpu-poly-diffmux
test-gpu-poly-diffmux:
	cd scripts && python3 run_gpu_poly_diffmux.py

test-gpu-area-reg:
	python3 scripts/run_gpu_area_reg.py

test-spu-static-readback:
	python3 scripts/run_spu_static_readback.py

test-main-lean:
	python3 scripts/run_main_lean.py

# Requires the locally installed Intel simulation library (not a CI default).
.PHONY:

test-gpu-no-watchdog:
	python3 scripts/run_gpu_no_watchdog.py

test-gpu-mul-retime:
	python3 scripts/run_gpu_mul_retime.py

test-dma-chcr-ram:
	python3 scripts/run_dma_chcr_ram.py

test-gte-add-table:
	python3 scripts/run_gte_add_table.py

test-gpu-half-pipe:
	python3 scripts/run_gpu_half_pipe.py

test-gpu-mul-narrow:
	python3 scripts/run_gpu_mul_narrow.py

test-spu-guest-m10k:
	python3 scripts/run_spu_guest_m10k.py

test-mdec-idct-pipe:
	python3 scripts/run_mdec_idct_pipe.py

test-gte-multicycle:
	python3 scripts/run_gte_multicycle.py

test-spu-reverb-resample:
	python3 scripts/run_spu_reverb_resample.py

test-gte-control-ram:
	python3 scripts/run_gte_control_ram.py

test-gte-matrix-ram:
	python3 scripts/run_gte_matrix_ram.py

test-gpu-poly-share:
	python3 scripts/run_gpu_poly_share.py

test-gpu-vertex-ram:
	python3 scripts/run_gpu_vertex_ram.py

test-gpu-tag-generation:
	python3 scripts/run_gpu_tag_generation.py

test-dma-channel-ram:
	python3 scripts/run_dma_channel_ram.py

test-spu-reverb-table:
	python3 scripts/run_spu_reverb_table.py

.PHONY: test-spu-context-ram
test-spu-context-ram:
	python3 scripts/run_spu_context_checks.py
	python3 scripts/run_spu_context_checks.py --cd

.PHONY: test-gpu-divider-unit test-gpu-divider-pool

test-gpu-divider-unit:
	python3 scripts/run_gpu_divider_pool.py

test-gpu-divider-pool: test-gpu-divider-unit
	python3 scripts/run_gpu_line_share.py --divider-pool

.PHONY: test-gpu-timing-prune

test-gpu-timing-prune:
	python3 scripts/run_gpu_line_share.py --timing-prune

.PHONY: test-gpu-combined

test-gpu-combined:
	python3 scripts/run_gpu_line_share.py --combined

.PHONY: test-gpu-read-math

test-gpu-read-math:
	python3 scripts/run_gpu_read_math.py

test-system-encodings:
	python3 scripts/run_system_encodings.py

# Longer connected-system diagnostic; no private BIOS/disc data is needed.
test-system:
	python3 scripts/run_system_sim.py

test-system-fast:
	python3 scripts/run_system_verilator.py

test-gpu-primitives:
	python3 scripts/run_gpu_primitives.py

.PHONY: test-gpu-signed-clip
test-gpu-signed-clip:
	python3 scripts/run_gpu_signed_clip.py

.PHONY: test-shared-sdram-system test-shared-reset-repro test-shared-cold-entry
test-shared-sdram-system:
	python3 scripts/run_shared_sdram_system.py

test-shared-reset-repro:
	python3 scripts/run_shared_sdram_system.py --scratch-reset-repro

test-shared-cold-entry:
	python3 scripts/run_shared_sdram_system.py --unpaused-cold-only

.PHONY: test-main-reset-fence
test: test-main-reset-fence
test-main-reset-fence:
	python3 scripts/run_main_reset_fence.py

.PHONY: test-main-sync
test-main-sync:
	python3 scripts/run_main_sync_checks.py

.PHONY: test-shared-reset-delayed
test-shared-reset-delayed:
	python3 scripts/run_shared_sdram_system.py --delay-main-reset

.PHONY: test-shared-cached-spu-reset
test-shared-cached-spu-reset:
	python3 scripts/run_shared_sdram_system.py --cached-spu --reset-only --release-init --delay-main-reset

.PHONY: test-vram-credit-timing
test-vram-credit-timing:
	python3 scripts/run_vram_credit_timing.py

.PHONY: measure-spu-scalar-buffer test-spu-scalar-buffer measure-spu-scalar-buffer-area measure-spu-scalar-buffer-extended measure-spu-scalar-full-top
measure-spu-scalar-buffer:
	python3 scripts/run_spu_memory_cadence.py --scalar-buffer --scalar-buffer-ways 2 --scalar-buffer-storage M10K-dual --scalar-stream --pitch 16384

measure-spu-scalar-buffer-extended:
	python3 scripts/run_spu_memory_cadence.py --scalar-buffer --scalar-buffer-ways 2 --scalar-buffer-storage M10K-dual --scalar-extended --pitch 16384

test-spu-scalar-buffer:
	python3 scripts/run_spu_scalar_buffer_checks.py
	python3 scripts/run_spu_scalar_buffer_checks.py --storage M10K-dual

measure-spu-scalar-buffer-area:
	python3 scripts/measure_spu_scalar_buffer_area.py --storage M10K-dual

measure-spu-scalar-full-top:
	@test -n "$(CAPTURED_RUN)" || (echo 'Set CAPTURED_RUN to the cached-SPU physical project to compare'; exit 1)
	python3 scripts/measure_spu_scalar_full_top.py "$(CAPTURED_RUN)"

.PHONY: test-spu-scalar-stream measure-spu-scalar-stream measure-spu-scalar-stream-area measure-spu-scalar-stream-full-top
test-spu-scalar-stream:
	python3 scripts/run_spu_scalar_buffer_checks.py --storage M10K-stream

measure-spu-scalar-stream:
	python3 scripts/run_spu_memory_cadence.py --scalar-buffer --scalar-buffer-ways 2 --scalar-buffer-storage M10K-stream --scalar-extended --pitch 16384

measure-spu-scalar-stream-area:
	python3 scripts/measure_spu_scalar_buffer_area.py --storage M10K-stream

measure-spu-scalar-stream-full-top:
	@test -n "$(CAPTURED_RUN)" || (echo 'Set CAPTURED_RUN to the scalar dual-clock physical project to compare'; exit 1)
	python3 scripts/measure_spu_scalar_full_top.py "$(CAPTURED_RUN)" --stream

test-gpu-line-share:
	python3 scripts/run_gpu_line_share.py

test-system-memory:
	python3 scripts/run_system_verilator.py --memory-checks

.PHONY: test-system-cd-dma

test-system-cd-dma:
	python3 scripts/run_system_cd_dma.py

test-sim-memory-models:
	python3 scripts/run_sim_memory_models.py
	python3 scripts/run_internal_ram_models.py

test-python:
	python3 -m unittest discover -s tests -v

test-rtl:
	python3 scripts/run_sim.py
	python3 scripts/run_clients.py
	python3 scripts/run_pgate.py
	python3 scripts/run_cpu.py
	python3 scripts/run_psram.py

test-references:
	python3 scripts/run_reference_checks.py

test-laxer:
	python3 scripts/run_laxer_gte.py

test-gte-adapter:
	python3 scripts/run_gte_adapter.py

test-cpu-gte-adapter:
	python3 scripts/run_cpu_gte_adapter.py

test-cd-arithmetic:
	python3 scripts/run_cd_arithmetic.py

test-no-turbo-cache:
	python3 scripts/run_no_turbo_cache.py

preflight:
	python3 scripts/preflight.py

resources:
	python3 scripts/resource_report.py

resource-targets:
	python3 scripts/prepare_resource_targets.py

resource-fit:
	python3 scripts/run_resource_fit.py $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-cluster:
	python3 scripts/run_resource_fit.py --targets cluster --output build/resources/cluster $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-peripherals:
	python3 scripts/run_resource_fit.py --targets cd_top dma timer irq joypad memcard memctrl sio exp2 memorymux --output build/resources/peripherals $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-system:
	python3 scripts/run_resource_fit.py --targets psx_top --output build/resources/system $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-system-area:
	python3 scripts/run_resource_fit.py --targets psx_top --profile area --output build/resources/system-area $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-system-reset:
	python3 scripts/run_resource_fit.py --targets psx_top --profile area --reset-only --output build/resources/system-reset-only $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-platform:
	python3 scripts/run_resource_fit.py --targets platform --output build/resources/platform $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

resource-laxer-gte:
	python3 scripts/run_resource_fit.py --targets laxer_gte --profile area --output build/resources/laxer-gte $(if $(QUARTUS_BIN),--bin "$(QUARTUS_BIN)")

inspect-assets:
	python3 scripts/inspect_discs.py --hash

diagnose-bios:
	python3 scripts/run_bios.py

.PHONY: test-vram-stream
test-vram-stream:
	python3 scripts/run_vram_stream_checks.py

.PHONY: test-psram-burst test-psram-probe
test-psram-burst:
	python3 scripts/run_psram_burst.py

test-psram-probe:
	python3 scripts/run_psram_probe.py

.PHONY: test-pocket-audio test-pocket-audio-sinc test-pocket-audio-bridge test-pocket-audio-i2s test-pocket-spu-capture test-pocket-audio-rate-lean test-pocket-audio-sinc-mac
test-pocket-audio: test-pocket-audio-sinc test-pocket-audio-bridge test-pocket-audio-i2s test-pocket-spu-capture test-pocket-audio-rate-lean test-pocket-audio-sinc-mac

test-pocket-audio-rate-lean:
	python3 scripts/run_pocket_audio_rate_lean.py

test-pocket-audio-sinc-mac:
	python3 scripts/run_pocket_audio_sinc_mac.py
	python3 scripts/run_pocket_audio_sinc.py --rtl rtl/audio/psx_audio_sinc_mac.sv

test-pocket-audio-sinc:
	python3 scripts/run_pocket_audio_sinc.py

.PHONY: test-pocket-audio-rom-packed test-dpram-sdp
test-pocket-audio-rom-packed:
	python3 scripts/run_pocket_audio_rom_packed.py

.PHONY: test-video-request-cdc test-video-request-pipeline
test-video-request-cdc:
	python3 scripts/run_video_request_cdc.py

test-video-request-pipeline:
	python3 scripts/run_video_request_pipeline.py

test-dpram-sdp:
	python3 scripts/run_dpram_sdp.py

test-pocket-audio-bridge:
	python3 scripts/run_pocket_audio_bridge.py

test-pocket-audio-i2s:
	python3 scripts/run_pocket_audio_i2s.py

test-pocket-spu-capture:
	python3 scripts/run_pocket_spu_capture.py

.PHONY: test-pocket-spu-bridge
test-pocket-spu-bridge:
	python3 scripts/run_pocket_spu_capture.py --audio-bridge

.PHONY: measure-spu-memory-cadence
measure-spu-memory-cadence:
	python3 scripts/run_spu_memory_cadence.py

.PHONY: measure-spu-cached-memory
measure-spu-cached-memory:
	python3 scripts/run_spu_memory_cadence.py --cached

.PHONY: test-apf-services
test-apf-services:
	python3 scripts/run_apf_services.py

.PHONY: test-pocket-bios-loader test-apf-bios-boot
test: test-pocket-bios-loader test-apf-bios-boot

test-pocket-bios-loader:
	python3 scripts/run_pocket_bios_loader.py

test-apf-bios-boot:
	python3 scripts/run_apf_bios_boot.py

.PHONY: test-main-memory
test: test-main-memory

test-main-memory:
	python3 scripts/run_main_memory.py

.PHONY: test-mister-main-adapter test-native-main-system
test: test-mister-main-adapter

test-mister-main-adapter:
	python3 scripts/run_mister_main_adapter.py

test-native-main-system:
	python3 scripts/run_native_main_system.py

.PHONY: test-cpu-cache-contract
test-cpu-cache-contract:
	python3 scripts/run_cpu_cache_contract.py

.PHONY: test-release-host-trim test-release-init
test-release-host-trim:
	python3 scripts/run_release_host_trim.py

test-release-init:
	python3 scripts/run_release_init.py

.PHONY: test-memcard-transfer
test-memcard-transfer:
	python3 scripts/run_memcard_transfer.py

.PHONY: test-memcard-pin
test-memcard-pin:
	python3 scripts/run_memcard_pin.py

.PHONY: test-cd-stream test-cd-controller-apf test-cd-swap-apf
test: test-cd-stream test-cd-controller-apf test-cd-swap-apf

test-cd-stream:
	python3 scripts/run_cd_stream.py

test-cd-controller-apf:
	python3 scripts/run_cd_controller.py --apf

test-cd-swap-apf:
	python3 scripts/run_cd_swap_apf.py

.PHONY: test-cd-menu-cdc test-cd-table-cdc test-cd-table-menu-cdc
test: test-cd-menu-cdc test-cd-table-cdc test-cd-table-menu-cdc
test-cd-menu-cdc:
	python3 scripts/run_cd_menu_cdc.py

test-cd-table-cdc:
	python3 scripts/run_cd_table_cdc.py

test-cd-table-menu-cdc:
	python3 scripts/run_cd_table_menu_cdc.py

.PHONY: test-clean-memory-cdc
test-clean-memory-cdc:
	python3 scripts/run_clean_memory_cdc.py

.PHONY: test-divider-bank
test: test-divider-bank
test-divider-bank:
	python3 scripts/run_divider_bank.py

.PHONY: test-memcard-initialize test-apf-target-arb
test-memcard-initialize:
	python3 scripts/run_memcard_initialize.py

test-apf-target-arb:
	python3 scripts/run_apf_target_arb.py

.PHONY: test-apf-video
test-apf-video:
	python3 scripts/run_apf_video.py

.PHONY: test-memcard-service
test-memcard-service:
	python3 scripts/run_memcard_service.py

.PHONY: test-memcard-os-read test-mirlo-sdram
test-memcard-os-read:
	python3 scripts/run_memcard_os_read.py

test-mirlo-sdram:
	python3 scripts/run_mirlo_sdram.py

.PHONY: test-native-sdram-system
test-native-sdram-system:
	python3 scripts/run_native_sdram_system.py

.PHONY: test-sdram-shared
test-sdram-shared:
	python3 scripts/run_sdram_shared.py

test: test-pad-combo
.PHONY: test-joypad-replay
test-joypad-replay:
	python3 scripts/run_joypad_replay.py

.PHONY: test-ntsc-fixed-vblank
test-ntsc-fixed-vblank:
	python3 scripts/run_ntsc_fixed_vblank.py

.PHONY: test-joypad-dualshock-lean
test-joypad-dualshock-lean:
	python3 scripts/run_joypad_dualshock_lean.py

.PHONY: test-joypad-pad-table
test-joypad-pad-table:
	python3 scripts/run_joypad_pad_table.py

.PHONY: test-joypad-dualshock
test-joypad-dualshock:
	python3 scripts/run_joypad_dualshock.py

.PHONY: test-pad-combo
test-pad-combo:
	python3 scripts/run_pad_combo.py

.PHONY: test-stick-map
test: test-stick-map
test-stick-map:
	iverilog -g2012 -s tb_psx_pocket_stick_map -o build/tb_stick_map sim/pocket/tb_psx_pocket_stick_map.sv rtl/pocket/psx_pocket_stick_map.sv
	vvp -n build/tb_stick_map | tee /dev/stderr | grep -q 'PASS psx_pocket_stick_map'
	python3 -m unittest tests.test_pocket_stick_settings
	mkdir -p build/tb_stick_dpad && cd build/tb_stick_dpad && ghdl -a --std=08 -frelaxed ../../references/PSX_MiSTer/rtl/pJoypad.vhd ../../rtl/pocket/joypad_pad_dualshock.vhd ../../sim/pocket/tb_joypad_stick_dpad.vhd && ghdl -e --std=08 -frelaxed tb_joypad_stick_dpad && \
		for g in true false; do ghdl -r --std=08 -frelaxed tb_joypad_stick_dpad -gSTICK_DPAD=$$g 2>&1 | grep -q "PASS stick dpad STICK_DPAD=$$g" || exit 1; done
	mkdir -p build/tb_analog_level && cd build/tb_analog_level && ghdl -a --std=08 -frelaxed ../../references/PSX_MiSTer/rtl/pJoypad.vhd ../../rtl/pocket/joypad_pad_dualshock.vhd ../../sim/pocket/tb_joypad_analog_level.vhd && ghdl -e --std=08 -frelaxed tb_joypad_analog_level && \
		for g in true false; do ghdl -r --std=08 -frelaxed tb_joypad_analog_level -gANALOG_LEVEL=$$g 2>&1 | grep -q "PASS analog level ANALOG_LEVEL=$$g" || exit 1; done

.PHONY: test-pocket-analog
test: test-pocket-analog
test-pocket-analog:
	python3 scripts/run_pocket_analog_checks.py
