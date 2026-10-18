"""L1 miss 后的词面意图路由（DESIGN-2：RAG 检索 vs 约束 LLM 直答）。

**为什么需要这一层**：实测（2026-10-18）域外问题（天气/闲聊/写作）也走完整
``search_kb``（BGE-M3 + Milvus + Rerank）链路，白花 1-1.5s 检索延迟；
而 thinking-disabled 的裸 LLM 只要 ~1.0s。域内问题则必须保 RAG 全链路
（要出处），路由判定必须零延迟 → 词表是唯一 viable 方案（用 LLM 分类意图
单次 ~1.4s，比它要省的检索还贵，负优化）。

**分流规则**（:func:`route`，纯函数、零外部调用、sub-ms）：

- 域内词命中 → 走 L2 RAG（保出处通道）
- 其余（域外/兜底）→ 约束 LLM 直答（channel=direct，限长+防幻觉 prompt）

**词表维护纪律**（不对称代价决定"宁宽勿窄"）：

- 漏判（域内问题走了 direct）= 制度问题拿到无出处回答 = **质量事故**；
- 误判（域外问题走了 RAG）= 多花 1-1.5s 后 Milvus 返回空或低相关，
  最终 miss / 诚实说"未找到" = **只损失延迟**。
- 因此词表优先收录语料**能回答**的口语说法；语料没覆盖的词（工资/绩效/
  婚假等 48 行制度未提及）**不收**——收了下场也是 RAG→miss，不如让
  direct 诚实指引对口部门。
- 新增制度后同步补词；未覆盖查询从 ``/api/v2/kbqa/stats`` 的 direct
  分布回看补齐（与 FAQ 晋升同款运营思路）。

词表来源（2026-10-18，数据驱动）：``kbqa_policy`` 48 行人工标注 keywords
+ 标题高频词 + 正文口语说法核验（仅收正文有命中的）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .kbqa_faq_match import normalize

# ---- 域内词表（CJK，子串命中即判域内）----
# 按语料域分组仅为人读；匹配不分组。刻意**不收**的泛词：计划/版本/取消/
# 责任/响应/需求/通道/结算/释放（"减肥计划""游戏结算"类误判面太大，且
# 其域内伴生词排产/图纸/订单/保密/班车已能覆盖真实问法）。
_DOMAIN_WORDS: frozenset[str] = frozenset({
    # 考勤与假期
    "考勤", "工时", "上下班", "请假", "事假", "病假", "年假", "休假",
    "调休", "加班餐", "班车", "通勤", "食堂", "餐饮", "会议室", "预订",
    # 入离职 / 培训 / 职业健康
    "入职", "试用期", "社保", "离职", "交接", "转正", "培训", "学时",
    "必修", "体检", "职业健康", "劳保",
    # IT 支持
    "电脑", "打印机", "账号", "账号安全", "密码", "网络", "资产",
    # 财务
    "差旅", "住宿", "发票", "报销", "借支", "备用金", "核销", "报价",
    "折扣", "核价", "回款", "账期", "应收", "费用",
    # 销售与订单（《订单变更》在语料；"我的订单"是销售域真实问法）
    "订单", "升级",
    # 合同 / 印章 / 保密
    "合同", "模板", "公章", "用印", "保密", "涉密", "竞业",
    # 工艺与工程变更
    "工艺", "图纸", "受控", "工装", "夹具", "校验", "变更", "首件",
    "试制", "样品", "评审", "认证",
    # 生产与质量
    "检验", "不良", "停线", "客诉", "结案", "排产", "插单", "报工",
    "投产", "换线", "工单", "报修", "点检", "保养", "开机", "备件",
    "台账", "质量", "产线", "工序",
    # 库存与物流
    "库存", "领用", "收货", "上架", "隔离", "发料", "盘点", "差异",
    "退换货", "保修", "维修", "存放",
    # 车间（《劳动保护》等制造现场制度有命中；温湿度/保存/期限正文零命中，
    # 刻意不收——语料答不了的问题走 direct 诚实指引更快，见 docstring）
    "车间",
    # 采购与供应
    "请购", "供应商", "准入", "稽核", "来料",
    # 安环
    "危化品", "消防", "动火",
    # 通用制度词（问"制度/流程怎么查"本身即域内信号）
    "制度", "流程", "规定", "办法", "细则", "审批", "登记",
    # 正文核验过的口语说法（打卡/出差/转正/门禁/薪资在 48 行正文有命中；
    # 工资/绩效/婚假/产假正文零命中，刻意不收，见模块 docstring）
    "打卡", "出差", "门禁", "薪资",
})

# 拉丁/数字缩写：词边界匹配（防 "MES" 命中 "TIMES" 类），大小写不敏感
_LATIN_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"VPN|ECN|ECM|SIP|MRB|8D|IQC|OQC|COA|MES|SMED|TPM|MSDS|"
    r"EVT|DVT|PVT|FIFO|SLA|EAM|WMS|NPI|CPK|SPC|EHS"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# 长词优先，保证命中上报的词是更具体的那个（"账号安全" 先于 "账号"）
_DOMAIN_WORDS_BY_LEN: tuple[str, ...] = tuple(
    sorted(_DOMAIN_WORDS, key=len, reverse=True))

# 「有实质内容」判定：至少一个拉丁字母/数字/CJK 字。
# 空串与纯标点（含全角 。？！、《》等 normalize 不转换的 CJK 标点）同样处理
_CONTENT_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


@dataclass(frozen=True)
class RouteDecision:
    """路由判定结果。

    - ``domain``：True → 走 L2 RAG（保出处）；False → 约束 LLM 直答
    - ``matched``：触发域内判定的词（空串 = 退化输入保守放行，见 route）
    """

    domain: bool
    matched: str


def route(query: str) -> RouteDecision:
    """词面意图路由：域内词命中 → RAG；其余 → direct。零外部调用，sub-ms。

    空/纯标点等退化输入**保守放行到 RAG**（与迭代一行为一致）——direct
    prompt 对空输入没有可约束的内容，而 L2 对它有既定的 miss 话术。
    注意 CJK 标点（。？！《》等）不在 normalize 的全角转换区，须单独判。
    """
    norm = normalize(query)
    if not norm or not _CONTENT_RE.search(norm):
        return RouteDecision(domain=True, matched="")
    for word in _DOMAIN_WORDS_BY_LEN:
        if word in norm:
            return RouteDecision(domain=True, matched=word)
    m = _LATIN_RE.search(norm)
    if m:
        return RouteDecision(domain=True, matched=m.group(1))
    return RouteDecision(domain=False, matched="")
