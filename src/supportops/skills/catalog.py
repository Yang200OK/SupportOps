"""固定发布目录；摘要先读，正文与来源只在匹配后读取并核对。"""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from supportops.api.errors import ServiceError
from supportops.lab.contracts import Strict, Version
from supportops.settings import ROOT
from supportops.skills.release import CATALOG_SHA256

Identity = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z-]{2,47}$")]
Release = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]
ShortText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
Mode = Literal["online", "startup"]
MAX_CONTEXT_CHARS = 6000


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Source(Strict):
    path: Annotated[
        str,
        StringConstraints(
            strict=True,
            pattern=r"^data/relaydesk/(1\.0|1\.1|2\.0)/(configuration|troubleshooting)\.md$",
        ),
    ]
    product_version: Version
    sha256: Digest
    source_type: Literal["self_authored_design"]
    license: Literal["CC0-1.0"]

    @model_validator(mode="after")
    def same_version(self):
        if self.path.split("/")[2] != self.product_version:
            raise ValueError("来源路径与适用版本不符。")
        return self


class Entry(Strict):
    skill_id: Identity
    version: Release
    title: ShortText
    summary: ShortText
    product_versions: list[Version] = Field(min_length=1, max_length=3)
    modes: list[Mode] = Field(min_length=1, max_length=2)
    signals: list[ShortText] = Field(min_length=1, max_length=8)
    body_sha256: Digest
    sources: list[Source] = Field(min_length=1, max_length=6)
    human_semantic_reviewed: Literal[False]

    @model_validator(mode="after")
    def unique_conditions_and_sources(self):
        if any(
            len(values) != len(set(values))
            for values in (self.product_versions, self.modes, self.signals)
        ):
            raise ValueError("适用条件不能重复。")
        paths = [s.path for s in self.sources]
        if len(paths) != len(set(paths)) or set(self.product_versions) != {
            s.product_version for s in self.sources
        }:
            raise ValueError("来源必须覆盖且仅覆盖声明的版本。")
        return self


class Manifest(Strict):
    schema_version: Literal["skill-catalog.v1"]
    items: list[Entry] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_releases(self):
        identities = [(s.skill_id, s.version) for s in self.items]
        # 本轮每个方法只发布一个版本；禁止隐式选择最新版。
        if len(identities) != len(set(identities)) or len({s.skill_id for s in self.items}) != len(
            self.items
        ):
            raise ValueError("方法或发布版本重复。")
        return self


def scope_valid(version, mode):
    if version not in ("1.0", "1.1", "2.0") or mode not in ("online", "startup"):
        raise ServiceError(422, "SKILL_SCOPE_INVALID", "Skill 需要明确的版本与运行模式。")


class Catalog:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        raw = self._read("skills/catalog.json", 40000, "SKILL_CATALOG_INVALID", "目录")
        if sha256(raw) != CATALOG_SHA256:
            raise ServiceError(409, "SKILL_CATALOG_INVALID", "Skill 目录与发布摘要不一致。")
        try:
            self.manifest = Manifest.model_validate_json(raw)
        except (ValidationError, ValueError):
            raise ServiceError(409, "SKILL_CATALOG_INVALID", "Skill 目录格式无效。") from None

    def _read(self, relative, limit, code, label):
        try:
            target = (self.root / relative).resolve(strict=True)
            if not target.is_relative_to(self.root) or not target.is_file():
                raise ValueError("越界路径")
            # 限定字节读取，避免大文件先进入内存后才拒绝。
            with target.open("rb") as source:
                raw = source.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("字节超限")
            raw.decode("utf-8", errors="strict")
            return raw
        except (OSError, ValueError):
            raise ServiceError(
                409, code, f"Skill {label}缺失、路径越界、超限或编码无效。"
            ) from None

    def listing(self):
        return {
            "schema_version": self.manifest.schema_version,
            "catalog_sha256": CATALOG_SHA256,
            "items": [
                {**s.model_dump(mode="json", exclude={"sources"}), "source_count": len(s.sources)}
                for s in self.manifest.items
            ],
            "source_type": "project_release",
            "effectiveness_evaluated": False,
        }

    def detail(self, identity, release, version, mode):
        scope_valid(version, mode)
        row = next(
            (s for s in self.manifest.items if s.skill_id == identity and s.version == release),
            None,
        )
        if row is None:
            raise ServiceError(404, "SKILL_NOT_FOUND", "Skill 发布版本不存在。")
        if version not in row.product_versions or mode not in row.modes:
            raise ServiceError(422, "SKILL_NOT_APPLICABLE", "Skill 不适用本次版本或运行模式。")
        raw = self._read(
            f"skills/{row.skill_id}/{row.version}/SKILL.md", 8192, "SKILL_BODY_INVALID", "正文"
        )
        if sha256(raw) != row.body_sha256:
            raise ServiceError(409, "SKILL_BODY_INVALID", "Skill 正文摘要不一致。")
        sources = [s for s in row.sources if s.product_version == version]
        for source in sources:
            original = self._read(source.path, 65536, "SKILL_SOURCE_INVALID", "来源")
            if sha256(original) != source.sha256:
                raise ServiceError(409, "SKILL_SOURCE_INVALID", "Skill 来源摘要不一致。")
        return {
            **row.model_dump(mode="json"),
            "sources": [s.model_dump(mode="json") for s in sources],
            "body": raw.decode("utf-8"),
            "body_verified": True,
            "sources_verified": True,
            "selected_product_version": version,
            "selected_mode": mode,
            "catalog_sha256": CATALOG_SHA256,
            "role": "planning_guidance_only",
        }

    def prepare(self, version, mode, symptoms):
        scope_valid(version, mode)
        normalized = symptoms.casefold()
        selected = []
        for row in self.manifest.items:
            if version not in row.product_versions or mode not in row.modes:
                continue
            signals = [word for word in row.signals if word.casefold() in normalized]
            if signals:
                selected.append((row, signals))
        # 信号命中数优先，身份打破同分，固定目录顺序不能改变选择结果。
        selected.sort(key=lambda item: (-len(item[1]), item[0].skill_id))
        bundle = {
            "selection_version": "symptom-match.v1",
            "catalog": self.listing(),
            "loaded": [
                {
                    **self.detail(row.skill_id, row.version, version, mode),
                    "matched_signals": signals,
                }
                for row, signals in selected[:2]
            ],
            "selection_limited": len(selected) > 2,
            "selection_reason": "matched" if selected else "no_matching_skill",
            "max_loaded": 2,
            "role": "planning_guidance_only",
        }
        if len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))) > MAX_CONTEXT_CHARS:
            raise ServiceError(409, "SKILL_CONTEXT_LIMIT", "Skill 目录与正文超过加载预算。")
        return bundle
