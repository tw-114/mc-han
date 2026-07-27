from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence

from mc_han.version import get_version


QT_INSTALL_HINT = 'PySide6 未安装。请先运行：python -m pip install -e ".[qt]"'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mc-han-qt")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if importlib.util.find_spec("PySide6") is None:
        print(QT_INSTALL_HINT, file=sys.stderr)
        return 2

    from mc_han.qt.main_window import run_qt_app

    try:
        return run_qt_app(smoke_test=args.smoke_test)
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"mc-han 启动失败：{type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
