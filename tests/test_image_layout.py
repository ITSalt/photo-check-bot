"""The image layout ships both cores next to app/ (WP-BOT-01).

Rebuilds /app from the Dockerfile's COPY lines into a temp dir and imports the
vision chain there with an isolated interpreter, so a COPY missing from the
Dockerfile turns the feature off here exactly as it would in the image.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKDIR = "/app/"


def copy_lines():
    """[(source, destination)] of the Dockerfile COPY instructions (no flags used)."""
    pairs = []
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        parts = line.split()
        if parts and parts[0].upper() == "COPY":
            if len(parts) != 3 or parts[1].startswith("--"):
                raise AssertionError("unsupported COPY form: " + line)
            pairs.append((parts[1], parts[2]))
    return pairs


def build_layout(target):
    for source, destination in copy_lines():
        if not destination.startswith(WORKDIR):
            raise AssertionError("COPY outside /app: " + destination)
        dest = Path(target) / destination[len(WORKDIR):]
        src = ROOT / source
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            target_file = dest / src.name if destination.endswith("/") else dest
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target_file)


PROBE = """
import app.vision_compare as vc, core_t2, core_t3
from core_t3 import photo_compare_t3_candidate as t3
assert vc._default_core() is not None, "T2 core missing"
assert vc._default_call_provider() is t3.call_provider, "T3b missing"
print("ok")
"""


class ImageLayoutTests(unittest.TestCase):
    def test_cores_are_copied_without_public_test_folders(self):
        sources = [s.rstrip("/") for s, _ in copy_lines()]
        self.assertIn("app", sources)
        self.assertIn("core_t2/photo_compare_t2_candidate.py", sources)
        self.assertIn("core_t3/photo_compare_t3_candidate.py", sources)
        self.assertFalse([s for s in sources if "contract_tests" in s])

    def test_vision_chain_imports_from_the_image_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_layout(tmp)
            self.assertFalse(list(Path(tmp).rglob("*contract_tests*")))
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""}
            proc = subprocess.run([sys.executable, "-c", PROBE], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            self.assertEqual(proc.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
