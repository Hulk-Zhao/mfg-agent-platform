"""数据库连接与查询封装。
本地：SQLite（data/mfg_agent.db）；Docker/生产：通过 DB_URL 切MySQL。
"""
import pandas as pd
from sqlalchemy import create_engine, text
from ..config import settings

_engine = create_engine(settings.DB_URL, pool_pre_ping=True, future=True)


def run_sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    with _engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def run_sql_one(sql: str, params: dict | None = None) -> dict | None:
    df = run_sql(sql, params)
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def test_connection() -> bool:
    try:
        run_sql_one("SELECT 1")
        return True
    except Exception as e:
        print(f"[db] connection failed: {e}")
        return False
