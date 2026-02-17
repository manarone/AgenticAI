from __future__ import annotations

import logging
import re
from pathlib import Path

import frontmatter
import yaml
from minio import Minio
from minio.error import S3Error

from libs.common.config import get_settings
from libs.common.schemas import SkillManifest

logger = logging.getLogger(__name__)
_SKILL_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


class SkillStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_dir = Path(self.settings.skill_local_dir)
        self.minio = Minio(
            self.settings.minio_endpoint,
            access_key=self.settings.minio_access_key,
            secret_key=self.settings.minio_secret_key,
            secure=self.settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        try:
            if not self.minio.bucket_exists(self.settings.minio_bucket):
                self.minio.make_bucket(self.settings.minio_bucket)
        except S3Error:
            # MinIO may not be reachable in local/dev; local fallback still works.
            logger.exception('Failed to ensure MinIO bucket %s', self.settings.minio_bucket)

    def load_local_skill(self, skill_name: str) -> tuple[SkillManifest, str]:
        normalized = skill_name.strip()
        if not normalized or '..' in normalized or not _SKILL_NAME_RE.fullmatch(normalized):
            raise ValueError('Invalid skill name')

        path = (self.local_dir / f'{normalized}.md').resolve()
        root = self.local_dir.resolve()
        if root not in path.parents:
            raise ValueError('Skill path escapes local skill directory')
        if not path.exists():
            raise FileNotFoundError(f'Skill {skill_name} not found at {path}')

        post = frontmatter.loads(path.read_text())
        manifest = SkillManifest.model_validate(post.metadata)
        return manifest, str(post.content)

    def load_skill(self, tenant_id: str, skill_name: str) -> tuple[SkillManifest, str]:
        # MVP behavior: local-first during development. MinIO path available for production.
        return self.load_local_skill(skill_name)

    def parse_skill_markdown(self, text: str) -> tuple[SkillManifest, str]:
        post = frontmatter.loads(text)
        manifest = SkillManifest.model_validate(post.metadata)
        return manifest, str(post.content)

    @staticmethod
    def validate_skill_text(text: str) -> SkillManifest:
        post = frontmatter.loads(text)
        return SkillManifest.model_validate(post.metadata)

    @staticmethod
    def dump_manifest(manifest: SkillManifest) -> str:
        return yaml.safe_dump(manifest.model_dump(mode='json'), sort_keys=False)
