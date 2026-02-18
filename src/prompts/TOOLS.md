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
