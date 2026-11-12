"""音效脚本：把一条音轨的效果链存成可复用的 JSON。

脚本是**纯数据**，只引用注册表里的效果类型与参数，不含可执行代码：

.. code-block:: json

    {
      "name": "guitar-bus",
      "description": "节奏吉他：削一点低频、补高次谐波、压住动态",
      "effects": [
        {"type": "eq", "low": -3, "mid": 1.5, "high": 2},
        {"type": "drive", "amount": 0.25, "tone": 0.6},
        {"type": "compressor", "threshold": -20, "ratio": 3}
      ]
    }

用途有两个：一是给人写/大模型写的「混音配方」一个落盘格式，二是音频逆向时
把估计出的效果链整条写回工程（``effect apply --script``），不必逐个 ``effect add``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .effects import EffectError, EffectSpec, effect_types_text, normalize_effect_params, resolve_effect

MAX_EFFECTS = 12


class EffectScriptError(ValueError):
    """脚本格式或参数不合法。"""


@dataclass
class EffectScript:
    name: str = "chain"
    description: str = ""
    specs: list[EffectSpec] = field(default_factory=list)
    source: Path | None = None

    # ---- 解析 ---------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Any, source: Path | None = None) -> "EffectScript":
        if not isinstance(data, dict):
            raise EffectScriptError("脚本根节点必须是对象")
        unknown = set(data) - {"name", "description", "effects", "$schema"}
        if unknown:
            raise EffectScriptError(f"不认识的字段：{'、'.join(sorted(unknown))}")
        raw_effects = data.get("effects")
        if not isinstance(raw_effects, list) or not raw_effects:
            raise EffectScriptError("effects 必须是非空数组")
        if len(raw_effects) > MAX_EFFECTS:
            raise EffectScriptError(f"一条链最多 {MAX_EFFECTS} 个效果")
        specs: list[EffectSpec] = []
        for index, item in enumerate(raw_effects, 1):
            if not isinstance(item, dict):
                raise EffectScriptError(f"effects[{index}] 必须是对象")
            type_name = item.get("type") or item.get("效果")
            key = resolve_effect(type_name)
            if key is None:
                raise EffectScriptError(
                    f"effects[{index}] 未知音效 {type_name!r}；可用：{effect_types_text()}"
                )
            params = {k: v for k, v in item.items() if k not in ("type", "效果")}
            try:
                values, _ = normalize_effect_params(key, params, strict=True)
            except EffectError as exc:
                raise EffectScriptError(f"effects[{index}]（{key}）：{exc}") from exc
            specs.append(EffectSpec(key, values))
        return cls(
            name=str(data.get("name") or (source.stem if source else "chain")),
            description=str(data.get("description") or ""),
            specs=specs,
            source=source,
        )

    @classmethod
    def load(cls, path: Path | str) -> "EffectScript":
        file = Path(path).expanduser()
        if not file.is_file():
            raise EffectScriptError(f"音效脚本不存在：{file}")
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EffectScriptError(
                f"{file.name} 不是合法 JSON：第 {exc.lineno} 行 {exc.msg}"
            ) from exc
        except OSError as exc:
            raise EffectScriptError(f"{file} 读取失败：{exc}") from exc
        return cls.from_dict(data, source=file)

    @classmethod
    def from_specs(cls, specs: list[EffectSpec], name: str = "chain",
                   description: str = "") -> "EffectScript":
        return cls(name=name, description=description, specs=list(specs))

    # ---- 输出 ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name}
        if self.description:
            payload["description"] = self.description
        payload["effects"] = [
            {"type": spec.type, **spec.params} for spec in self.specs
        ]
        return payload

    def save(self, path: Path | str) -> Path:
        file = Path(path).expanduser()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return file

    def labels(self) -> list[str]:
        try:
            return [spec.label() for spec in self.specs]
        except EffectError:
            return [spec.type for spec in self.specs]

    def summary(self) -> str:
        if not self.specs:
            return "（空链）"
        return " → ".join(
            f"{label}({spec.type})" for label, spec in zip(self.labels(), self.specs)
        )
