"""pytest 全局配置。

V1 基线的 tests/test_*.py 五个文件是**演示脚本**（顶层直接执行、无 pytest
测试函数，且依赖 Ollama / 运行中的服务），在 pytest 收集期即报错。
处理方式（V1 文件零改动）：
- collect_ignore 跳过这些脚本的收集——它们仍可 `uv run python tests/test_e2e.py` 手动运行；
- 等价覆盖移至 tests/test_v1_regression.py（离线可跑的 pytest 回归：
  图可编译、路由规则、SQL 只读守卫、域 Agent 结论）。
"""
collect_ignore = [
    "test_e2e.py",
    "test_sql.py",
    "test_events.py",
    "test_stream.py",
    "test_chunk.py",
]
