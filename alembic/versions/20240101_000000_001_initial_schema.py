"""Initial schema with all 15 tables.

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === SHARED TABLES (owned by JS Backend) ===

    # Users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_external_id", "users", ["external_id"])

    # Projects table
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.UniqueConstraint("external_id"),
    )
    op.create_index("ix_projects_external_id", "projects", ["external_id"])
    op.create_index("ix_projects_customer_name", "projects", ["customer_name"])

    # Project versions table
    op.create_table(
        "project_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
    )
    op.create_index("ix_project_versions_project_id", "project_versions", ["project_id"])

    # === DATA TABLES ===

    # Nielsen spend table
    op.create_table(
        "nielsen_spend",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_name", sa.String(255), nullable=False),
        sa.Column("wirtschaftsgruppe", sa.String(255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("spend_eur", sa.Numeric(15, 2), nullable=False),
        sa.Column("source_file", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nielsen_spend_brand_name", "nielsen_spend", ["brand_name"])
    op.create_index("ix_nielsen_spend_wirtschaftsgruppe", "nielsen_spend", ["wirtschaftsgruppe"])
    op.create_index("ix_nielsen_spend_year", "nielsen_spend", ["year"])
    op.create_index("ix_nielsen_spend_channel", "nielsen_spend", ["channel"])
    op.create_index("ix_nielsen_brand_year_month", "nielsen_spend", ["brand_name", "year", "month"])

    # YouGov KPI table
    op.create_table(
        "yougov_kpi",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_label", sa.String(255), nullable=False),
        sa.Column("sector", sa.String(255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("adaware", sa.Numeric(5, 2), nullable=True),
        sa.Column("aided", sa.Numeric(5, 2), nullable=True),
        sa.Column("consider", sa.Numeric(5, 2), nullable=True),
        sa.Column("source_file", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_yougov_kpi_brand_label", "yougov_kpi", ["brand_label"])
    op.create_index("ix_yougov_kpi_sector", "yougov_kpi", ["sector"])
    op.create_index("ix_yougov_kpi_year", "yougov_kpi", ["year"])
    op.create_index("ix_yougov_brand_year_month", "yougov_kpi", ["brand_label", "year", "month"])

    # === MAPPING TABLES ===

    # Industry map table
    op.create_table(
        "industry_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("wirtschaftsgruppe", sa.String(255), nullable=False),
        sa.Column("sector_label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wirtschaftsgruppe"),
    )
    op.create_index("ix_industry_map_wirtschaftsgruppe", "industry_map", ["wirtschaftsgruppe"])
    op.create_index("ix_industry_map_sector_label", "industry_map", ["sector_label"])

    # Brand map table
    op.create_table(
        "brand_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nielsen_brand", sa.String(255), nullable=False),
        sa.Column("yougov_brand_label", sa.String(255), nullable=False),
        sa.Column("wirtschaftsgruppe", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brand_map_nielsen_brand", "brand_map", ["nielsen_brand"])
    op.create_index("ix_brand_map_yougov_brand_label", "brand_map", ["yougov_brand_label"])

    # === PROMPT MANAGEMENT TABLES ===

    # Expert knowledge table
    op.create_table(
        "expert_knowledge",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_content", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("change_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_expert_knowledge_version", "expert_knowledge", ["version"])
    op.create_index("ix_expert_knowledge_category", "expert_knowledge", ["category"])

    # Prompt guardrails table
    op.create_table(
        "prompt_guardrails",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("guardrail_type", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_rules", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("change_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_guardrails_version", "prompt_guardrails", ["version"])
    op.create_index("ix_prompt_guardrails_guardrail_type", "prompt_guardrails", ["guardrail_type"])

    # === RUN MANAGEMENT TABLES ===

    # Runs table
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_token", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("project_version_id", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(255), nullable=False),
        sa.Column("brand_kpi", sa.String(50), nullable=False),
        sa.Column("total_budget", sa.Numeric(15, 2), nullable=True),
        sa.Column("time_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_parameters", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("confirmed_competitors", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["project_version_id"], ["project_versions.id"]),
    )
    op.create_index("ix_runs_session_token", "runs", ["session_token"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_input_hash", "runs", ["input_hash"])

    # Prompt traces table
    op.create_table(
        "prompt_traces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
    )
    op.create_index("ix_prompt_traces_run_id", "prompt_traces", ["run_id"])

    # Allocation results table
    op.create_table(
        "allocation_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("allocations", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_allocation_results_run_id", "allocation_results", ["run_id"])

    # Chat history table
    op.create_table(
        "chat_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("message_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
    )
    op.create_index("ix_chat_history_run_id", "chat_history", ["run_id"])

    # === LOGGING TABLES ===

    # Usage logs table
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("prompt_trace_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("session_token", sa.String(255), nullable=True),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("request_type", sa.String(50), nullable=False, server_default="generation"),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["prompt_trace_id"], ["prompt_traces.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_usage_logs_run_id", "usage_logs", ["run_id"])
    op.create_index("ix_usage_logs_user_id", "usage_logs", ["user_id"])
    op.create_index("ix_usage_logs_session_token", "usage_logs", ["session_token"])
    op.create_index("ix_usage_logs_model", "usage_logs", ["model"])


def downgrade() -> None:
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table("usage_logs")
    op.drop_table("chat_history")
    op.drop_table("allocation_results")
    op.drop_table("prompt_traces")
    op.drop_table("runs")
    op.drop_table("prompt_guardrails")
    op.drop_table("expert_knowledge")
    op.drop_table("brand_map")
    op.drop_table("industry_map")
    op.drop_table("yougov_kpi")
    op.drop_table("nielsen_spend")
    op.drop_table("project_versions")
    op.drop_table("projects")
    op.drop_table("users")
