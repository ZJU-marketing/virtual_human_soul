"""检查 DB 端 COMMENT 是否为正常 UTF-8 中文."""

from sqlalchemy import text

from virtual_human.db import engine

with engine.connect() as conn:
    for t in ["persona", "kb_edges"]:
        r = conn.execute(
            text("select obj_description(CAST(:t AS regclass))"),
            {"t": t},
        ).scalar()
        print(t, repr(r))
