"""Read-only view of the Bambu printer over its LAN MQTT broker.

The printer pushes `print` status reports every second or two (and a full one after a
`pushall` request). No commands are sent except `pushall`; on authorization-control
firmware that is all an unsigned client may do, and all cad2print needs: to know whether
the printer is idle before handing the next plate to Bambu Connect.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass, replace

log = logging.getLogger(__name__)

BUSY_STATES = {"RUNNING", "PREPARE", "PAUSE", "SLICING", "INIT"}
READY_STATES = {"IDLE", "FINISH", "FAILED"}


@dataclass(frozen=True)
class PrinterState:
    online: bool
    updated_at: float  # monotonic seconds of the last message
    gcode_state: str | None = None
    percent: int | None = None
    remaining_min: int | None = None
    task_name: str | None = None

    @classmethod
    def offline(cls) -> "PrinterState":
        return cls(online=False, updated_at=0.0)

    @property
    def busy(self) -> bool:
        return self.gcode_state in BUSY_STATES

    def is_live(self, now: float, stale_after_s: float) -> bool:
        return self.online and (now - self.updated_at) <= stale_after_s


def apply_push(state: PrinterState, payload: dict, now: float) -> PrinterState:
    """Merge one MQTT report into the state; pushes are partial, so only carried keys change."""
    p = payload.get("print") if isinstance(payload, dict) else None
    new = replace(state, online=True, updated_at=now)
    if not isinstance(p, dict):
        return new
    if "gcode_state" in p:
        new = replace(new, gcode_state=str(p["gcode_state"]))
    if "mc_percent" in p:
        new = replace(new, percent=_int(p["mc_percent"]))
    if "mc_remaining_time" in p:
        new = replace(new, remaining_min=_int(p["mc_remaining_time"]))
    if "subtask_name" in p:
        new = replace(new, task_name=str(p["subtask_name"]))
    return new


def may_dispatch(state: PrinterState, *, bed_cleared: bool, now: float, stale_after_s: float) -> tuple[bool, str]:
    """Decide whether the next auto-start plate may be handed to Bambu Connect."""
    if not state.is_live(now, stale_after_s):
        return False, "printer offline (no status from the printer)"
    if state.gcode_state is None:
        return False, "printer state unknown yet"
    if state.busy:
        return False, f"printer is printing ({state.gcode_state.lower()})"
    if state.gcode_state not in READY_STATES:
        return False, f"printer state {state.gcode_state} is not ready"
    if not bed_cleared:
        return False, "waiting for the bed cleared confirmation"
    return True, ""


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class PrinterMonitor:
    """Background MQTT subscriber; `state` is always safe to read from any thread."""

    def __init__(self, host: str, serial: str, access_code: str, *, port: int = 8883,
                 stale_after_s: float = 30.0):
        self.host, self.port, self.serial, self.access_code = host, port, serial, access_code
        self.stale_after_s = stale_after_s
        self._state = PrinterState.offline()
        self._lock = threading.Lock()
        self._client = None

    @property
    def state(self) -> PrinterState:
        with self._lock:
            return self._state

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.serial and self.access_code)

    def start(self) -> None:
        if not self.enabled or self._client is not None:
            return
        import paho.mqtt.client as mqtt  # imported lazily so tests without paho still run

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"cad2print-{int(time.time())}",
                             protocol=mqtt.MQTTv311)
        client.username_pw_set("bblp", self.access_code)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.connect_async(self.host, self.port, keepalive=30)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    # paho callbacks (worker thread)
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        log.info("printer %s: mqtt connected (%s)", self.host, reason_code)
        client.subscribe(f"device/{self.serial}/report")
        client.publish(f"device/{self.serial}/request",
                       json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        log.warning("printer %s: mqtt disconnected (%s)", self.host, reason_code)
        with self._lock:
            self._state = replace(self._state, online=False)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        with self._lock:
            self._state = apply_push(self._state, payload, time.monotonic())
