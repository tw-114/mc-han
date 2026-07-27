from __future__ import annotations

import importlib.util
import sys


QT_INSTALL_HINT = 'PySide6 未安装。请先运行：python -m pip install -e ".[qt]"'


def main() -> int:
    if importlib.util.find_spec("PySide6") is None:
        print(QT_INSTALL_HINT, file=sys.stderr)
        return 2

    from mc_han.qt.main_window import run_qt_app

    return run_qt_app()


if __name__ == "__main__":
    raise SystemExit(main())
