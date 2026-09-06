"""Let the generation worker move a suggestion through its own generation lifecycle.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_RETRIEVAL_COLUMNS = "source_type, asset_id, relevance_score, provider_metadata"
_GENERATION_COLUMNS = f"{_RETRIEVAL_COLUMNS}, status"


def upgrade() -> None:
    """Widen the worker's column grant to the status generation alone owns.

    Retrieval never changed a suggestion's status, so the worker's column-level grant
    deliberately excluded it. Generation is different: `generation_requested`,
    `generating`, `failed`, and the return to `proposed` are all transitions only the
    worker can know about, and none of them is a member's decision. `decided_at` stays
    outside the grant, so a Job still cannot overwrite what a member decided.
    """
    op.execute("REVOKE UPDATE ON TABLE broll_suggestions FROM clipah_worker")
    op.execute(f"GRANT UPDATE ({_GENERATION_COLUMNS}) ON TABLE broll_suggestions TO clipah_worker")


def downgrade() -> None:
    """Return the worker to the retrieval-only column grant."""
    op.execute("REVOKE UPDATE ON TABLE broll_suggestions FROM clipah_worker")
    op.execute(f"GRANT UPDATE ({_RETRIEVAL_COLUMNS}) ON TABLE broll_suggestions TO clipah_worker")
