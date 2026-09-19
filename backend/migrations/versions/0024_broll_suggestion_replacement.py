"""Let a new B-roll request replace undecided ideas, and let a plan start its search.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow the API to discard undecided suggestions when a new plan is requested.

    Asking for B-roll again is a member's decision that the earlier ideas are not wanted.
    Only the API, acting on that request, may remove them; the worker still only adds
    suggestions and attaches pictures, so a Job can never erase what a member decided.
    """
    op.execute("GRANT DELETE ON TABLE broll_suggestions TO clipah_api")
    # A finished plan admits the search for its pictures, which records what that search
    # was admitted to illustrate, exactly as the API does for a search a member asks for.
    op.execute("GRANT INSERT ON TABLE broll_plan_requests TO clipah_worker")


def downgrade() -> None:
    """Return the API to reading and deciding on suggestions, and the worker to reading."""
    op.execute("REVOKE INSERT ON TABLE broll_plan_requests FROM clipah_worker")
    op.execute("REVOKE DELETE ON TABLE broll_suggestions FROM clipah_api")
