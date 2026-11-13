"""add password reset fields

Revision ID: d22c64df8d3a
Revises: 5097bbcea5b1
Create Date: 2026-03-04 17:10:42.959333

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd22c64df8d3a'
down_revision: Union[str, Sequence[str], None] = '5097bbcea5b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add password reset token and expiry columns to users table."""
    op.add_column('users', sa.Column('password_reset_token', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('password_reset_expires', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove password reset columns from users table."""
    op.drop_column('users', 'password_reset_expires')
    op.drop_column('users', 'password_reset_token')
