"""Add messages.author_id: the staff member who wrote a message during a live handoff.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14 01:22:37.251821
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('author_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key('fk_messages_author_id_users', 'users', ['author_id'], ['id'])


def downgrade():
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_constraint('fk_messages_author_id_users', type_='foreignkey')
        batch_op.drop_column('author_id')
