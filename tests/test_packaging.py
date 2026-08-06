"""Verify the built wheel actually contains the auto-activation .pth file.

This is the single biggest packaging risk for this project: the .pth file
and the vendored cert both live outside the normal `src/zscaler_fix/*.py`
package tree (the .pth must land at the *root* of site-packages, not
inside the zscaler_fix/ directory, for Python to pick it up automatically).
Both are wired in via [tool.hatch.build.targets.wheel.force-include] in
pyproject.toml. If that mapping is ever wrong, the package would install
with no error and simply never activate - silently defeating its own
purpose. This test builds the real wheel and inspects its contents rather
than trusting the config.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_wheel_contains_pth_and_cert_files(tmp_path):
    dist_dir = tmp_path / "dist"

    result = subprocess.run(
        ["uv", "build", "--out-dir", str(dist_dir)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()

    assert "zscaler_fix.pth" in names, f".pth file missing from wheel. Contents:\n{names}"
    assert "zscaler_fix/zscaler-root-ca.pem" in names, f"cert missing from wheel. Contents:\n{names}"


def test_pth_file_content_matches_expected_import_hook():
    """The .pth must use the "import <module>" line format site.py executes.

    A malformed .pth (extra whitespace, wrong module path, etc.) would fail
    silently in the same way as a missing one - site.py just skips lines it
    doesn't recognise as either a path or an executable import statement.
    """
    pth_path = PROJECT_ROOT / "src" / "zscaler_fix.pth"
    content = pth_path.read_text(encoding="ascii").strip()
    assert content == "import zscaler_fix._bootstrap"


def test_installed_pth_actually_activates_the_fix():
    """End-to-end: build, install into a throwaway venv, and confirm activation.

    This is the strongest possible test short of a real network call - it
    proves the .pth mechanism works all the way through uv's own install
    path, not just that the file exists inside the wheel.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        venv_dir = tmp_path / "venv"

        build = subprocess.run(
            ["uv", "build", "--out-dir", str(dist_dir)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert build.returncode == 0, f"uv build failed:\n{build.stdout}\n{build.stderr}"

        wheels = list(dist_dir.glob("*.whl"))
        assert len(wheels) == 1

        venv_create = subprocess.run(
            ["uv", "venv", str(venv_dir), "--python", sys.executable],
            capture_output=True,
            text=True,
        )
        assert venv_create.returncode == 0, venv_create.stderr

        install = subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_dir), str(wheels[0])],
            capture_output=True,
            text=True,
        )
        assert install.returncode == 0, install.stderr

        venv_python = venv_dir / "bin" / "python"
        check = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import zscaler_fix; print('ACTIVE' if zscaler_fix.is_active() else 'INACTIVE')",
            ],
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stderr
        assert "ACTIVE" in check.stdout, (
            f"zscaler_fix was importable but not auto-activated - the .pth "
            f"hook did not run. stdout={check.stdout!r} stderr={check.stderr!r}"
        )
