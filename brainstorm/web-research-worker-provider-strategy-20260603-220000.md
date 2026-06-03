# Web Research Worker Provider Strategy

## Problem

The `web_research_worker` is now correctly owned by the worker kernel when tools fail, but the live QA run still failed because the default DuckDuckGo HTML fallback returned no parseable results. That means the graph/runtime responsibility boundary is better, but the web tool itself is not production-grade enough for real research tasks.

## Constraints

- Planner should keep assigning `web_research_worker` when the user asks for external/current/cited research.
- Tool/provider failures, rate limits, timeouts, and worker budget exhaustion should remain kernel-owned outcomes.
- Planner replan should be reserved for planner-level failures such as wrong scope, missing artifacts, wrong worker assignment, or logically invalid task decomposition.
- Workers must not get raw shell/browser access. They should call named JSON tools only.
- Web research output must preserve provenance and distinguish sourced facts from inference.

## Options

1. Keep DuckDuckGo HTML as the main provider.

Low effort, no API key, easy for local smoke tests. Bad fit for production because parsing public HTML is brittle, result markup changes, blocking/captcha behavior is unpredictable, and snippets are not reliable enough for citation-heavy worker outputs.

2. Add one official search provider adapter.

Good first production step. Brave Search API has an official web search endpoint and is positioned for agents/chatbots. Tavily has official search and extraction endpoints, including options to return cleaned content. Either would make `web_search` return structured data instead of parsed HTML. This keeps the worker runtime simple while making live QA realistic.

3. Add provider interface with multiple adapters.

Best long-term shape. Define `SearchProvider.search(query, max_results)` and `ContentExtractor.fetch(url)` behind the toolbox. Implement `brave`, `tavily`, `duckduckgo_html`, and `disabled`. This lets the kernel classify provider failures consistently, switch providers through env, and run contract tests with fake providers.

4. Split `web_search` and `web_extract` more strictly.

The current group already has source discovery, extraction, and citation formatting templates. Make the tools match that: `web_search` only returns candidate URLs/snippets; `web_fetch` or provider extraction returns cleaned source bodies; citation synthesis only consumes extracted artifacts. This prevents the discovery worker from pretending snippets are citations.

5. Add kernel fallback provider rotation.

If `brave` fails with timeout/rate limit/no results, the kernel can retry the same worker instance group with another provider before failing. This should be kernel-owned retry/replacement, not planner replan. It needs telemetry for provider, query, status code, result count, and elapsed time.

## Recommended Path

Use option 3, but implement it in two phases.

Phase 1: introduce a provider interface and one real provider adapter, preferably Tavily if we want search plus extraction in one product, or Brave if we want a clean independent search index and keep extraction separate. Keep DuckDuckGo HTML as `dev_fallback`, not production default. Add env vars like `WORKER_WEB_SEARCH_PROVIDER`, `WORKER_WEB_SEARCH_API_KEY`, `WORKER_WEB_SEARCH_MAX_RESULTS`, and `WORKER_WEB_SEARCH_TIMEOUT_SECONDS`.

Phase 2: add provider rotation and health-aware kernel behavior. Tool failures become `kernel_failure` or `instance_failure`; the kernel retries/replaces worker instances or provider adapters until budget is exhausted. Only if successful search results prove the planner asked for impossible/wrong evidence should the worker emit `needs_replan`.

## Failure Classification

- Provider disabled, missing API key, timeout, rate limit, parse failure: kernel/tool issue.
- Model asks for a tool not allowed by the instance: instance failure.
- Worker exhausts model/tool budget: kernel budget issue.
- Search succeeds but required domain/evidence does not exist: plan failure only if the planner required unavailable evidence as a hard dependency.
- Planner selected `research_worker` when external current sources are required: planner issue.
- Planner omitted required input artifacts or gave wrong artifact names: planner issue.

## Risks And Mitigations

- Search provider costs can climb during multi-instance fanout. Mitigate with kernel budget counters per provider call and per-query result limits.
- Web results can be low quality. Mitigate with source scoring artifacts, domain allow/deny hints, and verify-worker citation checks.
- Provider APIs can fail or drift. Mitigate with adapter contract tests and a disabled/dev fallback mode.
- Workers may over-cite snippets. Mitigate by requiring extraction artifacts before citation synthesis when the output asks for cited claims.

## Next Steps

1. Add `WorkerSearchProvider` and provider-specific adapters.
2. Add official provider env config and `.env.example` keys.
3. Make `web_search` return normalized `SearchResult` artifacts.
4. Make `web_fetch` return normalized `ExtractedSource` artifacts.
5. Add provider fake tests, disabled-provider tests, timeout/rate-limit tests, and no-results tests.
6. Rerun the same live prompt and inspect matrix rows for provider, query, result count, retry count, and final worker status.

## Notes

- Brave official docs describe a web search API for agent/chatbot use and show a `/res/v1/web/search` endpoint.
- Tavily official docs expose search and extraction-oriented APIs, including options for cleaned/parsed result content.
