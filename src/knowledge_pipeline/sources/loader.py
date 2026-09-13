from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from knowledge_pipeline.core.contracts import SourceBundle, SourceRecord, now_iso, sha256_bytes, sha256_text


@dataclass(frozen=True)
class SourcePolicy:
    allowed_roots: tuple[Path, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    max_bytes: int = 2 * 1024 * 1024
    max_chars: int = 100_000
    timeout_seconds: int = 30
    verify_tls: bool = True
    max_redirects: int = 3


def _normalize_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    target = path.resolve()
    return any(target == root.resolve() or root.resolve() in target.parents for root in roots)


def load_local_sources(paths: Iterable[Path], policy: SourcePolicy) -> tuple[SourceRecord, ...]:
    roots = policy.allowed_roots
    if not roots:
        raise ValueError("local source policy requires at least one allowed root")
    result: list[SourceRecord] = []
    for index, raw_path in enumerate(paths, 1):
        path = Path(raw_path).expanduser().resolve()
        if not _inside(path, roots):
            raise ValueError(f"source path is outside allowed roots: {path}")
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            raise ValueError(f"only local Markdown and text files are supported: {path}")
        payload = path.read_bytes()
        if len(payload) > policy.max_bytes:
            raise ValueError(f"source file exceeds {policy.max_bytes} bytes: {path}")
        try:
            raw_text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"source file must be UTF-8: {path}") from exc
        normalized = _normalize_text(raw_text)
        truncated = len(normalized) > policy.max_chars
        normalized = normalized[: policy.max_chars]
        result.append(SourceRecord(
            source_id=f"SRC{index:03d}", title=path.stem, kind="local_file",
            content=normalized, locator=str(path), raw_sha256=sha256_bytes(payload),
            normalized_sha256=sha256_text(normalized), collected_at=now_iso(),
            truncated=truncated, metadata={"extension": path.suffix.lower()},
        ))
    return tuple(result)


def _validate_public_url(url: str, policy: SourcePolicy) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ValueError("web sources require an https URL")
    allowed = {item.lower() for item in policy.allowed_hosts}
    if not allowed or host not in allowed:
        raise ValueError(f"web source host is not allowlisted: {host}")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve web source host: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"web source resolved to a non-public address: {host}")
    return host


def fetch_web_source(url: str, policy: SourcePolicy, source_number: int = 1) -> SourceRecord:
    current = str(url)
    response: requests.Response | None = None
    for _ in range(policy.max_redirects + 1):
        _validate_public_url(current, policy)
        response = requests.get(
            current, timeout=policy.timeout_seconds, verify=policy.verify_tls,
            allow_redirects=False, headers={"User-Agent": "KnowledgePipeline/0.1"}, stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                raise ValueError("web source redirect has no Location")
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        break
    else:
        raise ValueError("web source exceeded redirect limit")
    assert response is not None
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type not in {"text/html", "text/plain", "text/markdown"}:
        raise ValueError(f"unsupported web source content type: {content_type}")
    payload = response.raw.read(policy.max_bytes + 1, decode_content=True)
    if len(payload) > policy.max_bytes:
        raise ValueError("web source exceeds configured byte limit")
    response.encoding = response.encoding or "utf-8"
    raw_text = payload.decode(response.encoding, errors="replace")
    title = current
    if content_type == "text/html":
        soup = BeautifulSoup(raw_text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else current
        raw_text = soup.get_text("\n")
    normalized = _normalize_text(raw_text)
    truncated = len(normalized) > policy.max_chars
    normalized = normalized[: policy.max_chars]
    return SourceRecord(
        source_id=f"SRC{source_number:03d}", title=title, kind="web",
        content=normalized, locator=current, raw_sha256=sha256_bytes(payload),
        normalized_sha256=sha256_text(normalized), collected_at=now_iso(),
        truncated=truncated, metadata={"content_type": content_type},
    )


def build_source_bundle(document_id: str, profile_id: str, profile_version: str, sources: Iterable[SourceRecord]) -> SourceBundle:
    rows = tuple(sources)
    return SourceBundle(
        bundle_id=f"bundle-{document_id}", document_id=document_id,
        profile_id=profile_id, profile_version=profile_version,
        sources=rows, built_at=now_iso(),
    )
