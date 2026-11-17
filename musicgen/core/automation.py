"""自动化曲线：多段三次贝塞尔，用来让增益、声像与效果参数随时间变化。

设计要点：

* **每段都是真正的三次贝塞尔**，控制点默认由 Catmull-Rom 切线自动生成，
  因此多点连起来是 C1 连续的光滑曲线；也可以给单个点显式指定进出控制柄。
* 曲线求值对时间做二分反解（贝塞尔的 x 分量本身是非线性的），不是简单线性插值。
* 曲线值是**相对量**：增益曲线叠加在静态增益上，声像曲线叠加后夹到 −1…+1；
  效果参数则是绝对值（曲线怎么写，参数就是什么）。
* 纯函数、无随机数，同一工程重复渲染结果一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

#: 自动控制柄的切线强度（0 = 折线，0.5 = Catmull-Rom 标准，1 = 更夸张的过冲）
DEFAULT_TENSION = 0.5
#: 贝塞尔反解的最大迭代次数（20 次二分足够到 1e-6 的精度）
_SOLVE_STEPS = 22


class CurveError(ValueError):
    """曲线格式不合法。"""


@dataclass
class CurvePoint:
    """曲线上的一个控制点。``beat`` 与 ``value`` 是它的位置。

    ``in_dx/in_dy``、``out_dx/out_dy`` 是**相对本点**的贝塞尔控制柄偏移：
    出柄朝右（正值），入柄朝左（负值）。都没给时按 Catmull-Rom 自动生成。
    """

    beat: float
    value: float
    out_dx: float | None = None
    out_dy: float | None = None
    in_dx: float | None = None
    in_dy: float | None = None

    @property
    def has_handles(self) -> bool:
        return any(v is not None for v in (self.out_dx, self.out_dy, self.in_dx, self.in_dy))


@dataclass
class Curve:
    """一条自动化曲线。"""

    target: str
    points: list[CurvePoint] = field(default_factory=list)
    mode: str = "bezier"      # bezier | linear | step
    tension: float = DEFAULT_TENSION

    # ---- 基本查询 -----------------------------------------------------
    def __post_init__(self) -> None:
        self.points.sort(key=lambda p: p.beat)
        self.mode = (self.mode or "bezier").strip().lower()

    @property
    def start_beat(self) -> float:
        return self.points[0].beat if self.points else 0.0

    @property
    def end_beat(self) -> float:
        return self.points[-1].beat if self.points else 0.0

    def is_empty(self) -> bool:
        return len(self.points) < 2

    def value_at(self, beat: float) -> float:
        """求某一拍处的曲线值；超出首尾时取端点值。"""
        pts = self.points
        if not pts:
            return 0.0
        if len(pts) == 1 or beat <= pts[0].beat:
            return pts[0].value
        if beat >= pts[-1].beat:
            return pts[-1].value
        # 找到所在段
        lo, hi = 0, len(pts) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if pts[mid].beat <= beat:
                lo = mid
            else:
                hi = mid
        return self._segment_value(lo, beat)

    def sample(self, beats: np.ndarray) -> np.ndarray:
        """批量求值（按段向量化，比逐点快很多）。"""
        beats = np.asarray(beats, dtype=np.float64)
        out = np.empty_like(beats)
        if not self.points:
            return np.zeros_like(beats)
        if len(self.points) == 1:
            out.fill(self.points[0].value)
            return out
        # 先填首尾与逐段
        out[:] = np.nan
        out[beats <= self.points[0].beat] = self.points[0].value
        out[beats >= self.points[-1].beat] = self.points[-1].value
        for index in range(len(self.points) - 1):
            left = self.points[index].beat
            right = self.points[index + 1].beat
            mask = (beats >= left) & (beats < right)
            if not np.any(mask):
                continue
            out[mask] = self._segment_batch(index, beats[mask])
        # 理论上不会剩下 NaN，兜底
        return np.nan_to_num(out, nan=self.points[-1].value)

    # ---- 内部：单段求值 -----------------------------------------------
    def _handles(self, index: int) -> tuple[tuple[float, float], tuple[float, float]]:
        """返回第 ``index`` 段的两个控制点（绝对坐标）：C1（起点侧）与 C2（终点侧）。"""
        p0 = self.points[index]
        p1 = self.points[index + 1]
        span = p1.beat - p0.beat
        # --- 起点出柄
        if p0.out_dx is not None or p0.out_dy is not None:
            c1x = p0.beat + (p0.out_dx if p0.out_dx is not None else span / 3.0)
            c1y = p0.value + (p0.out_dy or 0.0)
        else:
            slope = self._auto_slope(index)
            c1x = p0.beat + span / 3.0
            c1y = p0.value + slope * span / 3.0
        # --- 终点入柄
        if p1.in_dx is not None or p1.in_dy is not None:
            c2x = p1.beat + (p1.in_dx if p1.in_dx is not None else -span / 3.0)
            c2y = p1.value + (p1.in_dy or 0.0)
        else:
            slope = self._auto_slope(index + 1)
            c2x = p1.beat - span / 3.0
            c2y = p1.value - slope * span / 3.0
        return (c1x, c1y), (c2x, c2y)

    def _auto_slope(self, index: int) -> float:
        """Catmull-Rom 切线斜率（值/拍）。端点用单侧差分。"""
        pts = self.points
        if index <= 0:
            a, b = pts[0], pts[1]
            return 0.0 if b.beat <= a.beat else (b.value - a.value) / (b.beat - a.beat)
        if index >= len(pts) - 1:
            a, b = pts[-2], pts[-1]
            return 0.0 if b.beat <= a.beat else (b.value - a.value) / (b.beat - a.beat)
        prev, nxt = pts[index - 1], pts[index + 1]
        span = nxt.beat - prev.beat
        if span <= 0:
            return 0.0
        return self.tension * 2.0 * (nxt.value - prev.value) / span

    def _segment_value(self, index: int, beat: float) -> float:
        p0 = self.points[index]
        p1 = self.points[index + 1]
        if self.mode == "step":
            return p0.value
        if self.mode == "linear" or p0.has_handles or p1.has_handles or self.tension <= 1e-6:
            if self.mode == "linear" or self.tension <= 1e-6:
                u = (beat - p0.beat) / (p1.beat - p0.beat)
                return p0.value + (p1.value - p0.value) * u
        (c1x, c1y), (c2x, c2y) = self._handles(index)
        u = _solve_u(p0.beat, c1x, c2x, p1.beat, beat)
        return _bezier(p0.value, c1y, c2y, p1.value, u)

    def _segment_batch(self, index: int, beats: np.ndarray) -> np.ndarray:
        p0 = self.points[index]
        p1 = self.points[index + 1]
        if self.mode == "step":
            return np.full_like(beats, p0.value)
        if self.mode == "linear" or self.tension <= 1e-6:
            u = (beats - p0.beat) / (p1.beat - p0.beat)
            return p0.value + (p1.value - p0.value) * u
        (c1x, c1y), (c2x, c2y) = self._handles(index)
        # 向量化二分：x(u) 单调，直接迭代
        lo = np.zeros_like(beats)
        hi = np.ones_like(beats)
        for _ in range(_SOLVE_STEPS):
            mid = (lo + hi) * 0.5
            x = _bezier(p0.beat, c1x, c2x, p1.beat, mid)
            too_small = x < beats
            lo = np.where(too_small, mid, lo)
            hi = np.where(too_small, hi, mid)
        u = (lo + hi) * 0.5
        return _bezier(p0.value, c1y, c2y, p1.value, u)

    # ---- 输出 ---------------------------------------------------------
    def format_points(self) -> str:
        return "; ".join(f"{p.beat:g}:{p.value:g}" for p in self.points)

    def describe(self) -> str:
        mode = {"bezier": "贝塞尔", "linear": "线性", "step": "阶跃"}.get(self.mode, self.mode)
        return f"{self.target}（{mode}，{len(self.points)} 点）"


def _bezier(p0: float, c1: float, c2: float, p3: float, u: np.ndarray | float):
    inv = 1.0 - u
    return inv * inv * inv * p0 + 3.0 * inv * inv * u * c1 + 3.0 * inv * u * u * c2 + u * u * u * p3


def _solve_u(x0: float, c1: float, c2: float, x3: float, target: float) -> float:
    """在 [0,1] 上二分求 u，使 x(u) == target。x 分量对 u 单调。"""
    lo, hi = 0.0, 1.0
    for _ in range(_SOLVE_STEPS):
        mid = (lo + hi) * 0.5
        if float(_bezier(x0, c1, c2, x3, mid)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) * 0.5


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_points(text: str) -> list[CurvePoint]:
    """解析简写 ``0:0; 8:-6; 16:0``（也认逗号分隔与 ``@`` 代替冒号）。"""
    points: list[CurvePoint] = []
    for chunk in str(text or "").replace(",", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        sep = ":" if ":" in chunk else ("@" if "@" in chunk else None)
        if sep is None:
            raise CurveError(f"控制点要写成「拍:值」，收到 {chunk!r}")
        left, right = chunk.split(sep, 1)
        try:
            beat = float(left.strip())
            value = float(right.strip())
        except ValueError:
            raise CurveError(f"控制点不是数字：{chunk!r}") from None
        points.append(CurvePoint(beat, value))
    return points


def parse_handle(text: str | None) -> tuple[float | None, float | None]:
    """解析 ``2,-1`` 形式的控制柄偏移。"""
    if text is None or str(text).strip() == "":
        return None, None
    parts = str(text).replace(";", ",").split(",")
    if len(parts) != 2:
        raise CurveError(f"控制柄要写成「Δ拍,Δ值」，收到 {text!r}")
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        raise CurveError(f"控制柄不是数字：{text!r}") from None


def curve_from_element(el, label: str, issues: list) -> "Curve | None":
    """从 ``<automation>`` 节点建曲线。支持简写 points 与逐点 ``<point>`` 两种写法。"""
    target = (el.get("target") or el.get("param") or "").strip()
    if not target:
        issues.append(_issue("error", label, "automation 缺少 target（要控制哪个参数）"))
        return None
    mode = (el.get("mode") or el.get("curve") or "bezier").strip().lower()
    if mode in ("bezier", "贝塞尔", "smooth", "平滑"):
        mode = "bezier"
    elif mode in ("linear", "line", "线性", "直线"):
        mode = "linear"
    elif mode in ("step", "hold", "阶跃"):
        mode = "step"
    else:
        issues.append(_issue("warning", label, f"未知曲线模式 {mode!r}，按贝塞尔处理"))
        mode = "bezier"
    try:
        tension = float(el.get("tension") or DEFAULT_TENSION)
    except ValueError:
        tension = DEFAULT_TENSION
    tension = max(0.0, min(1.5, tension))

    points: list[CurvePoint] = []
    raw_points = el.get("points")
    if raw_points:
        try:
            points.extend(parse_points(raw_points))
        except CurveError as exc:
            issues.append(_issue("warning", label, str(exc)))
    for child in el:
        if child.tag.lower() != "point":
            continue
        try:
            beat = float(child.get("time") or child.get("beat") or child.get("t") or "nan")
            value = float(child.get("value") or child.get("v") or "nan")
        except ValueError:
            issues.append(_issue("warning", label, "曲线点缺少有效的 time / value，已跳过"))
            continue
        if math.isnan(beat) or math.isnan(value):
            issues.append(_issue("warning", label, "曲线点缺少有效的 time / value，已跳过"))
            continue
        try:
            out_dx, out_dy = parse_handle(child.get("out"))
            in_dx, in_dy = parse_handle(child.get("in"))
        except CurveError as exc:
            issues.append(_issue("warning", label, str(exc)))
            out_dx = out_dy = in_dx = in_dy = None
        points.append(CurvePoint(beat, value, out_dx, out_dy, in_dx, in_dy))

    if len(points) < 2:
        issues.append(_issue("warning", label, f"曲线 {target} 少于 2 个点，已忽略"))
        return None
    return Curve(target=target, points=points, mode=mode, tension=tension)


def _issue(level: str, where: str, message: str):
    # 延迟导入避免循环依赖（project 导入 automation）
    from .project import Issue

    return Issue(level, where, message)


def curve_to_element(parent, curve: Curve, tag: str = "automation") -> None:
    """把曲线写回 XML（显式控制柄的点用子元素展开）。"""
    import xml.etree.ElementTree as ET

    attrs = {"target": curve.target, "mode": curve.mode, "points": curve.format_points()}
    if abs(curve.tension - DEFAULT_TENSION) > 1e-9:
        attrs["tension"] = f"{curve.tension:g}"
    node = ET.SubElement(parent, tag, attrs)
    # 有显式控制柄的点需要单独写，简写 points 表达不了
    if any(p.has_handles for p in curve.points):
        node.set("points", "")
        for p in curve.points:
            item = {"time": f"{p.beat:g}", "value": f"{p.value:g}"}
            if p.out_dx is not None or p.out_dy is not None:
                item["out"] = f"{p.out_dx or 0:g},{p.out_dy or 0:g}"
            if p.in_dx is not None or p.in_dy is not None:
                item["in"] = f"{p.in_dx or 0:g},{p.in_dy or 0:g}"
            ET.SubElement(node, "point", item)


# ---------------------------------------------------------------------------
# 包络工具
# ---------------------------------------------------------------------------


def curve_envelope(curve: Curve, frames: int, sample_rate: int, bpm: float,
                   hops: int = 32) -> np.ndarray:
    """把曲线采样成逐样本包络（分段线性插值，够平滑且内存友好）。"""
    if frames <= 0:
        return np.zeros(0, dtype=np.float32)
    if curve.is_empty():
        return np.full(frames, curve.points[0].value if curve.points else 0.0, dtype=np.float32)
    sec_per_beat = 60.0 / max(1e-9, bpm)
    # 每 hops 个样本取一个点，再线性插值到逐样本
    step = max(1, hops)
    count = frames // step + 2
    positions = np.arange(count, dtype=np.float64) * step / sample_rate / sec_per_beat
    values = curve.sample(positions).astype(np.float32)
    xp = np.arange(count, dtype=np.float64) * step
    xs = np.arange(frames, dtype=np.float64)
    return np.interp(xs, xp, values).astype(np.float32)


def apply_gain_curve(buffer: np.ndarray, curve: Curve, sample_rate: int, bpm: float,
                     base_db: float = 0.0) -> np.ndarray:
    """按曲线逐样本调整增益（曲线值视为相对 dB，叠加在 base_db 上）。"""
    env = curve_envelope(curve, buffer.shape[1], sample_rate, bpm)
    return buffer * (10.0 ** ((base_db + env) / 20.0)).astype(np.float32)


def apply_pan_curve(buffer: np.ndarray, curve: Curve, sample_rate: int, bpm: float,
                    base_pan: float = 0.0) -> np.ndarray:
    """按曲线逐样本调整声像（曲线值叠加在 base_pan 上，夹到 ±1）。"""
    env = np.clip(curve_envelope(curve, buffer.shape[1], sample_rate, bpm) + base_pan, -1.0, 1.0)
    theta = (env + 1.0) * math.pi / 4.0
    return buffer * np.stack([np.cos(theta), np.sin(theta)]).astype(np.float32)
