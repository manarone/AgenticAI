from __future__ import annotations

from kubernetes import client, config

from libs.common.config import get_settings


class ExecutorJobLauncher:
    def __init__(self) -> None:
        self.settings = get_settings()

    def load(self) -> None:
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()

    def build_job(self, task_id: str) -> client.V1Job:
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
            metadata=client.V1ObjectMeta(labels={'app': 'executor', 'task_id': task_id}),
            spec=client.V1PodSpec(restart_policy='Never', containers=[container]),
        )
        spec = client.V1JobSpec(template=template, backoff_limit=0, ttl_seconds_after_finished=300)
        return client.V1Job(
            api_version='batch/v1',
            kind='Job',
            metadata=client.V1ObjectMeta(name=f'executor-{task_id[:8]}', namespace=self.settings.k8s_namespace),
            spec=spec,
        )

    def create_job(self, task_id: str) -> None:
        self.load()
        api = client.BatchV1Api()
        api.create_namespaced_job(namespace=self.settings.k8s_namespace, body=self.build_job(task_id))
