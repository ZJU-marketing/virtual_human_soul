# 本地数据库

跑在本机 podman 里的 PostgreSQL 16 + pgvector，容器名 `consumer_goods_pg`。

| 项 | 值 |
| --- | --- |
| host | 127.0.0.1 |
| port | 55432 |
| user | consumer |
| password | consumer_dev_2026 |
| database | virtual_human |

配置统一放在项目根目录 `.env`（模板见 `.env.example`，真实 `.env` 已被 `.gitignore` 忽略）。

## 常用命令

```bash
# 看容器状态
podman ps

# 进 psql
podman exec -it consumer_goods_pg psql -U consumer -d virtual_human

# 列表
podman exec consumer_goods_pg psql -U consumer -d virtual_human -c "\dt"
```

已装扩展：`vector`、`pg_trgm`、`uuid-ossp`。原库 `consumer_goods` 保持不动。

`psql` 在本机没装，所以统一通过 `podman exec` 走容器内的客户端；应用侧则直接用 `127.0.0.1:55432` 连接。
