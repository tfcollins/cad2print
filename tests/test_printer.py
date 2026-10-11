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


# --- AMS trays -------------------------------------------------------------------------------

AMS = json.loads((Path(__file__).parent / "fixtures" / "h2s_push_ams.json").read_text())


def test_ams_push_yields_slots_with_type_colour_and_remaining():
    s = apply_push(PrinterState.offline(), AMS, now=1.0)
    assert [t.slot for t in s.trays] == ["A1", "A2", "A3", "A4"]
    a1 = s.trays[0]
    assert (a1.material, a1.sub_brand, a1.color, a1.remain) == ("PLA", "PLA Basic", "#000000", 95)
    assert s.trays[1].remain is None, "-1 means unknown"
    assert s.trays[3].color == "#00AE42" and s.trays[3].remain == 16


def test_trays_persist_across_partial_pushes_and_update_when_resent():
    s = apply_push(PrinterState.offline(), AMS, now=1.0)
    s2 = apply_push(s, {"print": {"mc_percent": 5}}, now=2.0)
    assert s2.trays == s.trays
    changed = json.loads(json.dumps(AMS)); changed["print"]["ams"]["ams"][0]["tray"][0]["remain"] = 50
    assert apply_push(s2, changed, now=3.0).trays[0].remain == 50


def test_empty_tray_is_reported_as_empty():
    empty = {"print": {"ams": {"ams": [{"id": "0", "tray": [{"id": "0"}, {"id": "1", "tray_type": "PETG", "tray_color": "FF0000FF", "remain": 80}]}]}}}
    s = apply_push(PrinterState.offline(), empty, now=1.0)
    assert s.trays[0].empty and s.trays[0].slot == "A1"
    assert not s.trays[1].empty and s.trays[1].material == "PETG" and s.trays[1].label == "A2 PETG 80%"


def test_slot_label_includes_sub_brand_and_unknown_remaining():
    s = apply_push(PrinterState.offline(), AMS, now=1.0)
    assert s.trays[0].label == "A1 PLA Basic 95%"
    assert s.trays[1].label == "A2 PLA Basic"
