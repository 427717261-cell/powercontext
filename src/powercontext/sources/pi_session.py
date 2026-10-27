# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pi session Sources that capture the raw visible events of one selected branch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_IDENTITY_PREFIX = "pi"
_CHECKPOINT_VERSION = 1
_SESSION_EVENT_TYPE = "session"
_SESSION_EVENT_VERSION = 3
_UNKNOWN = "unknown"


class PiSessionInput(BaseModel):
    """One UTF-8 JSONL Pi session archive and the branch terminal chosen from it."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch together with its original record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi session event that can be used as conversation evidence."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class _PiEventSourceAdapter:
    name = PI_EVENT_SOURCE_NAME
    version = PI_EVENT_SOURCE_VERSION
    input_class = PiEventInput
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class _PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one event from its session and event identifiers."""

    readable = f"{_IDENTITY_PREFIX}:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(readable) <= MAX_SOURCE_ID_LENGTH:
        return readable
    digest = hashlib.sha256(f"{session_id}\x1f{event_id}".encode()).hexdigest()
    return f"{_IDENTITY_PREFIX}:{digest}"


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse one Pi session archive into the raw visible events of the selected branch."""

    session_id, records = _parse_archive(value.content)
    branch = _selected_branch(records, value.leaf_id)
    events: list[PiEventInput] = []
    for record in branch:
        event = _visible_event(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


class PiSessionConnector:
    """Import newly appended visible events of one Pi session branch."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        session_id, records = _parse_archive(self._path.read_bytes())
        branch = _selected_branch(records, self._leaf_id)
        imported = _imported_events(session.checkpoint)
        _reject_rewritten_records(session_id, records, imported)
        for record in branch:
            event = _visible_event(session_id, record)
            if event is None:
                continue
            source_id = pi_event_source_id(session_id, event.event_id)
            if source_id in imported:
                continue
            await session.submit(source_id, PI_EVENT_SOURCE_NAME, event)
            imported[source_id] = {
                "event_id": event.event_id,
                "record_sha256": _record_digest(record),
            }
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": _CHECKPOINT_VERSION, "events": imported},
        )


def _parse_archive(content: bytes) -> tuple[str, tuple[dict[str, JsonValue], ...]]:
    """Decode and validate one whole archive without returning partial results."""

    if not isinstance(content, bytes):
        raise ValueError("pi session content must be bytes")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("pi session content must be valid UTF-8") from error

    rows: list[dict[str, JsonValue]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"pi session line {line_number} is not valid JSON") from error
        if not isinstance(row, dict):
            raise ValueError(f"pi session line {line_number} must be a JSON object")
        rows.append(row)

    if not rows:
        raise ValueError("pi session archive must start with a session header")

    header = rows[0]
    if header.get("type") != _SESSION_EVENT_TYPE:
        raise ValueError("pi session archive must start with a session header")
    if header.get("version") != _SESSION_EVENT_VERSION:
        raise ValueError(f"pi session header must declare version {_SESSION_EVENT_VERSION}")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("pi session header must declare a non-empty session id")

    seen: dict[str, dict[str, JsonValue]] = {}
    records: list[dict[str, JsonValue]] = []
    for row in rows[1:]:
        event_id = row.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every pi session record must declare a non-empty id")
        if event_id in seen:
            raise ValueError(f"pi session record id {event_id!r} is duplicated")
        parent_id = row.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id.strip():
                raise ValueError(f"pi session record {event_id!r} has an invalid parentId")
            if parent_id not in seen:
                raise ValueError(f"pi session record {event_id!r} references a missing parent")
        seen[event_id] = row
        records.append(row)
    return session_id, tuple(records)


def _selected_branch(
    records: tuple[dict[str, JsonValue], ...],
    leaf_id: str | None,
) -> tuple[dict[str, JsonValue], ...]:
    """Return the root-to-leaf ancestor chain of the requested terminal record."""

    by_id = {record["id"]: record for record in records}
    if leaf_id is None:
        if not records:
            raise ValueError("pi session archive has no selectable leaf record")
        leaf_id = str(records[-1]["id"])
    if leaf_id not in by_id:
        raise ValueError(f"pi session leaf {leaf_id!r} is not present in the archive")

    chain: list[dict[str, JsonValue]] = []
    current: str | None = leaf_id
    while current is not None:
        record = by_id[current]
        chain.append(record)
        parent = record.get("parentId")
        current = parent if isinstance(parent, str) else None
    chain.reverse()
    return tuple(chain)


def _visible_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    """Render one record into its visible evidence, or ignore it when it has no body."""

    role, body = _render_record(record)
    if not role or body is None:
        return None
    if not body.strip():
        return None
    event_id = record.get("id")
    if not isinstance(event_id, str):
        return None
    parent_id = record.get("parentId")
    event_type = record.get("type")
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id if isinstance(parent_id, str) else None,
        event_type=event_type if isinstance(event_type, str) else _UNKNOWN,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _render_record(record: dict[str, JsonValue]) -> tuple[str, str | None]:
    event_type = record.get("type")
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        return str(event_type), summary if isinstance(summary, str) else None
    if event_type == "custom_message":
        return "custom", _blocks_text(record.get("content"))
    if event_type != "message":
        return "", None

    message = record.get("message")
    if not isinstance(message, dict):
        return "", None
    role = message.get("role")
    if not isinstance(role, str) or not role:
        return "", None
    if role == "toolResult":
        return role, _tool_result_body(message)
    if role == "bashExecution":
        return role, _bash_body(message)
    return role, _blocks_text(message.get("content"))


def _tool_result_body(message: Mapping[str, JsonValue]) -> str:
    tool_name = message.get("toolName")
    call_id = message.get("toolCallId")
    name = tool_name if isinstance(tool_name, str) and tool_name else _UNKNOWN
    identifier = call_id if isinstance(call_id, str) and call_id else _UNKNOWN
    return f"[tool_result {name} {identifier}] {_blocks_text(message.get('content'))}"


def _bash_body(message: Mapping[str, JsonValue]) -> str:
    command = message.get("command")
    output = message.get("output")
    text = f"$ {command if isinstance(command, str) else ''}"
    if isinstance(output, str) and output:
        return f"{text}\n{output}"
    return text


def _blocks_text(content: JsonValue) -> str:
    """Join visible text and tool-call blocks; thinking, image and other blocks are ignored."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif kind == "toolCall":
            parts.append(_tool_call_text(block))
    return "\n".join(parts)


def _tool_call_text(block: Mapping[str, JsonValue]) -> str:
    name = block.get("name")
    call_id = block.get("id")
    tool_name = name if isinstance(name, str) and name else _UNKNOWN
    identifier = call_id if isinstance(call_id, str) and call_id else _UNKNOWN
    return f"[tool_call {tool_name} {identifier}] {_compact_arguments(block.get('arguments'))}"


def _compact_arguments(arguments: JsonValue) -> str:
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _imported_events(checkpoint: JsonValue) -> dict[str, dict[str, JsonValue]]:
    """Read the durable progress written by a previous run, ignoring unknown shapes."""

    if not isinstance(checkpoint, dict):
        return {}
    if checkpoint.get("version") != _CHECKPOINT_VERSION:
        return {}
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        return {}
    imported: dict[str, dict[str, JsonValue]] = {}
    for source_id, entry in events.items():
        if isinstance(source_id, str) and isinstance(entry, dict):
            imported[source_id] = dict(entry)
    return imported


def _reject_rewritten_records(
    session_id: str,
    records: tuple[dict[str, JsonValue], ...],
    imported: Mapping[str, dict[str, JsonValue]],
) -> None:
    """Fail before importing anything when a stored record no longer matches its original text."""

    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str):
            continue
        entry = imported.get(pi_event_source_id(session_id, event_id))
        if entry is None:
            continue
        if entry.get("record_sha256") != _record_digest(record):
            raise ValueError(f"pi session record {event_id!r} changed after it was imported")


_PI_EVENT_SOURCE_ADAPTER = _PiEventSourceAdapter()

PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    _PI_EVENT_SOURCE_ADAPTER,
    version=PI_EVENT_SOURCE_VERSION,
    projections=(_PiEventTextEvidenceProjection(),),
)

__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
