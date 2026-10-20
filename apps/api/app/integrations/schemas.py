import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Stream = Literal["locations", "catalog", "orders", "team_members", "timecards"]


class LocationLinkOut(BaseModel):
    external_location_id: str
    external_name: str
    location_id: uuid.UUID | None


class StreamHealthOut(BaseModel):
    stream: Stream
    status: Literal["fresh", "stale", "failing", "pending"]
    data_as_of: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    backfill_target_from: date | None
    backfill_completed_through: date | None


class ConnectionOut(BaseModel):
    id: uuid.UUID
    provider: Literal["square"]
    mode: Literal["fake", "sandbox"]
    status: Literal["pending", "active", "needs_reauth", "error", "disconnected"]
    external_account_id: str | None
    created_at: datetime
    location_links: list[LocationLinkOut]
    streams: list[StreamHealthOut]


class SyncRunOut(BaseModel):
    id: uuid.UUID
    stream: str
    kind: Literal["incremental", "backfill", "webhook"]
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    fetched_count: int
    changed_count: int
    error_detail: str | None


class ConnectionDetailOut(ConnectionOut):
    recent_runs: list[SyncRunOut]


class ConnectionList(BaseModel):
    data: list[ConnectionOut]


class AuthorizeOut(BaseModel):
    authorize_url: str


class LinkItem(BaseModel):
    external_location_id: str
    location_id: uuid.UUID | None


class LinksPatch(BaseModel):
    links: list[LinkItem]


class SyncRequest(BaseModel):
    streams: list[Stream] | None = None


class WorkflowStarted(BaseModel):
    workflow_id: str


class BackfillRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")


class MappingIssueOut(BaseModel):
    id: uuid.UUID
    kind: Literal["unmapped_location", "unmapped_item", "unmapped_team_member", "unknown_channel"]
    external_id: str
    label: str
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None


class MappingIssueList(BaseModel):
    data: list[MappingIssueOut]


class ResolveRequest(BaseModel):
    ignore: bool
