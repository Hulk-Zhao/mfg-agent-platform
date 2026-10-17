"""业务域模块（对齐 Itcast 各域独立目录思路，内部按角色分层）。

本包承载 4 个新场景（S2/S3/S4/S8）的域节点实现，全部遵循 handler 契约：
    handler(task, ctx, sink) -> {"domain","status","payload","summary"}
payload 必带 ``provenance``；``status ∈ {ok, degraded}``。

装配由 ``core/node_registry.build_handlers`` 完成（薄装配层）：现有 10 个
handler 保持冻结，新能力一律走本包 + 新 YAML（回归红线）。
"""
