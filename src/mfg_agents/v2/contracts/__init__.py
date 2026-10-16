"""接入契约层：新系统按契约实现即可接入，零改框架代码。

- im_gateway.py  : IMGateway ABC + InboundMessage / ReplyCard 数据结构
- data_source.py  : DataSource ABC + SQLiteDataSource + DataSourceRegistry
- scenario.py     : ScenarioMeta + ScenarioRegistry（扫描 scenarios/ 目录注册 YAML 场景）
- adapters/       : 各 IM 平台适配器（wecom.py 为企业微信实现）
"""

from .im_gateway import IMGateway, InboundMessage, ReplyCard
from .data_source import DataSource, DataSourceRegistry, SQLiteDataSource
from .scenario import ScenarioLoadError, ScenarioMeta, ScenarioNotFound, ScenarioRegistry

__all__ = [
    "IMGateway",
    "InboundMessage",
    "ReplyCard",
    "DataSource",
    "DataSourceRegistry",
    "SQLiteDataSource",
    "ScenarioMeta",
    "ScenarioRegistry",
    "ScenarioLoadError",
    "ScenarioNotFound",
]
