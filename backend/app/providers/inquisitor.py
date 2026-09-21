"""Client for the Inquisitor public-records system.

Inquisitor, a sibling project, runs the CPRA side of this operation: it sends
California Public Records Act requests to county agricultural commissioners,
tracks statutory deadlines, chases non-responses,
and files whatever the agencies produce into an evidence vault with immutable
originals and SHA-256 hashes.

The tracker is a *consumer* of that vault.  Once a month it asks Inquisitor to
refresh the standing CPRA campaign, then pulls any newly produced files and
feeds them through the ordinary import pipeline.  That gives the tracker a
self-refreshing supply of PUR records, and gives every published fact a
provenance chain that runs all the way back to the records request that
obtained it:

    published fact -> PUR record -> source file -> production -> CPRA request -> agency

Inquisitor's workstation API is deliberately private: it authenticates by
trusted-proxy header and is not reachable from the public internet.  This
client therefore talks to it over the internal network only, and every method
degrades to a clear error rather than a partial sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

#: Header Inquisitor's trusted-proxy authenticator reads the actor from.
#: Matches ``Settings.authenticated_user_header`` in the Inquisitor codebase.
DEFAULT_ACTOR_HEADER = "X-Inquisitor-Authenticated-User"

#: File types worth pulling into the tracker.  Agencies produce plenty that is
#: not a use report -- cover letters, fee notices, exemption logs -- and the
#: import pipeline's format detection will reject those anyway, so filtering
#: here just saves the download.
PUR_FILE_TYPES = ("pdf", "csv", "tsv", "txt", "xlsx", "xls", "xlsm", "docx")


class InquisitorError(RuntimeError):
    """Raised when Inquisitor is unreachable or rejects a request."""


@dataclass(frozen=True)
class VaultFile:
    """One immutable source file in Inquisitor's evidence vault."""

    id: str
    filename: str
    sha256: str | None
    byte_size: int | None
    mime_type: str | None
    file_type: str | None
    received_at: datetime | None
    processing_outcome: str | None
    agency: str | None = None
    agency_id: str | None = None
    request_number: str | None = None
    request_id: str | None = None
    campaign: str | None = None
    campaign_id: str | None = None
    production: str | None = None
    production_id: str | None = None
    relative_path: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> VaultFile:
        received = row.get("received_at")
        parsed: datetime | None = None
        if received:
            try:
                parsed = datetime.fromisoformat(str(received).replace("Z", "+00:00"))
            except ValueError:
                parsed = None
        return cls(
            id=str(row.get("id")),
            filename=str(row.get("filename") or ""),
            sha256=row.get("sha256"),
            byte_size=row.get("byte_size"),
            mime_type=row.get("mime_type"),
            file_type=row.get("file_type"),
            received_at=parsed,
            processing_outcome=row.get("processing_outcome"),
            agency=row.get("agency"),
            agency_id=row.get("agency_id"),
            request_number=row.get("request_number"),
            request_id=row.get("request_id"),
            campaign=row.get("campaign"),
            campaign_id=row.get("campaign_id"),
            production=row.get("production"),
            production_id=row.get("production_id"),
            relative_path=row.get("relative_path"),
            raw=row,
        )

    @property
    def citation(self) -> str:
        """How this file is cited in the public Sources section."""
        parts = []
        if self.agency:
            parts.append(self.agency)
        if self.request_number:
            parts.append(f"CPRA request {self.request_number}")
        if self.production:
            parts.append(f"production {self.production}")
        parts.append(self.filename)
        return " — ".join(parts)

    def looks_importable(self) -> bool:
        suffix = (self.file_type or "").lower().lstrip(".")
        if suffix in PUR_FILE_TYPES:
            return True
        name = self.filename.lower()
        return any(name.endswith(f".{ext}") for ext in PUR_FILE_TYPES)


@dataclass(frozen=True)
class Campaign:
    id: str
    number: str | None
    title: str | None
    state: str | None
    intent: str | None = None
    proposed_targets: list[dict[str, Any]] = field(default_factory=list)


class InquisitorClient:
    """Thin, explicit client over Inquisitor's workstation API."""

    def __init__(
        self,
        base_url: str,
        *,
        workspace_id: str,
        actor: str,
        actor_header: str = DEFAULT_ACTOR_HEADER,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise InquisitorError("Inquisitor base URL is not configured")
        self.base_url = base_url.rstrip("/")
        self.workspace_id = workspace_id
        self._headers = {actor_header: actor}
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None

    # -- plumbing ---------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise InquisitorError(f"could not reach Inquisitor at {url}: {exc}") from exc
        if response.status_code == 403:
            raise InquisitorError(
                "Inquisitor rejected the actor header; check that the tracker's address is "
                "listed in trusted_proxy_addresses and the actor is mapped"
            )
        if response.status_code >= 400:
            raise InquisitorError(
                f"Inquisitor returned {response.status_code} for {path}: {response.text[:300]}"
            )
        return response

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> InquisitorClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- reads ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/status").json()

    def list_campaigns(self) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", "/api/campaigns", params={"workspace_id": self.workspace_id}
        ).json()
        return payload.get("campaigns", [])

    def list_vault(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        campaign_id: str | None = None,
        agency_id: str | None = None,
        file_type: str | None = None,
    ) -> list[VaultFile]:
        """List evidence-vault source files, newest first.

        ``date_from`` filters on Inquisitor's ``received_at``, which is when the
        agency's production landed — the right watermark for an incremental
        sync.
        """
        params: dict[str, str] = {"workspace_id": self.workspace_id}
        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        if campaign_id:
            params["campaign"] = campaign_id
        if agency_id:
            params["agency"] = agency_id
        if file_type:
            params["file_type"] = file_type

        payload = self._request("GET", "/api/vault", params=params).json()
        rows = payload.get("records") or payload.get("files") or payload.get("vault") or []
        if isinstance(payload, list):  # tolerate a bare list
            rows = payload
        return [VaultFile.from_row(row) for row in rows]

    def download_original(self, source_id: str) -> bytes:
        """Fetch the immutable original bytes of a vault file."""
        response = self._request("GET", f"/evidence/{source_id}/original")
        return response.content

    # -- writes -----------------------------------------------------------
    def create_campaign(self, command: str) -> Campaign:
        """Create a CPRA campaign from a natural-language command.

        Inquisitor's planner turns the command into a target matrix of
        agencies; targets are then sent individually, which keeps a human in
        the loop for anything the planner is unsure about.
        """
        payload = self._request(
            "POST",
            "/api/campaigns",
            json={"workspace_id": self.workspace_id, "command": command},
        ).json()
        value = payload.get("campaign", {})
        return Campaign(
            id=str(value.get("id")),
            number=value.get("number"),
            title=value.get("title"),
            state=value.get("state"),
            intent=value.get("intent"),
            proposed_targets=value.get("proposed_targets") or [],
        )

    def send_campaign_target(self, campaign_id: str, target_id: str) -> dict[str, Any]:
        response = self._request(
            "POST", f"/api/campaigns/{campaign_id}/targets/{target_id}/send", json={}
        )
        return response.json() if response.content else {}
