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

"""Chunked, digest-addressed transport for prepared context.

A :class:`ContextPack` is the complete chunked form of one prepared context, and a
:class:`ContextPackDelta` carries only the chunks a receiver cannot already derive from the
previous pack. Both are self-describing: everything a receiver needs to rebuild the next
context travels in the serialized bytes, so rebuilding never depends on sender state.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from powercontext.builtin.runtime.errors import ContextPackError

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_DIGEST_PREFIX = "sha256:"


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete prepared context split into ordered, byte-bounded chunks."""

    items: tuple[bytes, ...]
    _digest: str = field(init=False, repr=False, compare=False)
    _wire: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            items = tuple(self.items)
        except TypeError as error:
            raise ContextPackError("pack items must be a sequence of bytes") from error
        for item in items:
            if not isinstance(item, bytes):
                raise ContextPackError("pack items must be bytes")
        digest = _pack_digest(items)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "_digest", digest)
        object.__setattr__(self, "_wire", _pack_wire(digest, items))

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        """Assemble a complete pack from already chunked bytes."""

        return cls(items)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split text into deterministic chunks of at most ``chunk_bytes`` UTF-8 bytes."""

        if not isinstance(content, str):
            raise ContextPackError("pack content must be text")
        return cls(_chunk_text(content.encode("utf-8"), chunk_bytes))

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPack:
        """Decode and verify one serialized complete pack."""

        payload = _decode_wire(data, _PACK_FIELDS)
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("pack schema is not supported")
        items = _decode_items(payload["items"])
        digest = _validate_digest(payload["digest"])
        byte_count = payload["byte_count"]
        if type(byte_count) is not int:
            raise ContextPackError("pack byte_count must be an integer")
        if byte_count != len(data):
            raise ContextPackError("pack byte_count does not match the serialized size")
        pack = cls(items)
        if pack.digest != digest:
            raise ContextPackError("pack digest does not match its items")
        return pack

    @property
    def digest(self) -> str:
        return self._digest

    @property
    def byte_count(self) -> int:
        return len(self._wire)

    def to_bytes(self) -> bytes:
        return self._wire


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """The base prefix a receiver keeps plus the chunks it must append."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_digest(self.base_digest)
        _validate_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        try:
            appended = tuple(self.appended)
        except TypeError as error:
            raise ContextPackError("delta appended must be a sequence of bytes") from error
        for item in appended:
            if not isinstance(item, bytes):
                raise ContextPackError("delta appended items must be bytes")
        object.__setattr__(self, "appended", appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Keep the leading chunks the two packs share and send the target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta requires two complete packs")
        prefix_items = _shared_prefix(base.items, target.items)
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPackDelta:
        """Decode and verify one serialized delta."""

        payload = _decode_wire(data, _DELTA_FIELDS)
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("delta schema is not supported")
        return cls(
            base_digest=_validate_digest(payload["base_digest"]),
            target_digest=_validate_digest(payload["target_digest"]),
            prefix_items=payload["prefix_items"],
            appended=_decode_items(payload["appended"]),
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical_json(
            {
                "schema": CONTEXT_PACK_DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": [_encode_chunk(item) for item in self.appended],
            }
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a receiver-held base pack and one delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application requires a pack and a delta")
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the supplied pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base chunk count")
    target = ContextPack((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")
    return target


def _chunk_text(data: bytes, chunk_bytes: int) -> tuple[bytes, ...]:
    """Split at line ends inside the window, else at the last UTF-8 boundary that fits.

    Breaking on a newline keeps the stable line-structured head of a rendered context in
    chunks of its own, so a later delta can keep it even when the payload line changes.
    """

    if type(chunk_bytes) is not int or chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")
    chunks: list[bytes] = []
    start = 0
    total = len(data)
    while start < total:
        window_end = min(start + chunk_bytes, total)
        if window_end == total:
            chunks.append(data[start:window_end])
            break
        line_end = data.rfind(b"\n", start, window_end)
        if line_end >= start:
            chunks.append(data[start : line_end + 1])
            start = line_end + 1
            continue
        end = _utf8_boundary(data, window_end)
        if end == start:
            raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")
        chunks.append(data[start:end])
        start = end
    return tuple(chunks)


def _utf8_boundary(data: bytes, position: int) -> int:
    while position > 0 and data[position] & 0xC0 == 0x80:
        position -= 1
    return position


def _shared_prefix(left: tuple[bytes, ...], right: tuple[bytes, ...]) -> int:
    prefix = 0
    for left_item, right_item in zip(left, right, strict=False):
        if left_item != right_item:
            break
        prefix += 1
    return prefix


def _encode_chunk(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_items(value: Any) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("chunk fields must be Base64 string arrays")
    items: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("chunk fields must be Base64 string arrays")
        try:
            item = base64.b64decode(entry.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error) as error:
            raise ContextPackError("chunk is not valid Base64") from error
        if _encode_chunk(item) != entry:
            raise ContextPackError("chunk is not canonical Base64")
        items.append(item)
    return tuple(items)


def _decode_wire(data: bytes, expected: frozenset[str]) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        raise ContextPackError("wire payload must be bytes")
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ContextPackError("wire payload is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be a JSON object")
    if set(payload) != expected:
        raise ContextPackError("wire payload does not carry exactly the protocol fields")
    return payload


def _validate_digest(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError("digest must be a sha256 reference")
    hexdigest = value.removeprefix(_DIGEST_PREFIX)
    if len(hexdigest) != 64 or hexdigest.lower() != hexdigest:
        raise ContextPackError("digest must be a sha256 reference")
    try:
        int(hexdigest, 16)
    except ValueError as error:
        raise ContextPackError("digest must be a sha256 reference") from error
    return value


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": CONTEXT_PACK_SCHEMA, "items": [_encode_chunk(item) for item in items]}
    return _DIGEST_PREFIX + hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _pack_wire(digest: str, items: tuple[bytes, ...]) -> bytes:
    payload = {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [_encode_chunk(item) for item in items],
        "digest": digest,
    }
    # byte_count counts this serialization, so its own digits are part of the size it reports.
    byte_count = 0
    for _attempt in range(8):
        payload["byte_count"] = byte_count
        size = len(_canonical_json(payload))
        if size == byte_count:
            return _canonical_json(payload)
        byte_count = size
    raise ContextPackError("pack byte_count does not converge")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
