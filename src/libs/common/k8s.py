from __future__ import annotations

import hashlib
import re

from kubernetes import client, config

from libs.common.config import get_settings

_LABEL_SAFE_RE = re.compile(r'[^a-z0-9_.-]')


class ExecutorJobLauncher:
    def __init__(self) -> None:
        self.settings = get_settings()

    def load(self) -> None:
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()

    def build_job(self, task_id: str) -> client.V1Job:
        task_label = self._sanitize_label(task_id)
        job_name = self._build_job_name(task_id)
        container = client.V1Container(
            name='executor',
            image=self.settings.k8s_executor_image,
            image_pull_policy='IfNotPresent',
            env=[client.V1EnvVar(name='EXECUTOR_ONCE_TASK_ID', value=task_id)],
            resources=client.V1ResourceRequirements(
                requests={'cpu': '250m', 'memory': '256Mi'},
                limits={'cpu': '1', 'memory': '1Gi'},
            ),
        )
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={'app': 'executor', 'task_id': task_label}),
            spec=client.V1PodSpec(restart_policy='Never', containers=[container]),
        )
        spec = client.V1JobSpec(template=template, backoff_limit=0, ttl_seconds_after_finished=300)
        return client.V1Job(
            api_version='batch/v1',
            kind='Job',
            metadata=client.V1ObjectMeta(name=job_name, namespace=self.settings.k8s_namespace),
            spec=spec,
        )

    def create_job(self, task_id: str) -> None:
        self.load()
        api = client.BatchV1Api()
        api.create_namespaced_job(namespace=self.settings.k8s_namespace, body=self.build_job(task_id))

    @staticmethod
    def _sanitize_label(task_id: str) -> str:
        lowered = task_id.lower()
        cleaned = _LABEL_SAFE_RE.sub('-', lowered).strip('-.')
        return (cleaned or 'unknown')[:63]

    @staticmethod
    def _build_job_name(task_id: str) -> str:
        digest = hashlib.sha1(task_id.encode('utf-8')).hexdigest()[:8]
        prefix = re.sub(r'[^a-z0-9-]', '-', ExecutorJobLauncher._sanitize_label(task_id).replace('_', '-'))[:45]
        return f'executor-{prefix}-{digest}'[:63].rstrip('-')
