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

"""Source-driven availability for Memory and Topic Memory evidence.

A Scope owns one dependency graph. Captured Sources sit at its roots; Memory entries
and Topic Memory revisions hang off them under an ``all`` or ``any`` support policy.
Withdrawing a Source withdraws everything that no longer has support, and restoring
it puts that evidence back. Withdrawal only changes use eligibility: the registered
body stays readable through its original reference.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.persistence.source_lifecycle import (
    COMPLETE,
    LifecycleNodeRow,
    SourceLifecycleRepository,
    encode_reference,
)
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

SupportPolicy = Literal["all", "any"]
ResolvePurpose = Literal["generation", "audit"]

_SOURCE = "source"
_MEMORY = "memory"
_TOPIC = "topic"
_PURPOSES = frozenset({"generation", "audit"})
_MEMORY_REACTIVATION_REASON = "source lifecycle restore"
_MEMORY_WITHDRAWAL_REASON = "source lifecycle withdrawal"


class SourceLifecycleError(ValueError):
    """A lifecycle registration, event, or lookup broke its contract."""


class SourceLifecycleEvent(BaseModel):
    """One lifecycle transition for a registered Source.

    ``revision`` pins the Source's lifecycle version the caller observed and
    ``sequence`` orders that Source's own events. Replaying an accepted event is
    idempotent; a lower or conflicting sequence is rejected.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty, non-blank string")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def _require_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value


class SourceLifecycleApplication:
    """Select the lifecycle graph of one Scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleApplication:
        return ScopedSourceLifecycleApplication(self._runtime, scope_id)


class ScopedSourceLifecycleApplication:
    """Register evidence dependencies and propagate Source state through them."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(
        self,
        node_id: str,
        reference: SourceRef,
        /,
        *,
        revision: int,
    ) -> None:
        """Register a captured Source root at the lifecycle version it is read at."""

        await self._register(
            node_id,
            kind=_SOURCE,
            reference=_source_payload(reference),
            parents=(),
            policy=None,
            lifecycle_revision=_positive_int(revision, "revision"),
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...],
        policy: SupportPolicy,
    ) -> None:
        """Register one Memory entry as depending on already registered parents."""

        await self._register(
            node_id,
            kind=_MEMORY,
            reference=_memory_payload(citation),
            parents=parents,
            policy=_support_policy(policy),
            lifecycle_revision=0,
        )

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...],
        policy: SupportPolicy,
    ) -> None:
        """Register one Topic Memory revision as depending on registered parents."""

        await self._register(
            node_id,
            kind=_TOPIC,
            reference=_topic_payload(reference),
            parents=parents,
            policy=_support_policy(policy),
            lifecycle_revision=0,
        )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event and return every usable node id in ordinal order."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event-type")
        repository = self._repository()
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            async with repository.transaction() as connection:
                nodes = await repository.load_nodes(connection, self.scope_id)
                index = {node.node_id: node for node in nodes}
                source = index.get(event.node_id)
                if source is None or source.kind != _SOURCE:
                    raise SourceLifecycleError("unknown-source")
                if source.lifecycle_revision != event.revision:
                    raise SourceLifecycleError("revision-mismatch")
                stored = await repository.load_event(
                    connection,
                    self.scope_id,
                    event.node_id,
                    event.sequence,
                )
                if stored is None:
                    if event.sequence <= source.last_sequence:
                        raise SourceLifecycleError("sequence-not-increasing")
                    await repository.insert_event(
                        connection,
                        self.scope_id,
                        node_id=event.node_id,
                        sequence=event.sequence,
                        lifecycle_revision=event.revision,
                        available=event.available,
                        reason=event.reason,
                    )
                    await repository.update_node_state(
                        connection,
                        self.scope_id,
                        event.node_id,
                        available=event.available,
                        last_sequence=event.sequence,
                    )
                else:
                    if (
                        stored.lifecycle_revision,
                        stored.available,
                        stored.reason,
                    ) != (event.revision, event.available, event.reason):
                        raise SourceLifecycleError("sequence-conflict")
                    if stored.status == COMPLETE:
                        return sorted(node.node_id for node in nodes if node.available)

            availability = self._propagate(nodes, {event.node_id: event.available})
            await self._sync_memory_states(nodes, availability)
            async with repository.transaction() as connection:
                for node in nodes:
                    if availability[node.node_id] != node.available:
                        await repository.update_node_state(
                            connection,
                            self.scope_id,
                            node.node_id,
                            available=availability[node.node_id],
                        )
                await repository.complete_event(
                    connection,
                    self.scope_id,
                    event.node_id,
                    event.sequence,
                )
        return sorted(node_id for node_id, usable in availability.items() if usable)

    async def resolve(self, node_id: str, /, *, purpose: ResolvePurpose) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the original reference of one node for generation or audit use."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError("unknown-purpose")
        repository = self._repository()
        async with repository.transaction() as connection:
            nodes = await repository.load_nodes(connection, self.scope_id)
        node = next((item for item in nodes if item.node_id == node_id), None)
        if node is None:
            raise SourceLifecycleError("unknown-node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("unavailable-for-generation")
        return _decode(node.kind, node.reference)

    async def _register(
        self,
        node_id: str,
        *,
        kind: str,
        reference: str,
        parents: tuple[str, ...],
        policy: str | None,
        lifecycle_revision: int,
    ) -> None:
        identifier = _node_id(node_id)
        declares_support = kind != _SOURCE
        if declares_support:
            if not parents:
                raise SourceLifecycleError("missing-parents")
            if len(set(parents)) != len(parents):
                raise SourceLifecycleError("duplicate-parent")
            if identifier in parents:
                raise SourceLifecycleError("self-dependency")
            for parent in parents:
                if not isinstance(parent, str) or not parent.strip():
                    raise SourceLifecycleError("invalid-parent")
        elif parents or policy is not None:
            raise SourceLifecycleError("source-support")

        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            await self._require_readable(kind, reference)
            repository = self._repository()
            async with repository.transaction() as connection:
                nodes = await repository.load_nodes(connection, self.scope_id)
                existing = next((item for item in nodes if item.node_id == identifier), None)
                if existing is not None:
                    if (
                        existing.kind != kind
                        or existing.reference != reference
                        or existing.parents != tuple(parents)
                        or existing.policy != policy
                        or existing.lifecycle_revision != lifecycle_revision
                    ):
                        raise SourceLifecycleError("conflicting-registration")
                    return
                if declares_support:
                    index = {item.node_id: item for item in nodes}
                    missing = [parent for parent in parents if parent not in index]
                    if missing:
                        raise SourceLifecycleError("unknown-parent")
                    supported = [index[parent].available for parent in parents]
                    available = all(supported) if policy == "all" else any(supported)
                else:
                    available = True
                await repository.insert_node(
                    connection,
                    self.scope_id,
                    node_id=identifier,
                    kind=kind,
                    reference=reference,
                    parents=tuple(parents),
                    policy=policy,
                    available=available,
                    position=len(nodes),
                    lifecycle_revision=lifecycle_revision,
                )
            if kind == _MEMORY and not available:
                await self._set_memory_state(_decode(_MEMORY, reference), active=False)

    async def _require_readable(self, kind: str, reference: str) -> None:
        """Reject a registration whose reference cannot be read back."""

        payload = json.loads(reference)
        try:
            if kind == _SOURCE:
                await self._runtime.records.for_scope(self.scope_id).get_source(
                    payload["source_type"],
                    payload["source_id"],
                )
            elif kind == _MEMORY:
                await self._runtime.memory.for_scope(self.scope_id).get(
                    GetMemoryEntryRequest(citation=_decode(_MEMORY, reference))
                )
            else:
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    payload["family"],
                    payload["artifact_id"],
                    payload["revision"],
                )
        except SourceLifecycleError:
            raise
        except (LookupError, ValueError) as error:
            raise SourceLifecycleError("unreadable-reference") from error

    def _propagate(self, nodes: tuple[LifecycleNodeRow, ...], overrides: dict[str, bool]) -> dict[str, bool]:
        """Recompute the whole graph in registration order, which is topological."""

        availability = {node.node_id: node.available for node in nodes}
        availability.update(overrides)
        for node in nodes:
            if node.kind == _SOURCE:
                continue
            supported = [availability[parent] for parent in node.parents]
            availability[node.node_id] = all(supported) if node.policy == "all" else any(supported)
        return availability

    async def _sync_memory_states(self, nodes: tuple[LifecycleNodeRow, ...], availability: dict[str, bool]) -> None:
        """Mirror computed availability into native Memory entry state."""

        for node in nodes:
            if node.kind != _MEMORY:
                continue
            target = availability[node.node_id]
            if target == node.available:
                continue
            await self._set_memory_state(_decode(_MEMORY, node.reference), active=target)

    async def _set_memory_state(self, citation: MemoryCitation, *, active: bool) -> None:
        """Move one registered entry between active and inactive without a body rewrite."""

        async with self._runtime._context(self.scope_id) as context:
            service: MemoryService = context.artifacts.memory
            try:
                current = await service.head(context.artifacts.memory_artifact_id)
            except ArtifactNotFoundError as error:
                raise SourceLifecycleError("memory-missing") from error
            manifest = next(
                (
                    item
                    for item in current.content.manifest.entries
                    if item.entry_id == citation.entry_id
                ),
                None,
            )
            if manifest is None:
                raise SourceLifecycleError("memory-entry-missing")
            if (manifest.state == "active") is active:
                return
            version = next(
                (item for item in await service.entries(current) if item.entry_id == citation.entry_id),
                None,
            )
            if version is None:
                raise SourceLifecycleError("memory-entry-missing")
            if active:
                await service.reactivate(current, entries=(version,), reason=_MEMORY_REACTIVATION_REASON)
            else:
                await service.forget(current, entries=(version,), reason=_MEMORY_WITHDRAWAL_REASON)

    def _repository(self) -> SourceLifecycleRepository:
        repository = self._runtime.lifecycle_repository
        if repository is None:
            raise SourceLifecycleError("lifecycle-store-unavailable")
        return repository


def _node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("invalid-node-id")
    return value


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceLifecycleError(f"invalid-{name}")
    return value


def _support_policy(value: str) -> str:
    if value not in {"all", "any"}:
        raise SourceLifecycleError("invalid-policy")
    return value


def _source_payload(reference: SourceRef) -> str:
    if not isinstance(reference, SourceRef):
        raise SourceLifecycleError("invalid-source-reference")
    return encode_reference({"source_type": reference.source_type, "source_id": reference.source_id})


def _memory_payload(citation: MemoryCitation) -> str:
    if not isinstance(citation, MemoryCitation):
        raise SourceLifecycleError("invalid-memory-reference")
    return encode_reference(
        {
            "memory_family": citation.memory_ref.family,
            "memory_artifact_id": citation.memory_ref.artifact_id,
            "memory_revision": citation.memory_ref.revision,
            "entry_id": citation.entry_id,
            "entry_version_id": citation.entry_version_id,
        }
    )


def _topic_payload(reference: ArtifactRef) -> str:
    if not isinstance(reference, ArtifactRef):
        raise SourceLifecycleError("invalid-topic-reference")
    return encode_reference(
        {
            "family": reference.family,
            "artifact_id": reference.artifact_id,
            "revision": reference.revision,
        }
    )


def _decode(kind: str, reference: str) -> Any:
    payload = json.loads(reference)
    if kind == _SOURCE:
        return SourceRef(
            source_type=payload["source_type"],
            source_id=payload["source_id"],
        )
    if kind == _MEMORY:
        return MemoryCitation(
            memory_ref=ArtifactRef(
                family=payload["memory_family"],
                artifact_id=payload["memory_artifact_id"],
                revision=payload["memory_revision"],
            ),
            entry_id=payload["entry_id"],
            entry_version_id=payload["entry_version_id"],
        )
    return ArtifactRef(
        family=payload["family"],
        artifact_id=payload["artifact_id"],
        revision=payload["revision"],
    )


__all__ = ["SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
