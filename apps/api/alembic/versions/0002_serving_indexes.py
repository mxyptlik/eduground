"""Composite indexes for observed request-path query shapes.

Run once as a migration before scaling API replicas.
"""
from alembic import op

revision = "0002_serving_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_chat_messages_session_recent", "chat_messages",
                    ["chat_session_id", "created_at", "id"])
    op.create_index("ix_ingestion_jobs_source_recent", "ingestion_jobs",
                    ["source_id", "created_at", "id"])
    op.create_index("ix_notebook_memberships_user_notebook", "notebook_memberships",
                    ["user_id", "notebook_id"])
    op.create_index("ix_sources_notebook_status", "sources", ["notebook_id", "status"])
    op.create_index("ix_notes_notebook_converted_visibility", "notes",
                    ["notebook_id", "converted_source_id", "visibility"])


def downgrade() -> None:
    op.drop_index("ix_notes_notebook_converted_visibility", table_name="notes")
    op.drop_index("ix_sources_notebook_status", table_name="sources")
    op.drop_index("ix_notebook_memberships_user_notebook", table_name="notebook_memberships")
    op.drop_index("ix_ingestion_jobs_source_recent", table_name="ingestion_jobs")
    op.drop_index("ix_chat_messages_session_recent", table_name="chat_messages")
