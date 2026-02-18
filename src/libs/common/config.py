from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_env: str = Field(default='dev', alias='APP_ENV')

    database_url: str = Field(default='sqlite+aiosqlite:///./agentai.db', alias='DATABASE_URL')
    redis_url: str = Field(default='redis://localhost:6379/0', alias='REDIS_URL')

    task_stream: str = Field(default='agentai:tasks', alias='TASK_STREAM')
    result_stream: str = Field(default='agentai:results', alias='RESULT_STREAM')
    cancel_stream: str = Field(default='agentai:cancels', alias='CANCEL_STREAM')

    telegram_bot_token: str = Field(default='', alias='TELEGRAM_BOT_TOKEN')

    openai_base_url: str = Field(default='https://openrouter.ai/api/v1', alias='OPENAI_BASE_URL')
    openai_api_key: str = Field(default='', alias='OPENAI_API_KEY')
    openai_model: str = Field(default='minimax/minimax-m2.5', alias='OPENAI_MODEL')
    openai_fallback_model: str = Field(default='', alias='OPENAI_FALLBACK_MODEL')

    admin_token: str = Field(default='change-me', alias='ADMIN_TOKEN')

    minio_endpoint: str = Field(default='localhost:9000', alias='MINIO_ENDPOINT')
    minio_access_key: str = Field(default='minioadmin', alias='MINIO_ACCESS_KEY')
    minio_secret_key: str = Field(default='minioadmin', alias='MINIO_SECRET_KEY')
    minio_secure: bool = Field(default=False, alias='MINIO_SECURE')
    minio_bucket: str = Field(default='agentai-skills', alias='MINIO_BUCKET')
    skill_local_dir: str = Field(default='src/skills/starter', alias='SKILL_LOCAL_DIR')

    bus_backend: str = Field(default='redis', alias='BUS_BACKEND')
    memory_backend: str = Field(default='local', alias='MEMORY_BACKEND')
    mem0_api_key: str = Field(default='', alias='MEM0_API_KEY')
    mem0_org_id: str = Field(default='', alias='MEM0_ORG_ID')
    mem0_project_id: str = Field(default='', alias='MEM0_PROJECT_ID')
    mem0_user_prefix: str = Field(default='agentai', alias='MEM0_USER_PREFIX')
    mem0_qdrant_host: str = Field(default='localhost', alias='MEM0_QDRANT_HOST')
    mem0_qdrant_port: int = Field(default=6333, alias='MEM0_QDRANT_PORT')
    mem0_qdrant_collection: str = Field(default='agentai-mem0-nomic', alias='MEM0_QDRANT_COLLECTION')
    mem0_embedder_provider: str = Field(default='fastembed', alias='MEM0_EMBEDDER_PROVIDER')
    mem0_embedding_model: str = Field(default='nomic-ai/nomic-embed-text-v1.5', alias='MEM0_EMBEDDING_MODEL')
    mem0_embedding_dims: int = Field(default=768, alias='MEM0_EMBEDDING_DIMS')
    mem0_llm_provider: str = Field(default='lmstudio', alias='MEM0_LLM_PROVIDER')
    mem0_llm_model: str = Field(default='minimax/minimax-m2.5', alias='MEM0_LLM_MODEL')
    mem0_llm_base_url: str = Field(default='http://localhost:1234/v1', alias='MEM0_LLM_BASE_URL')
    mem0_history_db_path: str = Field(default='./data/mem0-history.db', alias='MEM0_HISTORY_DB_PATH')

    k8s_namespace: str = Field(default='agentai', alias='K8S_NAMESPACE')
    k8s_executor_image: str = Field(default='agentai-executor:v0.1.0', alias='K8S_EXECUTOR_IMAGE')
    launch_executor_job: bool = Field(default=False, alias='LAUNCH_EXECUTOR_JOB')

    max_executor_retries: int = Field(default=2, alias='MAX_EXECUTOR_RETRIES')
    task_timeout_seconds: int = Field(default=120, alias='TASK_TIMEOUT_SECONDS')
    shell_policy_mode: str = Field(default='balanced', alias='SHELL_POLICY_MODE')
    shell_work_dir: str = Field(default='/tmp/agentai', alias='SHELL_WORK_DIR')
    shell_max_output_chars: int = Field(default=4000, alias='SHELL_MAX_OUTPUT_CHARS')
    shell_allow_hard_block_override: bool = Field(default=False, alias='SHELL_ALLOW_HARD_BLOCK_OVERRIDE')
    shell_mutation_grant_ttl_minutes: int = Field(default=10, alias='SHELL_MUTATION_GRANT_TTL_MINUTES')
    shell_remote_enabled: bool = Field(default=False, alias='SHELL_REMOTE_ENABLED')
    shell_timeout_seconds: int = Field(default=120, alias='SHELL_TIMEOUT_SECONDS')
    shell_env_allowlist: str = Field(default='PATH,HOME,LANG,LC_ALL,TERM,TZ', alias='SHELL_ENV_ALLOWLIST')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
