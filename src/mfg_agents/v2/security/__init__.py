"""安全内核包：ACL 编译器 / 二次授权评估器 / filter 翻译器 / 案例 RBAC。

**唯一 FilterAST 生产点**是 ``acl_compiler.AclCompiler``（裁定 A10）：
它的签名里没有 ``query_text``，也没有任何 LLM 输出位——从函数签名层面物理阻断
"让 LLM 参与生成权限过滤条件"这条最常见的越权路径。

本包只依赖标准库 + 既有依赖（pyyaml），零新增第三方依赖。
"""

from .filter_translator import (
    canonical_filter_json,
    filter_hash,
    to_chroma_where,
    to_milvus_expr,
    to_predicate,
)

__all__ = [
    "canonical_filter_json",
    "filter_hash",
    "to_chroma_where",
    "to_milvus_expr",
    "to_predicate",
]
