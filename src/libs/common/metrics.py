from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

REQUEST_COUNTER = Counter('agentai_requests_total', 'Total HTTP requests', ['service', 'endpoint'])
TASK_COUNTER = Counter('agentai_tasks_total', 'Tasks processed', ['status'])
TOKEN_COUNTER = Counter('agentai_tokens_total', 'Estimated tokens', ['tenant_id', 'model'])
REQUEST_LATENCY = Histogram('agentai_request_latency_seconds', 'Request latency', ['service', 'endpoint'])
SHELL_POLICY_ALLOW_COUNTER = Counter('shell_policy_allow_total', 'Shell commands auto-approved')
SHELL_POLICY_APPROVAL_COUNTER = Counter('shell_policy_approval_total', 'Shell commands requiring approval')
SHELL_POLICY_BLOCK_COUNTER = Counter('shell_policy_block_total', 'Shell commands blocked by policy')
SHELL_DENIED_NO_GRANT_COUNTER = Counter(
    'shell_execution_denied_no_grant_total',
    'Shell command executions denied because no approval grant exists',
)
WEB_SEARCH_REQUEST_COUNTER = Counter(
    'web_search_requests_total',
    'Web search requests',
    ['status', 'depth'],
)
WEB_SEARCH_LATENCY = Histogram(
    'web_search_latency_seconds',
    'Web search latency',
    ['depth'],
)
WEB_SEARCH_RESULTS_COUNT = Histogram(
    'web_search_results_count',
    'Web search results count',
    ['depth'],
)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
