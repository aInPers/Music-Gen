"""测试期间把乐器注册表指向临时文件，避免动用户 ~/.musicgen/instruments.json。

被 tests/ 下三个测试模块在 import musicgen 之前导入。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="musicgen_registry_"))
os.environ["MUSICGEN_INSTRUMENTS"] = str(_TMP / "instruments.json")
