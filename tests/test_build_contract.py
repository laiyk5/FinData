from __future__ import annotations

from pathlib import Path
import tomllib


def test_hatchling_build_backend_and_src_package_selection() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["build-system"] == {
        "requires": ["hatchling==1.31.0"],
        "build-backend": "hatchling.build",
    }
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/findata"]
    assert "setuptools" not in project["tool"]
