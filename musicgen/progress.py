"""终端进度条：CLI 的渲染与分析共用。"""

from __future__ import annotations

import sys
import time


class Progress:
    """终端进度条，非 TTY 环境退化为按行输出。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.is_tty = sys.stderr.isatty()
        self._last = -1.0
        self._start = time.time()

    def __call__(self, fraction: float, message: str) -> None:
        if not self.enabled:
            return
        if self.is_tty:
            if fraction - self._last < 0.02 and fraction < 1.0:
                return
            self._last = fraction
            width = 28
            filled = int(width * max(0.0, min(1.0, fraction)))
            bar = "█" * filled + "░" * (width - filled)
            sys.stderr.write(f"\r  [{bar}] {fraction * 100:5.1f}%  {message[:28]:<28}")
            sys.stderr.flush()
        elif fraction >= 1.0 or fraction - self._last >= 0.25:
            self._last = fraction
            sys.stderr.write(f"  {fraction * 100:5.1f}%  {message}\n")
            sys.stderr.flush()

    def done(self) -> None:
        if self.enabled and self.is_tty:
            sys.stderr.write("\n")
            sys.stderr.flush()
