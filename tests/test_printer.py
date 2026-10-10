import json
from pathlib import Path

import pytest

from cad2print.printer import PrinterState, apply_push, may_dispatch

FIX = json.loads((Path(__file__).parent / "fixtures" / "h2s_push_full.json").read_text())


def test_full_push_parses_state_progress_and_task():
    s = apply_push(PrinterState.offline(), FIX, now=100.0)
    assert s.online and s.gcode_state == "FINISH"
    assert s.percent == 100 and s.remaining_min == 0
    assert s.task_name == "IKEA BISSA Shoe Rack Divider"
    assert s.updated_at == 100.0


def test_partial_push_only_updates_the_keys_it_carries():
    s = apply_push(PrinterState.offline(), FIX, now=100.0)
    s2 = apply_push(s, {"print": {"mc_percent": 42, "gcode_state": "RUNNING"}}, now=130.0)
    assert s2.gcode_state == "RUNNING" and s2.percent == 42
    assert s2.task_name == s.task_name and s2.remaining_min == 0
    assert s2.updated_at == 130.0


def test_non_print_messages_are_ignored_but_refresh_liveness():
    s = apply_push(PrinterState.offline(), {"info": {"command": "get_version"}}, now=5.0)
    assert s.online and s.gcode_state is None and s.updated_at == 5.0


def test_stale_state_counts_as_offline():
    s = apply_push(PrinterState.offline(), FIX, now=100.0)
    assert s.is_live(now=120.0, stale_after_s=30)
    assert not s.is_live(now=140.0, stale_after_s=30)


@pytest.mark.parametrize("gcode_state,busy", [
    ("IDLE", False), ("FINISH", False), ("FAILED", False),
    ("RUNNING", True), ("PREPARE", True), ("PAUSE", True), ("SLICING", True), ("INIT", True),
])
def test_busy_states(gcode_state, busy):
    s = apply_push(PrinterState.offline(), {"print": {"gcode_state": gcode_state}}, now=1.0)
    assert s.busy is busy


@pytest.mark.parametrize("state,bed_cleared,now,ok,reason", [
    ("IDLE", True, 10.0, True, ""),
    ("FINISH", True, 10.0, True, ""),
    ("IDLE", False, 10.0, False, "bed cleared"),
    ("RUNNING", True, 10.0, False, "printing"),
    ("PAUSE", True, 10.0, False, "printing"),
    ("IDLE", True, 500.0, False, "offline"),
])
def test_dispatch_gate(state, bed_cleared, now, ok, reason):
    s = apply_push(PrinterState.offline(), {"print": {"gcode_state": state}}, now=1.0)
    allowed, why = may_dispatch(s, bed_cleared=bed_cleared, now=now, stale_after_s=30)
    assert allowed is ok
    assert reason in why


def test_unknown_state_blocks_dispatch():
    s = apply_push(PrinterState.offline(), {"print": {"mc_percent": 3}}, now=1.0)
    allowed, why = may_dispatch(s, bed_cleared=True, now=2.0, stale_after_s=30)
    assert not allowed and "state" in why
