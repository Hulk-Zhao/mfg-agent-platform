"""场景注册表契约（docs/ARCHITECTURE-V2.md D2：场景组合爆炸 → 声明式化）。

场景 = 一个 YAML 文件（见 scenarios/line_stop_v2.yaml）：
新场景零代码新增，只需新增 YAML + 复用现有 Agent 节点。

加载纪律（B.3 第 5 条）：YAML 解析失败必须 fail-fast（启动即报错），
不允许静默跳过——声明式配置的错误必须在启动时暴露，而不是运行时。

HITL 槽位白名单（治理层）：每个 ``hitl`` 节点的槽位必须落在
``v2/data/permissions.yaml`` 声明的 7 个 slot 内；否则**加载期**即抛
``ScenarioLoadError``（含场景文件名 + 节点 id + 非法槽位 + 合法清单），
绝不留到运行时才 ``KeyError``。这与权限注册表构成"双注册表启动校验"。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

logger = logging.getLogger(__name__)

_HITL_PREFIX = "hitl_"


class ScenarioLoadError(Exception):
    """场景 YAML 加载/校验失败（fail-fast）。"""


class ScenarioNotFound(Exception):
    """场景 key 未注册。"""


@dataclass
class ScenarioMeta:
    """场景元数据（YAML 声明式定义的结构化视图）。"""

    key: str                                  # 场景唯一键（如 line_stop_v2）
    name: str                                 # 场景中文名
    version: int = 1                          # 声明版本（语义变更必须升版）
    domains: list[str] = field(default_factory=list)   # 参与域组
    triggers: list[dict[str, Any]] = field(default_factory=list)  # 触发方式
    entry: str = ""                           # 入口节点（DAG 第一个 step id）
    steps: list[dict[str, Any]] = field(default_factory=list)     # DAG 步骤
    hitl: list[dict[str, Any]] = field(default_factory=list)      # HITL 点
    outputs: list[str] = field(default_factory=list)              # 输出物
    source_file: str = ""                     # 来源文件（审计）
    raw: dict[str, Any] = field(default_factory=dict)            # 原始 YAML

    def step_ids(self) -> list[str]:
        """所有显式步骤 ID（含 fan_out 展开后的分支 ID）。"""
        ids: list[str] = []
        for step in self.steps:
            sid = step.get("id")
            if sid:
                ids.append(str(sid))
            if step.get("type") == "fan_out":
                ids.extend(str(b.get("id")) for b in step.get("branches", []) if b.get("id"))
        return ids

    def trigger_words(self) -> list[str]:
        """汇总所有 keyword 触发词（供路由 L0 快路径复用）。"""
        words: list[str] = []
        for trig in self.triggers:
            if str(trig.get("type", "")).lower() == "keyword":
                words.extend(str(w) for w in trig.get("words", []))
        return words


_REQUIRED_FIELDS = ("key", "name", "steps")


class ScenarioRegistry:
    """场景注册表：扫描目录注册 YAML 场景，提供 get / list。

    Args:
        scenarios_dir: 场景 YAML 目录。
        allowed_hitl_slots: HITL 槽位白名单（默认从 ``permissions.yaml`` 加载，
            测试可显式注入）。
    """

    def __init__(self, scenarios_dir: str | Path,
                 allowed_hitl_slots: Optional[Iterable[str]] = None) -> None:
        self.scenarios_dir = Path(scenarios_dir)
        self._scenarios: dict[str, ScenarioMeta] = {}
        self._allowed_slots: Optional[list[str]] = (
            [str(s) for s in allowed_hitl_slots] if allowed_hitl_slots is not None
            else None)

    # ---------- HITL 槽位白名单 ----------

    def allowed_hitl_slots(self) -> list[str]:
        """合法 HITL 槽位清单（惰性取自权限注册表，保证单一事实源）。"""
        if self._allowed_slots is None:
            try:
                from .permissions import get_permission_registry
                self._allowed_slots = sorted(get_permission_registry().slot_keys())
            except Exception as exc:  # noqa: BLE001
                raise ScenarioLoadError(
                    "HITL 槽位白名单加载失败（v2/data/permissions.yaml）: "
                    f"{exc}") from exc
        return list(self._allowed_slots)

    @staticmethod
    def _slot_of(node_id: str, agent: str = "") -> str:
        """节点 id / agent key → 槽位（约定 ``hitl_<slot>``）。"""
        for candidate in (agent, node_id):
            text = str(candidate or "").strip()
            if text.startswith(_HITL_PREFIX):
                return text[len(_HITL_PREFIX):]
        text = str(node_id or "").strip()
        return text

    def hitl_nodes(self, meta: ScenarioMeta) -> list[dict[str, str]]:
        """场景内全部 HITL 节点（type=hitl 的步骤 + ``hitl:`` 段声明）。"""
        nodes: list[dict[str, str]] = []
        seen: set[str] = set()
        for step in meta.steps:
            if str(step.get("type") or "").strip().lower() != "hitl":
                continue
            node_id = str(step.get("id"))
            agent = str(step.get("agent") or "")
            nodes.append({"id": node_id, "agent": agent,
                          "slot": self._slot_of(node_id, agent),
                          "declared_in": "steps"})
            seen.add(node_id)
        for item in meta.hitl:
            if isinstance(item, dict):
                node_id = str(item.get("step") or "")
            else:
                node_id = str(item)
            if not node_id or node_id in seen:
                continue
            nodes.append({"id": node_id, "agent": "",
                          "slot": self._slot_of(node_id),
                          "declared_in": "hitl"})
            seen.add(node_id)
        return nodes

    def _validate_hitl_slots(self, meta: ScenarioMeta, path: Path) -> None:
        """加载期校验：HITL 槽位必须在白名单内（fail-fast，不等运行时）。"""
        allowed = self.allowed_hitl_slots()
        if not allowed:
            raise ScenarioLoadError(
                f"HITL 槽位白名单为空（v2/data/permissions.yaml 未声明 slots），"
                f"拒绝加载场景: {path.name}")
        for node in self.hitl_nodes(meta):
            if node["slot"] in allowed:
                continue
            raise ScenarioLoadError(
                f"HITL 槽位非法：场景 {path.name} 节点 {node['id']!r}"
                f"（声明位置 {node['declared_in']}）的槽位为 {node['slot']!r}，"
                f"不在合法槽位清单 {allowed} 内"
                f"（合法槽位定义见 src/mfg_agents/v2/data/permissions.yaml 的 slots）")

    def load(self) -> dict[str, ScenarioMeta]:
        """扫描 scenarios_dir 下全部 *.yaml；任何文件解析/校验失败即抛错。"""
        if not self.scenarios_dir.is_dir():
            raise ScenarioLoadError(f"场景目录不存在: {self.scenarios_dir}")
        loaded: dict[str, ScenarioMeta] = {}
        for path in sorted(self.scenarios_dir.glob("*.yaml")):
            meta = self._load_file(path)
            if meta.key in loaded:
                raise ScenarioLoadError(
                    f"场景 key 冲突: {meta.key!r}（{path.name} 与已加载文件重复）")
            loaded[meta.key] = meta
            logger.info("注册场景: %s v%s（%s，%d 步）",
                        meta.key, meta.version, meta.name, len(meta.steps))
        if not loaded:
            raise ScenarioLoadError(f"场景目录为空: {self.scenarios_dir}")
        self._scenarios = loaded
        return loaded

    def _load_file(self, path: Path) -> ScenarioMeta:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ScenarioLoadError(f"场景 YAML 解析失败: {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise ScenarioLoadError(f"场景 YAML 顶层必须是映射: {path.name}")
        missing = [f for f in _REQUIRED_FIELDS if not data.get(f)]
        if missing:
            raise ScenarioLoadError(f"场景缺少必填字段 {missing}: {path.name}")
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ScenarioLoadError(f"场景 steps 必须为非空列表: {path.name}")
        for step in steps:
            if not isinstance(step, dict) or not step.get("id"):
                raise ScenarioLoadError(f"场景 step 缺少 id: {path.name} -> {step!r}")
        meta = ScenarioMeta(
            key=str(data["key"]),
            name=str(data["name"]),
            version=int(data.get("version", 1)),
            domains=[str(d) for d in data.get("domains", [])],
            triggers=list(data.get("triggers", [])),
            entry=str(data.get("entry") or steps[0]["id"]),
            steps=[dict(s) for s in steps],
            hitl=list(data.get("hitl", [])),
            outputs=[str(o) for o in data.get("outputs", [])],
            source_file=str(path),
            raw=data,
        )
        self._validate_hitl_slots(meta, path)
        return meta

    def get(self, key: str) -> ScenarioMeta:
        if key not in self._scenarios:
            raise ScenarioNotFound(
                f"场景未注册: {key!r}（已注册: {sorted(self._scenarios)}）")
        return self._scenarios[key]

    def list(self) -> list[ScenarioMeta]:
        return [self._scenarios[k] for k in sorted(self._scenarios)]

    def match_trigger_words(self, query: str) -> Optional[str]:
        """按 keyword 触发词匹配场景（L0 快路径用；未命中返回 None）。"""
        for meta in self.list():
            if any(word in query for word in meta.trigger_words()):
                return meta.key
        return None
