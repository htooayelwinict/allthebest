# appv24 Later Chores

## 2026-07-06 Qwen workflow intelligence TUI test facts

- Worktree: `/Users/htooayelwin/.config/superpowers/worktrees/allthebest/next-appv24`
- Profile: `workflow-designer-lite`
- Provider/model: OpenRouter `qwen/qwen3-coder-next`
- Test style: actual interactive TUI background run, human-style prompt, saved package, then local fixture runs.
- Scenario: public company news and filings workflow with LLM catalyst extraction, financial-advice policy block, and Telegram/email alert draft with source links.
- Saved package: `/private/tmp/appv24-intelligence-node-qa-final/saved-catalyst-alert`
- Direct package validation: `ok=True`
- TUI save result: workflow draft saved and package validation passed.
- Happy-path local run: passed.
- Happy-path run id: `run-0ebf7f23ee6147d38057b1f8f0afc586`
- Happy-path terminal step: `save_artifact`
- Happy-path simulated brokered actions:
  - `fetch_news: source.fetch_items`
  - `extract_catalysts: llm.extract_structured`
  - `draft_alert: notification.create_draft`
- Rejection-path local run: passed.
- Rejection-path run id: `run-beccf71eaf29457c97b871385c089790`
- Rejection-path terminal step: `save_artifact`
- Rejection-path simulated brokered actions:
  - `fetch_news: source.fetch_items`
  - `extract_catalysts: llm.extract_structured`
- Rejection-path result: no notification draft action was brokered, so the policy path blocked before alert drafting.

## 2026-07-06 Mimo workflow TUI test facts

- Worktree: `/Users/htooayelwin/.config/superpowers/worktrees/allthebest/next-appv24`
- Profile: `workflow-designer-lite`
- Provider/model: OpenRouter `xiaomi/mimo-v2.5-pro`
- Test style: actual interactive TUI background run, no manual repair prompt injected.
- Scenario: support ticket triage workflow with LLM classification, deterministic policy checks, and local engineering escalation draft only.
- Saved package: `/private/tmp/appv24-tui-mimo-yMcLI3/support-ticket-triage`
- Direct package validation: `ok=True`
- Repair behavior: first draft failed validation, repair loop converged on attempt 3.
- TUI save result: workflow draft saved and package validation passed.
- Local dry-run result: passed.
- Run id: `run-09b476f5287d4bcb9f9f10f2eec326cf`
- Terminal step: `done_escalated`
- Simulated brokered actions:
  - `source.fetch_document`
  - `llm.classify`
  - `policy.check`
  - `notification.create_draft`
- Report summary: `status=passed`, `brokered_actions_count=4`, `approvals_required=0`.

Follow-up chore:

- Mimo is functionally acceptable, but verbose. Its repair attempts regenerate large package text instead of a tight minimal patch. Later, tighten workflow repair prompting/emission for lower token cost and cleaner convergence without changing red-zone/kernel code.
## Workflow Connector Sandbox v0

- Added fixture-backed connector sandbox behavior in local workflow runs.
- Added `/workflow-run-sandbox <package> [fixture]` as the user-facing TUI command.
- Added repeatable financial catalyst fixtures:
  - `happy_path.yaml`
  - `rejection_path.yaml`
  - `connector_timeout.yaml`
  - `bad_llm_output.yaml`
  - `missing_field.yaml`
- Added `unauthorized_user.yaml` to the golden Telegram support-ticket workflow.
- Added `ticket_draft_timeout.yaml` to the golden Telegram support-ticket workflow.
- Added `missing_recipient.yaml` to the golden Gmail draft-reply workflow.
- Added persisted `public-news-digest` example package with HTTP/news-style `source.fetch_document` fixtures:
  - `happy_path.yaml`
  - `rejection_path.yaml`
  - `source_timeout.yaml`
  - `bad_source_output.yaml`
- Added persisted `telegram-support-ticket` example package with Telegram/ticket fixtures:
  - `happy_path.yaml`
  - `rejection_path.yaml`
  - `unauthorized_user.yaml`
  - `ticket_draft_timeout.yaml`
- Added local Telegram message draft artifact provider for `telegram.create_message_draft`.
- Earlier note superseded: live production V0 now exists only for `telegram.send_message`, and remains disabled by default unless approval ledger, credential policy, allowlist, idempotency, transport, and production broker gates pass.
