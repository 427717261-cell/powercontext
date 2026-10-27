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

"""Row-level CSV Sources that keep one original table record per row."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"
_IDENTITY_PREFIX = "csv"
_UTF8_BOM = "\ufeff"


class CSVDocumentInput(BaseModel):
    """One offline CSV document whose rows are captured individually."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One original record identified by its document and primary key value."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured CSV row that can be used as table evidence."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class _CSVRowSourceAdapter:
    name = CSV_ROW_SOURCE_NAME
    version = CSV_ROW_SOURCE_VERSION
    input_class = CSVRowInput
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class _CSVRowTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable identity of one row from its document and key value."""

    _validate_document_id(document_id)
    source_id = f"{_IDENTITY_PREFIX}:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate one whole CSV document into row inputs."""

    _validate_document_id(value.document_id)
    if not value.primary_key:
        raise ValueError("primary_key must not be empty")

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("csv document content must be valid UTF-8") from error
    if text.startswith(_UTF8_BOM):
        text = text[len(_UTF8_BOM) :]

    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error

    if not records:
        raise ValueError("csv document must contain a header record")
    columns = records[0]
    if not columns:
        raise ValueError("csv header must not be empty")
    seen_columns: set[str] = set()
    for column in columns:
        if not column.strip():
            raise ValueError("csv header fields must not be blank")
        if column in seen_columns:
            raise ValueError(f"csv header field {column!r} is duplicated")
        seen_columns.add(column)
    if value.primary_key not in seen_columns:
        raise ValueError(f"primary_key {value.primary_key!r} is not a csv header field")

    key_index = columns.index(value.primary_key)
    width = len(columns)
    row_columns = tuple(columns)
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != width:
            raise ValueError(f"csv record width {len(record)} does not match header width {width}")
        key = record[key_index]
        if not key.strip():
            raise ValueError("csv primary key value must not be blank")
        if key in seen_keys:
            raise ValueError(f"csv primary key value {key!r} is duplicated")
        seen_keys.add(key)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key,
                columns=row_columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def _validate_document_id(document_id: str) -> None:
    if not document_id:
        raise ValueError("document_id must not be empty")
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")


_CSV_ROW_SOURCE_ADAPTER = _CSVRowSourceAdapter()

CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    _CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(_CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
