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

"""Relational storage for the source-driven evidence lifecycle graph.

One row per registered node keeps the whole graph readable in registration order,
which is also a topological order: a child cannot be registered before its parents.
Events append as ``pending`` and flip to ``complete`` only after propagation, so an
interrupted write is resumed by replaying the same event rather than rejected.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.builtin.sources import validate_scope_id

PENDING = "pending"
COMPLETE = "complete"


@dataclass(frozen=True)
class LifecycleNodeRow:
    """One registered node and the state the last completed propagation left."""

    node_id: str
    kind: str
    reference: str
    parents: tuple[str, ...]
    policy: str | None
    available: bool
    position: int
    lifecycle_revision: int
    last_sequence: int


@dataclass(frozen=True)
class LifecycleEventRow:
    """One lifecycle event and how far its propagation got."""

    node_id: str
    sequence: int
    lifecycle_revision: int
    available: bool
    reason: str
    status: str


def encode_reference(payload: dict[str, Any]) -> str:
    """Encode one reference with a stable key order for exact replay comparison."""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encode_parents(parents: tuple[str, ...]) -> str:
    """Encode the parent list without reordering a caller's declaration."""

    return json.dumps(list(parents), ensure_ascii=False, separators=(",", ":"))


class SourceLifecycleRepository:
    """Read and write the lifecycle graph of one Scope at a time."""

    def __init__(self, database: AsyncDatabase, /) -> None:
        self._database = database

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Yield a connection in a transaction the caller commits on exit."""

        async with self._database.transaction() as connection:
            yield connection

    async def load_nodes(self, connection: AsyncConnection, scope_id: str, /) -> tuple[LifecycleNodeRow, ...]:
        """Return every registered node ordered by registration position."""

        scope = validate_scope_id(scope_id)
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope)
                .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.position)
            )
        ).mappings()
        return tuple(
            LifecycleNodeRow(
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),
                reference=str(row["reference"]),
                parents=tuple(json.loads(row["parents"])),
                policy=None if row["policy"] is None else str(row["policy"]),
                available=bool(row["available"]),
                position=int(row["position"]),
                lifecycle_revision=int(row["lifecycle_revision"]),
                last_sequence=int(row["last_sequence"]),
            )
            for row in rows
        )

    async def insert_node(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        node_id: str,
        kind: str,
        reference: str,
        parents: tuple[str, ...],
        policy: str | None,
        available: bool,
        position: int,
        lifecycle_revision: int,
        last_sequence: int = 0,
    ) -> None:
        """Register one node. The caller owns the surrounding transaction."""

        await connection.execute(
            insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                scope_id=validate_scope_id(scope_id),
                node_id=node_id,
                kind=kind,
                reference=reference,
                parents=encode_parents(parents),
                policy=policy,
                available=available,
                position=position,
                lifecycle_revision=lifecycle_revision,
                last_sequence=last_sequence,
            )
        )

    async def load_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
        sequence: int,
        /,
    ) -> LifecycleEventRow | None:
        """Return one already recorded event, or ``None`` when it is new."""

        row = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_EVENTS_TABLE).where(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == validate_scope_id(scope_id),
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
                )
            )
        ).mappings().first()
        if row is None:
            return None
        return LifecycleEventRow(
            node_id=str(row["node_id"]),
            sequence=int(row["sequence"]),
            lifecycle_revision=int(row["lifecycle_revision"]),
            available=bool(row["available"]),
            reason=str(row["reason"]),
            status=str(row["status"]),
        )

    async def insert_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        node_id: str,
        sequence: int,
        lifecycle_revision: int,
        available: bool,
        reason: str,
    ) -> None:
        """Record an accepted event before its propagation starts."""

        await connection.execute(
            insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                scope_id=validate_scope_id(scope_id),
                node_id=node_id,
                sequence=sequence,
                lifecycle_revision=lifecycle_revision,
                available=available,
                reason=reason,
                status=PENDING,
            )
        )

    async def complete_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
        sequence: int,
        /,
    ) -> None:
        """Mark one event's propagation as finished."""

        await connection.execute(
            update(SOURCE_LIFECYCLE_EVENTS_TABLE)
            .where(
                SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == validate_scope_id(scope_id),
                SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
            )
            .values(status=COMPLETE)
        )

    async def update_node_state(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
        /,
        *,
        available: bool | None = None,
        last_sequence: int | None = None,
    ) -> None:
        """Persist the state one propagation computed for a node."""

        values: dict[str, Any] = {}
        if available is not None:
            values["available"] = available
        if last_sequence is not None:
            values["last_sequence"] = last_sequence
        if not values:
            return
        await connection.execute(
            update(SOURCE_LIFECYCLE_NODES_TABLE)
            .where(
                SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == validate_scope_id(scope_id),
                SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
            )
            .values(**values)
        )

    async def unavailable_topic_artifacts(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
    ) -> frozenset[str]:
        """Return the Topic Memory artifact identities this graph currently withholds."""

        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE.c.reference).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == validate_scope_id(scope_id),
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                )
            )
        ).scalars()
        artifacts: set[str] = set()
        for reference in rows:
            payload = json.loads(reference)
            artifact_id = payload.get("artifact_id")
            if isinstance(artifact_id, str):
                artifacts.add(artifact_id)
        return frozenset(artifacts)


__all__ = [
    "COMPLETE",
    "PENDING",
    "LifecycleEventRow",
    "LifecycleNodeRow",
    "SourceLifecycleRepository",
    "encode_parents",
    "encode_reference",
]
