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

"""Automatic admission when a Scope's unprocessed Journal entries reach a threshold."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import suppress
from types import TracebackType

from sqlalchemy import select

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors

_TICK_SECONDS = 0.25
_PAGE_SIZE = 100
logger = logging.getLogger(__name__)


class SourceCountPump:
    """Accept one Source window per Scope once its unprocessed count reaches a threshold.

    The pump only decides *whether* to invoke; it never discovers work, fences a
    Worker, or advances a cursor. Admission reuses the existing requested
    generation channel, so failure and restart recovery stay the Supervisor's
    responsibility.

    Three gates keep the decision honest:

    * leadership - only the process holding the lease requests, because the
      intent repository does not validate a fence;
    * in-flight - a Scope with an unhandled request is skipped, because a second
      request would let the Supervisor dispatch the next window before the
      current one is acknowledged;
    * threshold - the unprocessed count is ``head - cursor``, never a dirty
      generation, because artifacts also mark dirty and dirty never decreases.
    """

    def __init__(
        self,
        database: AsyncDatabase,
        cursors: SourceCursorRepository,
        /,
        *,
        bindings: Sequence[tuple[str, str, int]],
        supervisors: Callable[[], ArtifactProcessingSupervisors | None],
    ) -> None:
        self._database = database
        self._cursors = cursors
        self._intents = ArtifactProcessingIntentRepository()
        self._bindings = tuple(bindings)
        self._supervisors = supervisors
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SourceCountPump:
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._pump_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a failed tick must not end the pump
                # No local backoff: the Supervisor owns retry pacing, and this
                # loop is the only path that re-reaches a threshold later.
                log_safely(logger, logging.WARNING, "source count pump tick failed", exc_info=error)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_TICK_SECONDS)
            except TimeoutError:
                continue

    async def _pump_once(self) -> None:
        supervisors = self._supervisors()
        if supervisors is None:
            return
        # Gate 1: the requested channel is unfenced, so only the lease holder may use it.
        if not any(getattr(supervisor, "fence", None) is not None for supervisor in supervisors.supervisors):
            return
        for binding_name, family, threshold in self._bindings:
            accepted = await self._accept_pages(binding_name, threshold)
            if accepted:
                # Waking after commit keeps a Worker from reading an uncommitted request.
                supervisors.wake(binding_name)
                log_safely(
                    logger,
                    logging.DEBUG,
                    "source count threshold reached",
                    extra={"binding": binding_name, "family": family, "threshold": threshold},
                )

    async def _accept_pages(self, binding_name: str, threshold: int) -> bool:
        heads = SOURCE_JOURNAL_HEADS_TABLE
        after_scope: str | None = None
        accepted = False
        while True:
            async with self._database.transaction() as connection:
                statement = (
                    select(heads.c.scope_id, heads.c.position).order_by(heads.c.scope_id).limit(_PAGE_SIZE)
                )
                if after_scope is not None:
                    statement = statement.where(heads.c.scope_id > after_scope)
                rows = (await connection.execute(statement)).all()
                if not rows:
                    break
                cursors = await self._cursors.load_many(
                    connection, [str(row.scope_id) for row in rows], binding_name
                )
                for row in rows:
                    scope_id = str(row.scope_id)
                    cursor = cursors.get(scope_id)
                    # Gate 3: the Journal head counts every unprocessed identity, so the
                    # difference against the Family cursor is the pending count.
                    if int(row.position) - (0 if cursor is None else cursor.cursor.sequence) < threshold:
                        continue
                    intent = await self._intents.load(connection, scope_id, binding_name)
                    # Gate 2: an unhandled request already owns the next window.
                    if intent is not None and intent.requested_generation > intent.handled_generation:
                        continue
                    # Locking is a write, so it waits until the cheap filters pass.
                    locked = await self._intents.load(connection, scope_id, binding_name, for_update=True)
                    if locked is not None and locked.requested_generation > locked.handled_generation:
                        continue
                    await self._intents.request(connection, scope_id, binding_name)
                    accepted = True
            if len(rows) < _PAGE_SIZE:
                break
            after_scope = str(rows[-1].scope_id)
        return accepted


__all__ = ["SourceCountPump"]
