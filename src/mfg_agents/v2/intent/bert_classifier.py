"""BERT 意图分类器（复用自 E:/tools/assistant 项目的训练成果）——**未接线骨架**。

资产来源（2026-10-17 复制）：
- ``models/intent_bert_best.pt``：assistant 项目训练的意图分类权重（391MB）
- 训练项目：E:/tools/assistant（checkpoints/intent_bert/best.pt）

为什么暂不接线（主理人裁定，2026-10-17）：
1. **标签集未对齐**：权重文件无 label_mapping 元数据，assistant 的意图
   标签集与本平台四策略（scenario/qa/chitchat/fallback）不同构——直接
   加载会得到无法映射的标签空间；
2. **现有链路已验证**：L1 意图分类（DeepSeek + few-shot）实测 conf 0.85~0.99，
   满足当前需求；BERT 的价值在"本地零成本快速分类"（离线/高频场景）；
3. **依赖成本**：需引入 transformers/torch 推理链（torch 未在依赖中）。

接线条件（满足任一即重启评估）：
- assistant 项目补齐 label_mapping 且标签可映射到本平台域组；
- 出现"离线环境意图分类"或"L1 云端调用成本不可接受"的真实需求。

届时实现：``BertIntentClassifier(IntentClassifier)``——加载 best.pt →
softmax → 标签映射到 DOMAIN_GROUPS → confidence 透传。接口与
``intent/classifier.py`` 的 ``IntentClassifier`` ABC 对齐。
"""
from __future__ import annotations

__all__ = ["BertIntentClassifier_PLACEHOLDER"]


class BertIntentClassifier_PLACEHOLDER:
    """占位：见模块文档的接线条件。禁止在本平台默认路径实例化。"""

    MODEL_PATH = "models/intent_bert_best.pt"
