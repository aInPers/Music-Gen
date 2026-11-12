"""音频分析：把成品音频还原成 ``*.musicproj`` 工程。

对外的两个入口：

* :func:`analyze` —— 完整管线（Demucs 分轨 → FFT 转谱 → 分层 → 分段 → 音色识别
  → 可选决策 → 写工程 / 简报）
* :func:`describe_result` —— 把结果格式化成 CLI 摘要

各算法模块（``spectrum`` / ``rhythm`` / ``pitch`` / ``percussion`` / ``timbre`` /
``layers`` / ``regions`` / ``effects_estimate`` / ``brief`` / ``decisions``）
也可以单独调用，方便测试与二次开发。
"""

from .audio import Audio, AudioError, load
from .pipeline import AnalysisError, AnalysisOptions, AnalysisResult, analyze, describe_result
from .rebuild import TrackDraft, write_project

__all__ = [
    "AnalysisError",
    "AnalysisOptions",
    "AnalysisResult",
    "Audio",
    "AudioError",
    "TrackDraft",
    "analyze",
    "describe_result",
    "load",
    "write_project",
]
