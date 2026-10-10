"""Runtime settings, read from the environment (a .env file is loaded by the runner, not here)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    onshape_access_key: str = ""
    onshape_secret_key: str = ""
    onshape_base_url: str = "https://cad.onshape.com"
    handoff_dir: Path = Path("data/handoff")
    work_dir: Path = Path("data/work")
    db_path: Path = Path("data/cad2print.sqlite")
    orca_bin: str = "/opt/orcaslicer/bin/orca-slicer"
    profiles_file: Path = Path("profiles.yaml")
    use_xvfb: bool = False
    slice_timeout_s: int = 600
    export_timeout_s: int = 120
    log_tail_lines: int = 40
    extra: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        return cls(
            onshape_access_key=e.get("ONSHAPE_ACCESS_KEY", ""),
            onshape_secret_key=e.get("ONSHAPE_SECRET_KEY", ""),
            onshape_base_url=e.get("ONSHAPE_BASE_URL", cls.onshape_base_url).rstrip("/"),
            handoff_dir=Path(e.get("HANDOFF_DIR", str(cls.handoff_dir))),
            work_dir=Path(e.get("WORK_DIR", str(cls.work_dir))),
            db_path=Path(e.get("DB_PATH", str(cls.db_path))),
            orca_bin=e.get("ORCA_BIN", cls.orca_bin),
            profiles_file=Path(e.get("PROFILES_FILE", str(cls.profiles_file))),
            use_xvfb=_bool(e.get("USE_XVFB"), cls.use_xvfb),
            slice_timeout_s=int(e.get("SLICE_TIMEOUT_S", cls.slice_timeout_s)),
            export_timeout_s=int(e.get("EXPORT_TIMEOUT_S", cls.export_timeout_s)),
            log_tail_lines=int(e.get("LOG_TAIL_LINES", cls.log_tail_lines)),
        )
