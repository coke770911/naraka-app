"""測試設定。

抽出 UI 的相依套件，讓不需要 tkinter 的邏輯測試在無頭環境也能跑；
``NarakaApp`` 本身仍需要顯示環境（見 test_ui.py 的 skip 條件）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # customtkinter 會 import tkinter；無頭環境缺少 tk 模組時略過
    import tkinter  # noqa: F401
except ImportError:  # pragma: no cover
    pass
