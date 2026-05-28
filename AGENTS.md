<!-- vibekit:pack=core-vibe-coder -->
# Vibe Coding Setup

This project uses a vibekit-managed assistant setup for planning, debugging, and review.

Core workflow:

1. Understand the request and restate the goal.
2. Investigate the repository before proposing or changing anything.
3. Persist planning and research artifacts inside the repository when they will guide later work.
4. Route the task to the best matching specialist when it improves quality.
5. Keep changes small, reversible, and verified.
6. Update documentation when behavior, setup, or workflows change.

Specialists:

- `planner` for multi-file work, risky changes, migrations, or unclear implementation paths.
- `fullstack-developer` for implementing planned work phase by phase.
- `project-manager` for coordination, task breakdown, and documentation synchronization.
- `researcher` for external or cross-source investigation when supported context is available.
- `brainstormer` for trade-off exploration and decision support without implementation.
- `debugger` for bugs, regressions, failing tests, crashes, and root-cause analysis.
- `reviewer` for code review, pre-merge checks, regression scanning, and security-minded quality review.
- `testing-expert` for TDD, coverage improvements, and browser or end-to-end validation.
- `security-expert` for OWASP-minded audits, secrets review, and auth or permission checks.
- `database-admin` for schema changes, relationship design, indexes, and migration safety.
- `ui-ux-designer` for accessible UI work, responsive polish, and design-system consistency.
- `devops-engineer` for Docker, CI/CD, deployment, and infrastructure changes.

Operating rules:

- Research existing patterns before editing.
- Prefer the smallest viable change set over new abstractions.
- Fix root causes instead of symptoms.
- Identify concrete file paths whenever possible.
- When a task needs durable context, write it into repo files instead of leaving it only in chat.
- Run the narrowest relevant verification first, then broader checks if needed.
- Do not install or use MCP servers unless the user has explicitly enabled them.
- If the user has enabled an MCP, load the matching `mcp-*` skill before relying on it.
- If `open-bridge` is enabled, use `mcp-open-bridge` for second-opinion reasoning, trade-off comparison, or synthesis across tricky files before making a high-risk call.
- Never write secrets into tracked project files.

Persistent artifacts:

- Planning work should create `plan/<topic>-<YYYYMMDD-HHMMSS>/`.
- Each plan folder should contain `README.md`, `plan.md`, `research/`, and `phases/`.
- Implementation work should read `plan.md` first and update phase status as work completes.
- Standalone research should create a dated folder under `plan/` or append to an existing plan's `research/`.
- Brainstorming should write a durable summary in `brainstorm/` or inside the active plan when it informs later implementation.

Expected outputs:

- Plans should include goal, existing patterns, files to change, phases, risks, and verification, and should be written to repo files.
- Implementation should reference the active plan, execute phases in order, and update progress artifacts.
- Bug fixes should include reproduction, root cause, minimal fix, regression coverage, and verification.
- Reviews should lead with findings ordered by severity, including file references and concrete remediation.
