"""工段 → 现场群（企微）映射：目录加载、解析与投递记账的契约测试。

守四件事：
1. **结构自检**：工段必须来自 ``site_board.STAGES``（写错工段名加载期失败）、群名必填、
   slug 必须是 ASCII（env 变量名靠它拼）；
2. **凭据在 env**：``IM_GROUP_KEY_<SLUG>`` / ``IM_GROUP_MEMBERS_<SLUG>`` 覆盖 YAML，
   未配置即 ``configured=False``（不猜群、不静默）；
3. **投递逐条记账**：命中群 → 经 sender/webhook 投递；未配置 → ``skipped`` + 理由；
   配了责任人但缺 IM 网关 → ``failed`` + 理由；非工段 target → ``skipped``；
4. **接线**：默认运行时的通知通道必须并联「现场群」通道，且客诉分派一次通知走三路。
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from mfg_agents.v2.services import im_groups, site_board
from mfg_agents.v2.services.im_groups import (
    IMGroupError,
    IMGroupRegistry,
    StageGroup,
)
from mfg_agents.v2.services.notification import (
    BoardNotificationChannel,
    IMGroupNotificationChannel,
    LogNotificationChannel,
    MultiChannel,
    NotificationService,
)

#: 仓库自带的映射目录（凭据由 env 注入，YAML 里留空）
_TERMS = (Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2"
          / "data" / "im_groups.yaml")


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, key: str, chat_name: str, card) -> None:
        self.calls.append((key, chat_name, card.title))


class _FakeGateway:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    def push(self, user_id: str, card) -> None:
        self.pushed.append((user_id, card.title))


class _BrokenSender:
    def __call__(self, key: str, chat_name: str, card) -> None:
        raise RuntimeError("webhook 500")


def _registry(**env: str) -> IMGroupRegistry:
    return IMGroupRegistry.from_yaml(_TERMS, env=env)


@pytest.fixture()
def board_db(tmp_path) -> str:
    """临时治理库（看板落库隔离；本文件不依赖 test_site_board 的同名夹具）。"""
    return str(tmp_path / "wf_im_groups.db")


# ---------------------------------------------------------------- 一、目录自检


def test_shipped_terms_file_covers_all_stages():
    """仓库自带的映射目录必须覆盖全部工段白名单（结构完整，凭据留空）。"""
    reg = _registry()
    assert set(reg.keys()) == set(site_board.STAGES)
    for stage in site_board.STAGES:
        group = reg.resolve(stage)
        assert group.name, f"{stage} 缺群名"
        assert group.slug.isalnum(), f"{stage} 的 slug 必须是 ASCII 字母数字"
        # 演示态：未注入凭据 → 未配置（如实记账，不假装能推）
        assert group.configured is False
        assert "未配置投递凭据" in group.gap_reason()


def test_rejects_unknown_stage_and_missing_fields(tmp_path):
    bad_stage = tmp_path / "bad_stage.yaml"
    bad_stage.write_text(
        "version: 1\ngroups:\n  SMT-2:\n    name: x\n    slug: smt\n",
        encoding="utf-8")
    with pytest.raises(IMGroupError, match="工段白名单"):
        IMGroupRegistry.from_yaml(bad_stage)

    no_name = tmp_path / "no_name.yaml"
    no_name.write_text("version: 1\ngroups:\n  SMT:\n    slug: smt\n",
                       encoding="utf-8")
    with pytest.raises(IMGroupError, match="缺少群名"):
        IMGroupRegistry.from_yaml(no_name)

    bad_slug = tmp_path / "bad_slug.yaml"
    bad_slug.write_text(
        "version: 1\ngroups:\n  SMT:\n    name: x\n    slug: 锡膏\n", encoding="utf-8")
    with pytest.raises(IMGroupError, match="slug"):
        IMGroupRegistry.from_yaml(bad_slug)

    empty = tmp_path / "empty.yaml"
    empty.write_text("version: 1\ngroups: {}\n", encoding="utf-8")
    with pytest.raises(IMGroupError, match="未声明任何工段群"):
        IMGroupRegistry.from_yaml(empty)


def test_missing_file_degrades_to_empty_registry(tmp_path):
    """目录文件缺失 = 无映射（演示态可接受），但不静默当作已配置。"""
    reg = IMGroupRegistry.from_yaml(tmp_path / "nope.yaml")
    assert reg.keys() == []
    assert reg.resolve("SMT") is None


# ---------------------------------------------------------------- 二、env 覆盖


def test_env_overrides_credentials_and_members():
    reg = _registry(IM_GROUP_KEY_SMT="hook-key-1",
                    IM_GROUP_MEMBERS_PACK="zhang.san, li.si；wang.wu")
    smt = reg.resolve("SMT")
    assert (smt.key, smt.members) == ("hook-key-1", ())
    assert smt.configured is True
    pack = reg.resolve("PACK")
    assert pack.key == ""
    assert pack.members == ("zhang.san", "li.si", "wang.wu")
    assert pack.configured is True
    assert reg.configured_stages() == ["SMT", "PACK"]
    assert _registry().resolve("SMT").configured is False


# ---------------------------------------------------------------- 三、投递记账


def test_channel_delivers_to_group_and_skips_unconfigured():
    """命中群 → 投递记账；未配置凭据 → skipped + 理由（不静默丢弃）。"""
    sender = _FakeSender()
    channel = IMGroupNotificationChannel(
        registry=_registry(IM_GROUP_KEY_SMT="hook-key-1"), sender=sender)

    record = channel.send("P1", ["SMT", "PACK", "质量总监"], "客诉处置", "请现场确认")

    assert record["via"] == "im_group"
    assert record["pushed"] == [{"stage": "SMT", "chat": "SMT 现场处置群",
                                 "via": "webhook"}]
    assert sender.calls == [("hook-key-1", "SMT 现场处置群", "客诉处置")]
    # PACK 未配凭据、质量总监不是工段 —— 两条都如实记账
    assert len(record["skipped"]) == 2
    assert any("PACK" in s and "未配置投递凭据" in s for s in record["skipped"])
    assert any("质量总监" in s and "未在 im_groups.yaml 登记" in s
               for s in record["skipped"])
    assert record["failed"] == []


def test_channel_reports_failure_without_raising():
    """群投递抛错 → 记 failed + 理由，绝不把异常抛回通知服务（链路不崩）。"""
    channel = IMGroupNotificationChannel(
        registry=_registry(IM_GROUP_KEY_SMT="hook-key-1"), sender=_BrokenSender())
    record = channel.send("P1", ["SMT"], "客诉处置", "请现场确认")
    assert record["pushed"] == []
    assert record["failed"] and "群消息投递失败" in record["failed"][0]


def test_members_without_gateway_are_reported_not_claimed():
    """配了责任人但缺 IM 网关 → failed + 说清原因（不假装已 @ 到人）。"""
    channel = IMGroupNotificationChannel(
        registry=_registry(IM_GROUP_MEMBERS_SMT="zhang.san"), gateway=None)
    record = channel.send("P1", ["SMT"], "客诉处置", "请现场确认")
    assert record["pushed"] == []
    assert "应用消息发送实现为预留" in record["failed"][0]

    gateway = _FakeGateway()
    delivered = IMGroupNotificationChannel(
        registry=_registry(IM_GROUP_MEMBERS_SMT="zhang.san"), gateway=gateway,
    ).send("P1", ["SMT"], "客诉处置", "请现场确认")
    assert gateway.pushed == [("zhang.san", "客诉处置")]
    assert delivered["failed"] == []
    assert delivered["pushed"][0]["via"] == "app"


# ---------------------------------------------------------------- 四、接线与端到端


def test_default_runtime_wires_im_group_channel():
    """装配守卫：默认运行时的通知通道必须并联「现场群」通道。"""
    from mfg_agents.v2.core.orchestrator import V2Runtime

    channel = V2Runtime.create_default().notifier.channel
    assert isinstance(channel, MultiChannel)
    assert any(isinstance(c, IMGroupNotificationChannel) for c in channel.channels)


def test_complaint_dispatch_notifies_group_channel(board_db, monkeypatch):
    """端到端：客诉分派一次投递走三路——日志留痕 + 看板落库 + 现场群记账。"""
    from mfg_agents.common.config import settings

    sender = _FakeSender()
    monkeypatch.setattr(settings, "IM_GROUPS_PATH", str(_TERMS))
    monkeypatch.setenv("IM_GROUP_KEY_SMT", "hook-key-e2e")
    im_groups.reset_im_groups()

    notifier = NotificationService(MultiChannel([
        LogNotificationChannel(),
        BoardNotificationChannel(db_path=board_db),
        IMGroupNotificationChannel(sender=sender),
    ]))
    result = site_board.dispatch_complaint(notifier, {
        "complaint_no": "CS-20261012-01", "batch_no": "BATCH-A-1008",
        "customer": "华为终端", "product_model": "A型号",
        "defect_desc": "贴片元件偏移",
    })
    assert result["ok"] is True and result["stage"] == "SMT"
    # 群触达：工段被解析到该工段的现场群并投递
    assert sender.calls and sender.calls[0][1] == "SMT 现场处置群"
    # 站内留痕仍在（两路互不影响）
    assert site_board.list_by_stage("SMT", db_path=board_db)[0]["status"] == "pushed"
    im_groups.reset_im_groups()


def test_stage_group_type_is_frozen_and_configured_flag():
    """StageGroup 是不可变值对象（凭据合并结果不被后续改动污染）。"""
    group = StageGroup(stage="SMT", name="SMT 现场处置群", slug="smt",
                       key="k", members=("u1",))
    assert group.configured is True and group.gap_reason() == ""
    with pytest.raises(FrozenInstanceError):
        group.key = "other"  # type: ignore[misc]
