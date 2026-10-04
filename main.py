"""MTGO 入口：本地万智牌标准赛制对战。

    python main.py

首次运行前若没有卡池，请先抓取：

    python -m mtg.cards.fetch_scryfall
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import tkinter  # noqa: F401
except ImportError:
    print("当前 Python 没有 tkinter，无法运行图形界面。")
    print("Windows 上通常是安装时没勾选 \"tcl/tk and IDLE\"。")
    print("可以试试其他 Python，例如：")
    print(r"  %LOCALAPPDATA%\Python\bin\python.exe main.py")
    print("或者直接双击项目里的「启动游戏.bat」，它会自动挑一个可用的 Python。")
    raise SystemExit(1)

from mtg.ui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
