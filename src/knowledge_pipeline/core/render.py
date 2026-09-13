from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .contracts import KnowledgeDocument, SourceBundle
from .profile import KnowledgeProfile


CITATION_RE = re.compile(r"\[(SRC\d{3,6})\]")
SECTION_MARKER_RE = re.compile(r"^<!-- kp:section=([A-Za-z0-9._-]+) -->$", re.M)


class DocumentContractError(ValueError):
    pass


def parse_generation_output(value: Mapping[str, Any], profile: KnowledgeProfile, bundle: SourceBundle) -> tuple[dict[str, str], tuple[str, ...]]:
    sections = value.get("sections")
    if not isinstance(sections, dict):
        sections = {key: value.get(key) for key in profile.section_ids if key in value}
    unexpected = sorted(set(sections) - set(profile.section_ids))
    if unexpected:
        raise DocumentContractError("unexpected sections: " + ", ".join(unexpected))
    normalized: dict[str, str] = {}
    for definition in profile.sections:
        body = sections.get(definition.id)
        if definition.id in sections and not isinstance(body, str):
            raise DocumentContractError(f"section must be a string: {definition.id}")
        if definition.required and (not isinstance(body, str) or not body.strip()):
            raise DocumentContractError(f"missing required section: {definition.id}")
        if isinstance(body, str) and body.strip():
            normalized[definition.id] = body.strip()
    used = value.get("used_source_ids")
    if not isinstance(used, list):
        raise DocumentContractError("used_source_ids must be an array")
    used_ids = tuple(dict.fromkeys(str(item).upper() for item in used))
    valid = {item.source_id for item in bundle.sources}
    unknown = sorted(set(used_ids) - valid)
    if unknown:
        raise DocumentContractError("unknown source IDs: " + ", ".join(unknown))
    cited = set(CITATION_RE.findall("\n".join(normalized.values())))
    unknown_citations = sorted(cited - valid)
    if unknown_citations:
        raise DocumentContractError("unknown citations: " + ", ".join(unknown_citations))
    if cited - set(used_ids):
        raise DocumentContractError("used_source_ids does not include every cited source")
    return normalized, used_ids


def render_markdown(document: KnowledgeDocument, profile: KnowledgeProfile) -> str:
    header = {
        "document_id": document.document_id,
        "profile": document.profile_id,
        "profile_version": document.profile_version,
        "schema_version": document.schema_version,
        "source_bundle_sha256": document.source_bundle_sha256,
        "content_sha256": document.content_sha256,
        "generation": document.generation,
        "used_source_ids": list(document.used_source_ids),
    }
    lines = ["<!-- knowledge-pipeline-metadata", json.dumps(header, ensure_ascii=False, sort_keys=True), "-->", "", f"# {document.title}", ""]
    titles = {item.id: item.title for item in profile.sections}
    # SQLite canonical JSON sorts keys. Render in template order after reload
    # so an exported card has the same reading order as its saved Markdown.
    section_ids = [item.id for item in profile.sections if item.id in document.sections]
    section_ids.extend(key for key in document.sections if key not in titles)
    for section_id in section_ids:
        body = document.sections[section_id]
        lines.extend([f"<!-- kp:section={section_id} -->", f"## {titles.get(section_id, section_id)}", "", body.strip(), ""])
    return "\n".join(lines).strip() + "\n"


def validate_document(document: KnowledgeDocument, profile: KnowledgeProfile, bundle: SourceBundle) -> list[str]:
    errors: list[str] = []
    if document.profile_id != profile.id or document.profile_version != profile.version:
        errors.append("document profile binding mismatch")
    if document.source_bundle_sha256 != bundle.bundle_sha256:
        errors.append("document source bundle binding mismatch")
    missing = [item.id for item in profile.sections if item.required and item.id not in document.sections]
    if missing:
        errors.append("missing required sections: " + ", ".join(missing))
    valid = {item.source_id for item in bundle.sources}
    cited = set(CITATION_RE.findall("\n".join(document.sections.values())))
    unknown = sorted((set(document.used_source_ids) | cited) - valid)
    if unknown:
        errors.append("unknown source references: " + ", ".join(unknown))
    if cited - set(document.used_source_ids):
        errors.append("cited sources missing from used_source_ids")
    return errors
