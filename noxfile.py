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
    session.install("ruff==0.15.22")
    session.run("ruff", "check", ".")


@nox.session(python=False)
def webui(session: nox.Session) -> None:
    """Typecheck, test, and build the WebUI bundle into src/findata/webui/."""
    session.cd(Path(__file__).parent / "web")
    session.run("npm", "ci", external=True)
    session.run("npm", "run", "typecheck", external=True)
    session.run("npm", "test", external=True)
    session.run("npm", "run", "build", external=True)
