"""工段 → 现场群（企业微信）映射目录：`v2/data/im_groups.yaml` 的加载与解析。

职责边界（与 ``data_scope.py`` 分工，别混）：

- ``data_scope.yaml`` 管「**能看什么**」（按岗位裁决 + 脱敏）；
- 本模块管「**推给谁**」：客诉归因到工段后，该工段的现场群是哪个、要 @ 谁。

关键纪律：

1. **工段必须来自** :data:`~services.site_board.STAGES`——加载期逐个校验，写错工段名
   即启动失败（推错群比不推更贵，与 ``dispatch_complaint`` 的归因纪律同源）；
2. **结构在 YAML、凭据在 env**：群机器人 webhook key 与责任人 userid 由
   ``IM_GROUP_KEY_<SLUG>`` / ``IM_GROUP_MEMBERS_<SLUG>`` 注入，YAML 里不落真实值；
3. **未配置即如实标注**：既无 key 又无 members 的工段解析为 ``configured=False``
   ——投递侧据此记账为 ``skipped`` 并给出理由，**不静默丢弃、也不猜一个群**；
4. 演示/测试可注入路径（``IM_GROUPS_PATH`` 或 ``get_im_groups(path=…)``），
   并提供 ``reset_im_groups()`` 清缓存。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

__all__ = [
    "IMGroupError",
    "StageGroup",
    "IMGroupRegistry",
    "DEFAULT_IM_GROUPS_PATH",
    "get_im_groups",
    "reset_im_groups",
]

DEFAULT_IM_GROUPS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "im_groups.yaml")

#: env 变量名模板（大写 slug）：群机器人 key / 责任人 userid 列表
_KEY_ENV = "IM_GROUP_KEY_{slug}"
_MEMBERS_ENV = "IM_GROUP_MEMBERS_{slug}"


class IMGroupError(RuntimeError):
    """映射目录配置错误（加载期 fail-fast，不静默降级）。"""


@dataclass(frozen=True)
class StageGroup:
    """一个工段的现场群解析结果（凭据已从 env 合并进来）。"""

    stage: str
    name: str
    slug: str
    #: 群机器人 webhook key（env 注入；空 = 未配置 → 不推群）
    key: str = ""
    #: 要 @ 的责任人 userid（env 注入，可覆盖 YAML 的示例值）
    members: tuple[str, ...] = field(default=())

    @property
    def configured(self) -> bool:
        """是否具备投递条件：有群 key 或有责任人（两者都空 = 未配置，如实记账）。"""
        return bool(self.key or self.members)

    def gap_reason(self) -> str:
        """未配置的原因文案（投递侧原样记账，便于排查"为什么没推出去"）。"""
        if self.configured:
            return ""
        return (f"{self.stage} 现场群未配置投递凭据"
                f"（需 {_KEY_ENV.format(slug=self.slug.upper())} 或 "
                f"{_MEMBERS_ENV.format(slug=self.slug.upper())}），"
                "当前仅落站内看板与日志")


class IMGroupRegistry:
    """工段 → 现场群映射目录（构造期自检）。"""

    def __init__(self, groups: dict[str, StageGroup]) -> None:
        self._groups = dict(groups)

    # ---------- 加载与自检 ----------

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_IM_GROUPS_PATH,
                  env: Optional[dict[str, str]] = None) -> "IMGroupRegistry":
        """加载 YAML 并合并 env 覆盖；工段白名单校验失败即抛 ``IMGroupError``。"""
        from .site_board import STAGES                     # 唯一工段事实源
        from ..core.dimensions import MFG_STAGES

        raw_path = Path(path)
        if not raw_path.exists():
            # 缺文件 = 无映射（演示态可接受），但要留痕：不静默当作"已配置"
            logger.warning("[im_groups] 映射目录不存在: %s（按未配置处理）", raw_path)
            return cls({})
        raw = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise IMGroupError("im_groups.yaml 顶层必须是映射")
        nodes = raw.get("groups") or {}
        if not isinstance(nodes, dict):
            raise IMGroupError("im_groups.yaml 的 groups 必须是映射")
        if not nodes:
            raise IMGroupError("im_groups.yaml 未声明任何工段群")

        environ = os.environ if env is None else env
        groups: dict[str, StageGroup] = {}
        for stage_key, node in nodes.items():
            stage = str(stage_key).strip()
            if stage not in STAGES:
                raise IMGroupError(
                    f"工段 {stage!r} 不在工段白名单内（合法值：{'、'.join(MFG_STAGES)}）"
                    "——推错群比不推更贵")
            if not isinstance(node, dict):
                raise IMGroupError(f"工段 {stage!r} 的配置必须是映射")
            name = str(node.get("name") or "").strip()
            if not name:
                raise IMGroupError(f"工段 {stage!r} 缺少群名 name（通知文案要写清推给谁）")
            slug = str(node.get("slug") or "").strip().lower()
            # 必须 ASCII 字母数字：env 变量名靠它拼（中文/全角字符在部分环境不可用）
            if not slug or not (slug.isascii() and slug.isalnum()):
                raise IMGroupError(
                    f"工段 {stage!r} 的 slug 必须是 ASCII 字母数字（用于 env 变量名），"
                    f"收到 {slug!r}")

            env_key = environ.get(_KEY_ENV.format(slug=slug.upper()), "").strip()
            env_members = _split_members(
                environ.get(_MEMBERS_ENV.format(slug=slug.upper()), ""))
            yaml_members = _split_members(node.get("members"))
            members = tuple(env_members or yaml_members)
            groups[stage] = StageGroup(stage=stage, name=name, slug=slug,
                                       key=env_key, members=members)

        logger.info("[im_groups] 映射目录就绪: %s（%d 个工段，已配置 %d）",
                    raw_path.name, len(groups),
                    sum(1 for g in groups.values() if g.configured))
        return cls(groups)

    # ---------- 查询 ----------

    def keys(self) -> list[str]:
        return list(self._groups)

    def get(self, stage: str) -> Optional[StageGroup]:
        return self._groups.get(str(stage or "").strip())

    def resolve(self, stage: str) -> Optional[StageGroup]:
        """解析工段的现场群；未登记的工段返回 ``None``（调用方如实记账）。"""
        return self.get(stage)

    def configured_stages(self) -> list[str]:
        return [s for s, g in self._groups.items() if g.configured]


def _split_members(value) -> list[str]:
    """责任人 userid：支持列表或逗号/分号分隔字符串（两种来源同口径）。"""
    if value in (None, "", [], ()):
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("；", ",").replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(p).strip() for p in value]
    else:
        return []
    return [p for p in parts if p]


# ---------------------------------------------------------------- 单例

_REGISTRY: Optional[IMGroupRegistry] = None
_REGISTRY_PATH: Optional[str] = None


def get_im_groups(path: Optional[str | Path] = None,
                  force_reload: bool = False) -> IMGroupRegistry:
    """映射目录单例（惰性加载；路径缺省 = ``IM_GROUPS_PATH`` 或仓库内默认文件）。"""
    global _REGISTRY, _REGISTRY_PATH
    from ...common.config import settings

    target = str(path or getattr(settings, "IM_GROUPS_PATH", "")
                 or DEFAULT_IM_GROUPS_PATH)
    if force_reload or _REGISTRY is None or _REGISTRY_PATH != target:
        _REGISTRY = IMGroupRegistry.from_yaml(target)
        _REGISTRY_PATH = target
    return _REGISTRY


def reset_im_groups() -> None:
    """清空单例缓存（测试隔离用）。"""
    global _REGISTRY, _REGISTRY_PATH
    _REGISTRY = None
    _REGISTRY_PATH = None
