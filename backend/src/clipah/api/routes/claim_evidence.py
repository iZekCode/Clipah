"""HTTP adapters for User-provided claim evidence.

Nothing here verifies anything. A citation is recorded as its author wrote it, shown as a
reviewable suggestion, and marked as supported or disputed only when a person says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from clipah.api.dependencies import (
    CurrentWorkspace,
    DatabaseSession,
    require_csrf,
    require_workspace,
)
from clipah.api.errors import ApiError
from clipah.models import ClaimVerificationStatus
from clipah.variants.claim_evidence import ClaimEvidenceInvalidError
from clipah.variants.repository import EvidenceSummary
from clipah.variants.use_cases import (
    VariantTargetNotFoundError,
    attach_claim_evidence,
    list_claim_evidence,
    set_verification_status,
)
from clipah.workspaces.models import WorkspaceAction

router = APIRouter(prefix="/api/v1", tags=["claim-evidence"])
ReadableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.PROJECT_READ))
]
WritableWorkspace = Annotated[
    CurrentWorkspace, Depends(require_workspace(WorkspaceAction.EDIT_WRITE))
]


class ClaimEvidenceBody(BaseModel):
    """One citation, exactly as a member wrote it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start_word_id: str = Field(alias="startWordId", min_length=1, max_length=32)
    end_word_id: str = Field(alias="endWordId", min_length=1, max_length=32)
    claim_text: str = Field(alias="claimText", min_length=1, max_length=1_000)
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=2_048)
    source_title: str = Field(alias="sourceTitle", min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=200)
    retrieved_at: datetime = Field(alias="retrievedAt")


class VerificationBody(BaseModel):
    """The one field only a User may change."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    verification_status: ClaimVerificationStatus = Field(alias="verificationStatus")


class ClaimEvidenceResponse(BaseModel):
    """One citation, as a reviewer is allowed to read it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    candidate_id: UUID = Field(alias="candidateId")
    start_word_id: str = Field(alias="startWordId")
    end_word_id: str = Field(alias="endWordId")
    claim_text: str = Field(alias="claimText")
    source_url: str = Field(alias="sourceUrl")
    source_title: str = Field(alias="sourceTitle")
    publisher: str
    retrieved_at: datetime = Field(alias="retrievedAt")
    verification_status: ClaimVerificationStatus = Field(alias="verificationStatus")
    created_by_user_id: UUID = Field(alias="createdByUserId")
    created_at: datetime = Field(alias="createdAt")

    @field_serializer("retrieved_at", "created_at")
    def serialize_timestamp(self, value: datetime) -> str:
        """Preserve the API's established explicit UTC-offset timestamp shape."""
        return value.isoformat()


class ClaimEvidenceListResponse(BaseModel):
    """Every citation attached to one candidate."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    evidence: tuple[ClaimEvidenceResponse, ...]


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/claim-evidence",
    status_code=201,
    response_model=ClaimEvidenceResponse,
    dependencies=[Depends(require_csrf)],
)
def create_evidence(
    project_id: UUID,
    candidate_id: UUID,
    body: ClaimEvidenceBody,
    session: DatabaseSession,
    workspace: WritableWorkspace,
) -> ClaimEvidenceResponse:
    """Record where a member says one claim came from, bound to exact words."""
    try:
        evidence = attach_claim_evidence(
            session,
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
            start_word_id=body.start_word_id,
            end_word_id=body.end_word_id,
            claim_text=body.claim_text,
            source_url=body.source_url,
            source_title=body.source_title,
            publisher=body.publisher,
            retrieved_at=body.retrieved_at,
        )
    except VariantTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    except ClaimEvidenceInvalidError as error:
        raise ApiError(status_code=422, code="EVIDENCE_INVALID") from error

    session.commit()
    return _body(evidence)


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}/claim-evidence",
    response_model=ClaimEvidenceListResponse,
)
def list_collection(
    project_id: UUID,
    candidate_id: UUID,
    session: DatabaseSession,
    workspace: ReadableWorkspace,
) -> ClaimEvidenceListResponse:
    """List every citation attached to one candidate."""
    try:
        evidence = list_claim_evidence(
            session,
            access=workspace.access,
            project_id=project_id,
            candidate_id=candidate_id,
        )
    except VariantTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error
    return ClaimEvidenceListResponse(evidence=tuple(_body(item) for item in evidence))


@router.patch(
    "/claim-evidence/{evidence_id}",
    response_model=ClaimEvidenceResponse,
    dependencies=[Depends(require_csrf)],
)
def update_verification(
    evidence_id: UUID,
    body: VerificationBody,
    session: DatabaseSession,
    workspace: WritableWorkspace,
) -> ClaimEvidenceResponse:
    """Record that a User decided what this evidence establishes."""
    try:
        evidence = set_verification_status(
            session,
            access=workspace.access,
            evidence_id=evidence_id,
            status=body.verification_status,
        )
    except VariantTargetNotFoundError as error:
        raise ApiError(status_code=404, code="NOT_FOUND") from error

    session.commit()
    return _body(evidence)


def _body(evidence: EvidenceSummary) -> ClaimEvidenceResponse:
    """Render one citation exactly as it was written."""
    return ClaimEvidenceResponse(
        id=evidence.evidence_id,
        candidateId=evidence.candidate_id,
        startWordId=evidence.start_word_id,
        endWordId=evidence.end_word_id,
        claimText=evidence.claim_text,
        sourceUrl=evidence.source_url,
        sourceTitle=evidence.source_title,
        publisher=evidence.publisher,
        retrievedAt=evidence.retrieved_at,
        verificationStatus=evidence.verification_status,
        createdByUserId=evidence.created_by_user_id,
        createdAt=evidence.created_at,
    )
