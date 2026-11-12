# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Routine step "drive-state-msf" for run_save_project_cpu.py --routine-step drive-state-msf: part 1 of
prepare_save_drive_state_routine.py (msf without the +150) on its own. Fault: plus150 (the +150 back)."""
import prepare_save_drive_state_routine as step

FAULTS = {'plus150': step.FAULTS['plus150']}
fault_text = step.fault_text


def firmware_text(text):
    return step.firmware_text(text, 'msf')
