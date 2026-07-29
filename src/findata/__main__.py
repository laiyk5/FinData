"""Allow ``python -m findata`` to serve as a shortcut for the server CLI."""
import multiprocessing

multiprocessing.freeze_support()

from findata.server.server_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
