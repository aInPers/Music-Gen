"""决策文件（decisions.json）：大模型 / 人工对自动分析结果的修正。

自动分析给出一版「能渲染」的工程，但音色归属、误检、分层颗粒度往往需要上下文
判断。决策文件是这两步之间的**可审阅契约**：

.. code-block:: json

    {
      "version": 1,
      "regions": [{"index": 2, "label": "chorus"}],
      "synth_patches": {
        "my_pad": {"name": "my_pad", "oscillators": [{"wave": "saw"}],
                   "amp": {"attack": 0.6, "release": 1.5}}
      },
      "tracks": [
        {"track": 7, "action": "retimbre", "instrument": "synth", "patch": "my_pad",
         "name": "副歌 Pad", "gain_db": -5, "pan": 0.2,
         "effects": [{"type": "eq", "low": 3}, {"type": "chorus"}],
         "reason": "音区 C3–C4、持续音占比 0.9，候选里 synth_pad 概率最高"}
      ]
    }

``apply_decisions`` 只改内存里的 :class:`TrackDraft`，写盘仍由 ``rebuild`` 负责，
所以决策出错不会留下半新半旧的工程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.effectscript import EffectScript, EffectScriptError
from ..core.patch import Patch, PatchError

MAX_PATCHES = 16


class DecisionError(ValueError):
    """决策文件格式或内容不合法。"""


@dataclass
class TrackDecision:
    target: str = ""            # 轨名或 id
    stem: str = ""
    layer: int = 0
    action: str = "retimbre"    # keep | retimbre | delete
    instrument: str | None = None
    patch: str | None = None
    name: str | None = None
    gain_db: float | None = None
    pan: float | None = None
    effects: list | None = None  # list[EffectSpec]
    reason: str = ""


@dataclass
class Decisions:
    regions: list[dict] = field(default_factory=list)
    patches: dict[str, Patch] = field(default_factory=dict)
    tracks: list[TrackDecision] = field(default_factory=list)
    source: Path | None = None

    # ---- 解析 ---------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Any, source: Path | None = None) -> "Decisions":
        if not isinstance(data, dict):
            raise DecisionError("决策文件根节点必须是对象")
        unknown = set(data) - {"version", "regions", "synth_patches", "tracks",
                               "description", "$schema", "notes"}
        if unknown:
            raise DecisionError(f"不认识的字段：{'、'.join(sorted(unknown))}")
        version = int(data.get("version") or 1)
        if version != 1:
            raise DecisionError(f"不支持的决策版本 {version}（当前只认 1）")

        patches: dict[str, Patch] = {}
        raw_patches = data.get("synth_patches") or {}
        if not isinstance(raw_patches, dict):
            raise DecisionError("synth_patches 必须是对象：音色名 → 脚本内容")
        if len(raw_patches) > MAX_PATCHES:
            raise DecisionError(f"一次最多定义 {MAX_PATCHES} 个合成音脚本")
        for name, payload in raw_patches.items():
            try:
                patches[str(name)] = Patch.from_dict(payload)
            except PatchError as exc:
                raise DecisionError(f"synth_patches.{name}：{exc}") from exc

        regions = data.get("regions") or []
        if not isinstance(regions, list):
            raise DecisionError("regions 必须是数组")
        parsed_regions = []
        for item in regions:
            if not isinstance(item, dict) or "index" not in item:
                raise DecisionError("regions 的每一项都需要 index")
            label = str(item.get("label") or "").strip()
            if not label:
                raise DecisionError(f"regions[{item['index']}] 缺少 label")
            parsed_regions.append({"index": int(item["index"]), "label": label})

        raw_tracks = data.get("tracks") or []
        if not isinstance(raw_tracks, list):
            raise DecisionError("tracks 必须是数组")
        tracks: list[TrackDecision] = []
        for index, item in enumerate(raw_tracks, 1):
            if not isinstance(item, dict):
                raise DecisionError(f"tracks[{index}] 必须是对象")
            action = str(item.get("action") or "retimbre").strip().lower()
            if action not in ("keep", "retimbre", "delete"):
                raise DecisionError(
                    f"tracks[{index}].action 只能是 keep / retimbre / delete，收到 {action!r}"
                )
            effects = None
            if item.get("effects") is not None:
                try:
                    script = EffectScript.from_dict({"effects": item["effects"]})
                except EffectScriptError as exc:
                    raise DecisionError(f"tracks[{index}] 的效果链：{exc}") from exc
                effects = script.specs
            tracks.append(TrackDecision(
                target=str(item.get("track") or "").strip(),
                stem=str(item.get("stem") or "").strip(),
                layer=int(item.get("layer") or 0),
                action=action,
                instrument=(str(item["instrument"]).strip()
                            if item.get("instrument") else None),
                patch=(str(item["patch"]).strip() if item.get("patch") else None),
                name=(str(item["name"]).strip() if item.get("name") else None),
                gain_db=(float(item["gain_db"]) if item.get("gain_db") is not None else None),
                pan=(float(item["pan"]) if item.get("pan") is not None else None),
                effects=effects,
                reason=str(item.get("reason") or ""),
            ))
        return cls(regions=parsed_regions, patches=patches, tracks=tracks, source=source)

    @classmethod
    def load(cls, path: Path | str) -> "Decisions":
        file = Path(path).expanduser()
        if not file.is_file():
            raise DecisionError(f"决策文件不存在：{file}")
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DecisionError(
                f"{file.name} 不是合法 JSON：第 {exc.lineno} 行 {exc.msg}"
            ) from exc
        return cls.from_dict(data, source=file)


def apply_decisions(
    drafts: list,
    decisions: Decisions,
    *,
    output_dir: Path,
    regions: list | None = None,
) -> tuple[list, list[str]]:
    """把决策应用到音轨草稿，返回 ``(新草稿列表, 说明)`。

    * ``retimbre``：换乐器 / 换合成音脚本 / 改增益声像 / 替换效果链；
    * ``keep``：不换音色，但仍可改名字、增益、声像、效果；
    * ``delete``：整条丢掉（误检最常见的处理方式）。

    提供了 ``patch`` 时，脚本会落盘到 ``<工程目录>/synths/``，音轨写
    ``instrument="synth" patch="synths/xxx.synth.json"``，工程自包含、可搬走。
    """
    messages: list[str] = []
    written: dict[str, Path] = {}
    if not decisions.tracks and not decisions.regions:
        return drafts, ["决策文件是空的，工程保持自动分析结果"]

    if regions:
        labels = {item["index"]: item["label"] for item in decisions.regions}
        applied: dict[int, str] = {}
        for region in regions:
            if region.index in labels:
                region.label = labels[region.index]
                applied[region.index] = labels[region.index]
        if applied:
            messages.append(f"已标注 {len(applied)} 个段落："
                            + "、".join(f"{k}→{v}" for k, v in sorted(applied.items())))
        missing = sorted(set(labels) - set(applied))
        if missing:
            messages.append(f"段落 {missing} 不存在（共 {len(regions)} 段），标签已忽略")

    kept: list = []
    for order, draft in enumerate(drafts, 1):
        decision = _match(decisions, draft, order)
        if decision is None:
            kept.append(draft)
            continue
        if decision.action == "delete":
            messages.append(f"删除「{draft.name}」（{len(draft.notes)} 音符）"
                            + (f"：{decision.reason}" if decision.reason else ""))
            continue

        if decision.instrument:
            draft.instrument = decision.instrument
        if decision.patch:
            patch = _resolve_patch(decisions, decision.patch)
            target = written.get(patch.name) or _write_patch(patch, output_dir)
            written[patch.name] = target
            draft.patch_path = target
            draft.instrument = "synth"
            messages.append(f"「{draft.name}」改用合成音脚本 {patch.name}"
                            f"（{target.name}）")
        elif decision.instrument and decision.instrument != "synth":
            draft.patch_path = None
        if decision.name:
            draft.name = decision.name
        if decision.gain_db is not None:
            draft.gain_db = round(float(decision.gain_db), 1)
            # 决策给了电平就不要再被自动响度校准覆盖
            draft.gain_locked = True
        if decision.pan is not None:
            draft.pan = max(-1.0, min(1.0, float(decision.pan)))
        if decision.effects is not None:
            draft.effects = list(decision.effects)
        if decision.reason:
            messages.append(f"「{draft.name}」：{decision.reason}")
        kept.append(draft)

    if len(kept) != len(drafts):
        messages.append(f"决策后剩 {len(kept)} 条音轨（原 {len(drafts)} 条）")
    return kept, messages


def _match(decisions: Decisions, draft, order: int) -> TrackDecision | None:
    # 优先用草稿的稳定编号；老工程没有 id 时退回当前顺序
    identity = getattr(draft, "id", 0) or order
    for decision in decisions.tracks:
        if decision.target:
            text = decision.target.strip()
            if text.isdigit() and int(text) == identity:
                return decision
            if text == draft.name:
                return decision
            continue
        if decision.stem and decision.stem == draft.stem:
            if decision.layer and decision.layer != (getattr(draft, "layer", 0) or 1):
                continue
            return decision
    return None


def _resolve_patch(decisions: Decisions, ref: str) -> Patch:
    if ref in decisions.patches:
        return decisions.patches[ref]
    path = Path(ref).expanduser()
    if not path.is_absolute() and decisions.source is not None:
        candidate = (decisions.source.parent / ref)
        if candidate.is_file():
            path = candidate
    if path.is_file():
        try:
            return Patch.load(path)
        except PatchError as exc:
            raise DecisionError(f"决策引用的脚本 {ref} 无法载入：{exc}") from exc
    # 内置音色名（warm_pad 等）
    from ..core.patch import builtin_patches

    builtin = builtin_patches()
    if ref in builtin:
        return builtin[ref]
    raise DecisionError(
        f"找不到合成音脚本 {ref!r}；可用内置：{'、'.join(sorted(builtin))}，"
        "或在 synth_patches 里定义"
    )


def _write_patch(patch: Patch, output_dir: Path) -> Path:
    """把决策里的脚本落盘到 ``<工程目录>/synths/``（同名覆盖，保证可重复）。"""
    folder = Path(output_dir) / "synths"
    folder.mkdir(parents=True, exist_ok=True)
    stem = "".join(ch for ch in patch.name if ch.isalnum() or ch in "-_") or "patch"
    return patch.save(folder / f"{stem}.synth.json")
