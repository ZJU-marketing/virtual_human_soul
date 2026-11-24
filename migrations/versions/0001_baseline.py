"""baseline: schema 已由 sql/schema.sql 建立, 此迁移仅作基线标记.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 建表走 sql/schema.sql (含 HNSW 索引/触发器/COMMENT, autogenerate 不覆盖这些),
    # 这里只标记基线; 后续 schema 变更用 autogenerate 生成增量迁移.
    pass


def downgrade() -> None:
    pass
