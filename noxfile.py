from __future__ import annotations

from pathlib import Path

import nox


SUPPORTED_PYTHONS = ("3.11", "3.12", "3.13", "3.14")
nox.options.default_venv_backend = "uv"


@nox.session(python=SUPPORTED_PYTHONS)
def tests(session: nox.Session) -> None:
    """Build a wheel, install it cleanly, smoke both scripts, and run the default suite."""
    wheel_directory = Path(session.create_tmp()) / "dist"
    session.run(
        "uv",
        "build",
        "--wheel",
        "--out-dir",
        str(wheel_directory),
        external=True,
    )
    wheel = next(wheel_directory.glob("findata-*.whl"))
    session.install("--force-reinstall", str(wheel), "pytest>=8", "pytest-cov>=5")
    session.run("findata", "--version")
    session.run("findata-server", "--help")
    session.run("pytest", "-q")


@nox.session(python="3.11")
def lint(session: nox.Session) -> None:
    session.install("ruff>=0.9")
    session.run("ruff", "check", ".")
