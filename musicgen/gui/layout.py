"""界面布局的保存与恢复（窗口大小、面板停靠位置、分栏尺寸、当前页面）。

配置文件：``~/.musicgen/ui.json``，读写失败都不影响使用。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def config_path() -> Path:
    """配置文件位置；``MUSICGEN_UI`` 环境变量可覆盖（测试用）。"""
    override = os.environ.get("MUSICGEN_UI")
    if override:
        return Path(override)
    return Path.home() / ".musicgen" / "ui.json"


def load_layout() -> dict:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_layout(data: dict) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
