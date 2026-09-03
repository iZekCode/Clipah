"""Let a worker start the stage that follows the one it just finished.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant the worker role exactly what admitting one follow-on stage requires.

    Until now nothing joined the pipeline: no code created an INGEST or a TRANSCRIBE
    Job, so a Project stopped wherever its last stage finished. The actor that knows a
    stage succeeded is the worker that ran it, so the worker is the one that has to
    admit the next one.

    This widens the worker role, and the widening is deliberately narrow. The worker
    already inserts the Assets, Transcripts, and Clip Candidates of the same Project;
    it may now also create a Job for that Project. It already held the budget grant that
    a metered stage reserves. It still cannot create a Workspace, a Project, or an
    upload, and it gains no privilege over any table it does not already write.
    """
    op.execute("GRANT INSERT ON TABLE jobs TO clipah_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE workspace_quota_reservations TO clipah_worker")


def downgrade() -> None:
    """Return the worker role to advancing jobs it was handed and never creating one."""
    op.execute("REVOKE SELECT, INSERT ON TABLE workspace_quota_reservations FROM clipah_worker")
    op.execute("REVOKE INSERT ON TABLE jobs FROM clipah_worker")
