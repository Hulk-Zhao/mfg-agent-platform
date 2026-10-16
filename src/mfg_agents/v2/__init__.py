"""V2 可接入框架（mfg_agent_platform.v2）。

定位（见 docs/ARCHITECTURE-V2.md）：
把 V1 原型中"硬编码的场景知识"抽取为声明式配置（YAML 场景 DAG），
把"单体状态"拆为 SharedContext + 分域状态，并新增接入契约层
（IM 网关 / 数据源 / 场景注册表），使新系统"按契约实现即可接入，零改框架代码"。

包结构：
- contracts/  接入契约层：IMGateway、DataSource、ScenarioRegistry 及企业微信适配器
- core/       编排内核：SharedContext、DomainState、YamlDagExecutor、TwoLevelRouter
- services/   能力服务：Text2SQLService（八道防线）、NotificationService、WorkflowEngine
- scenarios/  声明式场景定义（V1 五场景等价迁移，先落 line_stop_v2）
- demo/       CLI / FastAPI / IM 模拟器三件套（offline 可跑，degraded 模式）

设计纪律：
1. V1 代码零破坏——本包不修改任何 V1 既有文件行为，仅复用其 Agent/SQL 守卫等资产；
2. offline 可跑——无 Ollama、无外网时全链路 degraded 降级（见 core/router.py、services/text2sql.py）；
3. 可审计——所有数据结论必须带 provenance（SQL 原文 + 命中行 + 耗时）；
4. HITL——复线决策、对客 ETA 等关键决策走 waiting_hitl，绝不自动放行。
"""

__version__ = "2.0.0"
