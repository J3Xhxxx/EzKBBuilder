from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


@dataclass(frozen=True)
class SectionDefinition:
    id: str
    title: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class CheckDefinition:
    id: str
    title: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class KnowledgeProfile:
    id: str
    title: str
    version: str
    schema_version: str
    sections: tuple[SectionDefinition, ...]
    evaluation_checks: tuple[CheckDefinition, ...]
    max_repairs: int
    prompt_dir: Path
    metadata: Mapping[str, Any]

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.sections)

    def prompt(self, role: str) -> str:
        name = {"generate": "generate_system.txt", "audit": "audit_system.txt", "repair": "repair_system.txt"}.get(role)
        if not name:
            raise ValueError(f"unsupported model role: {role}")
        target = (self.prompt_dir / name).resolve()
        if self.prompt_dir.resolve() not in target.parents:
            raise ValueError("prompt path escapes profile directory")
        if not target.is_file():
            raise RuntimeError(f"missing {role} prompt for profile {self.id}: {target}")
        return target.read_text(encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "version": self.version,
            "schema_version": self.schema_version,
            "sections": [item.__dict__ for item in self.sections],
            "evaluation_checks": [item.__dict__ for item in self.evaluation_checks],
            "max_repairs": self.max_repairs, "metadata": dict(self.metadata),
            "prompt_sha256": {
                role: hashlib.sha256(self.prompt(role).encode("utf-8")).hexdigest()
                for role in ("generate", "audit", "repair")
            },
        }


def _definitions(values: Iterable[Mapping[str, Any]], cls: type[SectionDefinition] | type[CheckDefinition]) -> tuple[Any, ...]:
    result = tuple(
        cls(
            id=str(value.get("id") or "").strip(),
            title=str(value.get("title") or value.get("id") or "").strip(),
            description=str(value.get("description") or "").strip(),
            required=bool(value.get("required", True)),
        )
        for value in values
    )
    ids = [item.id for item in result]
    if not result or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("profile definitions require unique, non-empty IDs")
    return result


class ProfileRegistry:
    def __init__(self, roots: Iterable[Path] | None = None) -> None:
        built_in = Path(str(files("knowledge_pipeline").joinpath("profiles")))
        external = tuple(Path(item).expanduser().resolve() for item in (roots or ()))
        self.roots = (built_in.resolve(), *external)

    def list(self) -> list[KnowledgeProfile]:
        result: dict[str, KnowledgeProfile] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/profile.yaml")):
                profile = self._load(path)
                if profile.id in result:
                    raise ValueError(f"duplicate profile ID: {profile.id}")
                result[profile.id] = profile
        return list(result.values())

    def get(self, profile_id: str) -> KnowledgeProfile:
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        raise KeyError(f"unknown knowledge profile: {profile_id}")

    @staticmethod
    def _load(path: Path) -> KnowledgeProfile:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError(f"profile root must be an object: {path}")
        profile_id = str(value.get("id") or "").strip()
        if not profile_id or path.parent.name != profile_id:
            raise ValueError(f"profile ID must match directory name: {path}")
        checks = (value.get("evaluation") or {}).get("checks") or []
        max_repairs = int((value.get("repair") or {}).get("max_attempts", 1))
        if not 0 <= max_repairs <= 3:
            raise ValueError("max repair attempts must be between 0 and 3")
        reserved = {"id", "title", "version", "schema_version", "sections", "evaluation", "repair"}
        return KnowledgeProfile(
            id=profile_id,
            title=str(value.get("title") or profile_id),
            version=str(value.get("version") or "1"),
            schema_version=str(value.get("schema_version") or "1"),
            sections=_definitions(value.get("sections") or [], SectionDefinition),
            evaluation_checks=_definitions(checks, CheckDefinition),
            max_repairs=max_repairs,
            prompt_dir=path.parent,
            metadata={key: item for key, item in value.items() if key not in reserved},
        )
