from __future__ import annotations

from pathlib import Path

import nox


SUPPORTED_PYTHONS = ("3.11", "3.12", "3.13", "3.14")
PLUGIN_DISTRIBUTIONS = (
    "plugins/tushare/shared",
    "plugins/tushare/provider",
    "plugins/tushare/trade-cal",
    "plugins/tushare/stock-basic",
    "plugins/tushare/index-basic",
    "plugins/tushare/index-daily",
    "plugins/tushare/index-weight",
    "plugins/tushare/daily-basic",
    "plugins/tushare/fund-daily",
    "plugins/tushare/umbrella",
    "plugins/demo/provider",
    "plugins/demo/datasets/demo-hello",
    "plugins/demo/datasets/demo-random",
    "plugins/demo/datasets/umbrella",
)
nox.options.default_venv_backend = "uv"


@nox.session(python=SUPPORTED_PYTHONS)
def tests(session: nox.Session) -> None:
    """Build all wheels, install them cleanly, smoke both scripts, and run the suite."""
    wheel_directory = Path(session.create_tmp()) / "dist"
    session.run(
        "uv",
        "build",
        "--wheel",
        "--out-dir",
        str(wheel_directory),
        external=True,
    )
    for distribution in PLUGIN_DISTRIBUTIONS:
        session.run(
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(wheel_directory),
            distribution,
            external=True,
        )
    wheels = sorted(wheel_directory.glob("*.whl"))
    if len(wheels) != 1 + len(PLUGIN_DISTRIBUTIONS):
        session.error(f"expected findata and all plugin wheels, got {wheels}")
    session.install(
        "--force-reinstall", *(str(wheel) for wheel in wheels), "pytest>=8", "pytest-cov>=5"
    )
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


@nox.session(python="3.11")
def docs(session: nox.Session) -> None:
    """Build the documentation site; strict mode fails on broken links or nav."""
    session.install("zensical>=0.0.51")
    session.run("zensical", "build", "--strict")
