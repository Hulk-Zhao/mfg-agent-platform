"""协同问数「历史轮次收缩」前端契约守护（参考案例检索屏 case-foldbar 交互）。

为什么用静态契约：收缩功能横跨轮次划分 / 收缩渲染 / 状态重置 / 强制展开四块，
任何一块被顺手删掉都不会抛错，现场表现只是"该收缩的没收缩"或"把运行中的
链路 / 待办也折叠了"——后者会让用户漏看进行中任务与人工确认，属于演示事故。
静态断言能便宜地钉住这四块。
"""
from __future__ import annotations

from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"


def _screens() -> str:
    return (UI_DIR / "screens.js").read_text(encoding="utf-8")


def _css() -> str:
    return (UI_DIR / "app.css").read_text(encoding="utf-8")


def test_chat_round_fold_is_wired():
    """轮次收缩四件套：轮次划分、摘要条、收起按钮、收缩提示缺一不可。"""
    src = _screens()
    # 轮次划分：user 消息开轮，其后 bot 消息归轮
    assert "rounds.push({ key: m.id, q: m, items: [] })" in src, "缺少按 user 消息划分轮次"
    # 收缩态：一行摘要条（点击展开）+ 标签 + 摘要
    assert "chat-foldbar" in src, "缺少收缩摘要条渲染"
    assert "chat-foldbar-tag" in src and "chat-foldbar-main" in src, "摘要条缺少标签/摘要结构"
    # 展开态：「收起这一轮」按钮
    assert "收起这一轮" in src and "chat-fold-toggle" in src, "缺少收起按钮"
    # 收缩提示（与案例检索屏 case-fold-hint 同构文案）
    assert "轮历史回答已收缩" in src and "chat-fold-hint" in src, "缺少收缩提示条"


def test_chat_fold_state_reset_and_toggle():
    """askOpen 状态机：新提问重置为自动（只展开最新轮），点摘要条/收起按钮切换。"""
    src = _screens()
    # 两条发送路径（在线/演示回放）都要重置，否则新提问后历史仍停留在手点展开态
    assert src.count("chat.askOpen = null;") >= 2, "在线与演示两条发送路径都必须重置 askOpen"
    # 切换逻辑：与案例检索屏同构（点开的轮再点=全部收缩）
    assert 'chat.askOpen = (cur === k) ? "none" : k;' in src, "缺少切换状态机"
    assert "chatStore(ctx).askOpen = null;" in src, "清空对话必须重置 askOpen"


def test_chat_fold_force_open_guards():
    """运行中的链路与未处理人工确认必须强制展开——收缩只针对已完结历史。"""
    src = _screens()
    assert 'm.kind === "flow" && m.status === "running"' in src, "运行中链路缺少强制展开"
    assert 'm.kind === "hitl" && !processed[m.taskUid]' in src, "未处理人工确认缺少强制展开"
    # 强制展开必须先于 askOpen 判定（"none" 也不能折叠这两类）
    force = src.index('m.kind === "flow" && m.status === "running"')
    none_gate = src.index('chat.askOpen === "none"')
    assert force < none_gate, "强制展开判定必须先于 askOpen 收缩门"


def test_chat_fold_styles_exist():
    """app.css 必须有摘要条/收起按钮样式（含亮色主题适配）。"""
    css = _css()
    assert ".msg .chat-foldbar" in css, "缺少收缩摘要条样式"
    assert ".chat-fold-toggle" in css, "缺少收起按钮样式"
    assert ".chat-fold-hint" in css, "缺少收缩提示样式"
    assert 'html[data-theme="light"] .msg .chat-foldbar' in css, "缺少亮色主题适配"
