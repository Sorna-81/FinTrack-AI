"""Adopt the existing schema and create tables missing on fresh installs.

Revision ID: a8332ee9bd9b
Revises:
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8332ee9bd9b"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This first migration also adopts databases created by the pre-Alembic
    # application. Never replace or drop an existing user's tables here.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "users" not in existing:
        op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(120), nullable=False), sa.Column("email", sa.String(255), nullable=False), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_users_email", "users", ["email"], unique=True)
    if "categories" not in existing:
        op.create_table("categories", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(80), nullable=False), sa.Column("kind", sa.String(16), nullable=False), sa.Column("color", sa.String(16), nullable=False), sa.UniqueConstraint("user_id", "name"))
        op.create_index("ix_categories_user_id", "categories", ["user_id"])
    if "transactions" not in existing:
        op.create_table("transactions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("amount", sa.Float(), nullable=False), sa.Column("category", sa.String(80), nullable=False), sa.Column("description", sa.String(500), nullable=False), sa.Column("kind", sa.String(16), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("payment_method", sa.String(40), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        for column in ("user_id", "category", "kind", "date"):
            op.create_index(f"ix_transactions_{column}", "transactions", [column])
    if "budgets" not in existing:
        op.create_table("budgets", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("category", sa.String(80), nullable=False), sa.Column("month", sa.String(7), nullable=False), sa.Column("amount", sa.Float(), nullable=False))
        op.create_index("ix_budgets_user_id", "budgets", ["user_id"])
        op.create_index("ix_budgets_month", "budgets", ["month"])
    if "financial_insights" not in existing:
        op.create_table("financial_insights", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("message", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_financial_insights_user_id", "financial_insights", ["user_id"])
    if "savings_goals" not in existing:
        op.create_table("savings_goals", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("target_amount", sa.Float(), nullable=False), sa.Column("saved_amount", sa.Float(), nullable=False), sa.Column("target_date", sa.Date(), nullable=True))
        op.create_index("ix_savings_goals_user_id", "savings_goals", ["user_id"])
    if "auth_sessions" not in existing:
        op.create_table("auth_sessions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("refresh_token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True), sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    # Financial tables belong to the pre-Alembic application schema. Roll back
    # only the session feature introduced by this revision; never drop records.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "auth_sessions" in existing:
        op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
        op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
        op.drop_table("auth_sessions")
