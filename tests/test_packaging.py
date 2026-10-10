"""The Docker image installs the wheel, so templates and static files must ship in it."""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_contains_templates_and_static(tmp_path):
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--no-build-isolation",
                    "-q", "-w", str(tmp_path)], check=True, capture_output=True)
    wheel = next(tmp_path.glob("cad2print-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert "cad2print/templates/base.html" in names
    assert "cad2print/templates/_job_fragment.html" in names
    assert "cad2print/static/style.css" in names
