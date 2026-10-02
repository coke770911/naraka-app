"""Naraka 星格監控工具 - 進入點。

    python main.py
"""

from __future__ import annotations

import sys
import traceback

from naraka.ui.app import NarakaApp


def main() -> int:
    try:
        app = NarakaApp()
    except Exception:
        traceback.print_exc()
        return 1
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app._on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
