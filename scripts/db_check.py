"""数据库连通性自检: 走 pydantic-settings 读 .env, 连 virtual_human, 数表, 查扩展."""

from psycopg import connect
from virtual_human.config import settings


def main() -> None:
    with connect(settings.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("select current_database(), current_user, version()")
        db, user, version = cur.fetchone()
        print(f"db={db} user={user}")
        print(f"version={version.split(',')[0]}")

        cur.execute("select count(*) from information_schema.tables where table_schema='public'")
        print(f"tables={cur.fetchone()[0]}")

        cur.execute("select extname from pg_extension where extname in ('vector','pg_trgm','uuid-ossp') order by extname")
        print(f"extensions={[r[0] for r in cur.fetchall()]}")


if __name__ == "__main__":
    main()
