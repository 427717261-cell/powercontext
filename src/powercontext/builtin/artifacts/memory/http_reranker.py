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

"""HTTP cross-encoder reranking for coarse Memory search candidates."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score every coarse candidate through one batched HTTP service.

    The service assigns a relevance score per candidate; this client keeps the original
    one-based candidate ranks across batches so the final selection is made once over the
    whole pool, with ties resolved by the original coarse order.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate and return the globally best original ranks."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text}
                    for rank, candidate in zip(ranks, batch, strict=True)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            scores.update(_parse_scores(response, frozenset(ranks)))

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _score in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, batch_ranks: frozenset[int]) -> dict[int, float]:
    body = response.json()
    if not isinstance(body, dict) or set(body) != {"scores"}:
        raise ValueError("rerank response must contain exactly one scores field")  # noqa: TRY003
    records = body["scores"]
    if not isinstance(records, list):
        raise ValueError("rerank response scores must be a list")  # noqa: TRY003

    parsed: dict[int, float] = {}
    for record in records:
        rank, score = _parse_record(record)
        if rank not in batch_ranks:
            raise ValueError("rerank response reported a rank from outside its batch")  # noqa: TRY003
        if rank in parsed:
            raise ValueError("rerank response reported the same rank twice")  # noqa: TRY003
        parsed[rank] = score
    if parsed.keys() != batch_ranks:
        raise ValueError("rerank response did not cover every candidate in its batch")  # noqa: TRY003
    return parsed


def _parse_record(record: Any) -> tuple[int, float]:
    if not isinstance(record, dict) or set(record) != {"rank", "score"}:
        raise ValueError("rerank response records must contain exactly rank and score")  # noqa: TRY003
    rank = record["rank"]
    score = record["score"]
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise ValueError("rerank response rank must be an integer")  # noqa: TRY003
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise ValueError("rerank response score must be a number")  # noqa: TRY003
    value = float(score)
    if not math.isfinite(value):
        raise ValueError("rerank response score must be finite")  # noqa: TRY003
    return rank, value


__all__ = [
    "HTTP_RERANK_POLICY_ID",
    "HTTPMemoryReranker",
]
