"""诊断: ORM 元数据 vs DB 实际 的类型差异."""

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

from virtual_human.db import engine
from virtual_human.models import KbEdge, KbEmbedding, MemoryAccessLog

insp = inspect(engine)

print("== ORM declared types ==")
for tbl, col in [("kb_edges", "weight"), ("memory_access_log", "load_score"), ("kb_embeddings", "embedding")]:
    c = getattr(globals()[dict(kb_edges="KbEdge", memory_access_log="MemoryAccessLog", kb_embeddings="KbEmbedding")[tbl]].__table__.c, col)
    print(f"{tbl}.{col}: {c.type} | pg: {c.type.compile(dialect=postgresql.dialect())}")

print("\n== DB actual types ==")
with engine.connect() as conn:
    rows = conn.execute(text(
        "select table_name, column_name, data_type from information_schema.columns "
        "where table_schema='public' and ((table_name='kb_edges' and column_name='weight') "
        "or (table_name='memory_access_log' and column_name='load_score') "
        "or (table_name='kb_embeddings' and column_name='embedding'))"
    )).fetchall()
    for r in rows:
        print(r)

print("\n== kb_embeddings nullable ==")
print([ (c["name"], c["nullable"]) for c in insp.get_columns("kb_embeddings") ])
