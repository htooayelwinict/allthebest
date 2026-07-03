# appv24 Built-In Profiles

appv24 profiles are passive startup configuration. A profile may select tools,
append system prompt text, set iteration limits, and set guardrail defaults.
Profiles do not execute code and do not change agent loop, provider streaming,
compaction, session store, or tool schemas.

## Built-In Only

The first profile implementation only supports profiles compiled into appv24.
It does not load profile files from the user home directory, project directory,
packages, or environment variables.

## Profiles

| ID | Purpose | Tools |
| --- | --- | --- |
| `coding` | Default appv24 coding agent behavior | Current default |
| `read-only` | Inspect and explain without mutation | `read`, `grep`, `find`, `ls` |
| `planning` | Read-only implementation planning | `read`, `grep`, `find`, `ls` |
| `research-lite` | Local evidence research | `read`, `grep`, `find`, `ls` |
| `sre-lite` | Read-only operational analysis | `read`, `grep`, `find`, `ls` |
| `security-lab-lite` | Defensive local code/config review | `read`, `grep`, `find`, `ls` |
| `workflow-designer-lite` | Passive workflow-as-code design from natural language | `read`, `grep`, `find`, `ls` |

## CLI

```bash
appv24 --list-profiles
appv24 --profile coding --cwd .
appv24 --profile read-only --cwd .
appv24 --profile planning --cwd .
appv24 --profile research-lite --cwd .
appv24 --profile sre-lite --cwd .
appv24 --profile security-lab-lite --cwd .
appv24 --profile workflow-designer-lite --cwd .
```

## Workflow Designer Lite

`workflow-designer-lite` is a passive compiler-style profile. It designs
workflow packages in the final response, but does not write package files or
execute workflows.

Expected output may include:

```text
workflow summary
assumptions
workflow.yaml draft
connectors.yaml draft
permissions.yaml draft
approval gates
dry-run fixtures
test plan
rollback plan
risk review
future runner target
```

The profile may discuss future adapter targets such as MCP, direct APIs,
OpenAPI, LangGraph, n8n/Pipedream export, and IaC. These are design targets
only in v1.

It must not:

```text
call external APIs
request raw secrets
send messages
publish
provision cloud resources
run workflow steps
create workflow package files
```

## Override Rules

CLI flags override profile defaults for model, provider, thinking level, maximum
iterations, and tool-loop hard-stop behavior.

## Boundary

Profiles are not extensions. Extensions may eventually contain active hooks,
commands, UI integration, provider payload hooks, or dynamic tools. Profiles
must remain declarative startup configuration.
