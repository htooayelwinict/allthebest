# appv24 Workflow Activation v0

Workflow Activation v0 turns a validated workflow package into a managed local activation.

This is not a live automation daemon yet. It is a control layer around the existing safe local runner:

- persist activation metadata
- remember trigger intent
- run the package on demand
- write runs under the activation directory
- track last run status and run history

## Boundary

Enabled in v0:

- manual activations
- persisted activation records
- local fixture-backed workflow runs
- activation status
- activation run history

Recorded but not executed as services in v0:

- schedule trigger intent
- webhook trigger intent

Still disabled:

- background scheduler loops
- webhook ingress servers
- live external mutation
- real send/deploy/cloud side effects

## Storage

`WorkflowActivationStore(root)` stores each activation under:

```text
<root>/<activation-id>/
  activation.json
  runs.jsonl
  runs/<run-id>/
    run.json
    events.jsonl
    trace.json
    approvals.json
    report.json
    artifacts/
```

Run artifacts are delegated to the existing workflow runner. Activation v0 does not create a second execution engine.

Activation runs stamp their provenance into `run.json`:

```text
activation_id
activation_trigger_type
mode
fixture_name
package_path
approval_mode
connector_allowlist
```

This makes later explanations and live-read gates auditable from the persisted run directory without trusting in-memory state.

`/workflow-activation-run-explain` includes this provenance so an operator can see which activation and trigger produced a saved run.

## API Shape

```python
from pathlib import Path

from appv24.workflows import WorkflowActivationStore
from appv24.workflows.execution import WorkflowRunMode

store = WorkflowActivationStore(Path(".appv24/workflow-activations"))
activation = store.create_activation(
    Path("saved-catalyst-alert"),
    trigger_type="manual",
    fixture_name="happy_path.yaml",
    mode=WorkflowRunMode.LOCAL,
    approve_all=True,
)

report = store.run_activation(activation.activation_id)
status = store.status(activation.activation_id)
history = store.history(activation.activation_id)
```

## TUI And Plain CLI Commands

The workflow-designer profile exposes activation commands for local managed runs in the TUI and in `--plain` mode:

```text
/workflow-activate <path> [fixture]
/workflow-activation-disable <activation-id>
/workflow-activation-enable <activation-id>
/workflow-activation-list
/workflow-activation-run <activation-id>
/workflow-activation-run-explain <activation-id> [run-id]
/workflow-activation-status <activation-id>
/workflow-activation-history <activation-id>
```

Activation records are stored under the current working directory:

```text
.appv24/workflow-activations/<activation-id>/
```

Activation IDs are treated as opaque IDs, not paths. TUI commands reject path-like IDs.

## Policy

Activation creation validates the workflow package before persisting the record.

Activation v0 only enables `WorkflowRunMode.LOCAL`. Other modes remain blocked at this layer until broker, approval, credential, and operator controls are stronger.

Activation records pin the package fingerprint at creation time. `run_activation()` refuses to run if the package files changed after activation. To run changed package content, create a new activation so the operator deliberately accepts the new fingerprint.

`/workflow-activation-status` prints `package: current` or `package: changed`. `/workflow-activation-list` includes the same state as `package=current` or `package=changed`.
