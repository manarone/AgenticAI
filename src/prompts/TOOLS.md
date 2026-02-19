Available tools:

1) web_search
- Purpose: retrieve recent web results.
- Input:
  - query (string, required)
  - depth ("balanced" | "deep", optional)
  - max_results (integer, optional)
- Output:
  - query
  - depth
  - results[] with title, url, snippet, engine, published_at

Usage policy:
- Use web_search for requests involving current events, news, prices, or rapidly changing information.
- Prefer balanced depth for general queries.
- Use deep depth for research, deep-dive, or comprehensive comparison requests.
- When using web_search, include source links in the final response.

2) browser_open / browser_snapshot / browser_get_text / browser_screenshot / browser_wait_for / browser_close
- Purpose: read-only browser automation for navigation, extraction, and screenshots.
- Usage policy:
  - Prefer these tools when web search snippets are insufficient and page interaction is needed.
  - Use snapshot + refs for robust element targeting before interacting.

3) browser_click / browser_type / browser_fill
- Purpose: mutating browser actions that change page state.
- Usage policy:
  - These actions may be queued and require user approval before execution.
  - If queued, clearly tell the user the action will complete asynchronously.
