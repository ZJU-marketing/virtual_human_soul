"""life scaffold contents

Revision ID: 60bf52cc1ad2
Revises: f9f4e9958cb3
Create Date: 2026-11-19 18:05:51.371729

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '60bf52cc1ad2'
down_revision: Union[str, Sequence[str], None] = 'f9f4e9958cb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'life_scaffold_contents',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('persona_id', sa.String(length=36), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['life_scaffold_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['persona_id'], ['persona.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        comment='履历事件展开: agent 为每条 life_scaffold_event 生成的摘要与正文',
    )
    op.create_index('ix_lsc_event', 'life_scaffold_contents', ['event_id', 'seq'], unique=False)
    op.create_index('ix_lsc_persona', 'life_scaffold_contents', ['persona_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_lsc_persona', table_name='life_scaffold_contents')
    op.drop_index('ix_lsc_event', table_name='life_scaffold_contents')
    op.drop_table('life_scaffold_contents')
