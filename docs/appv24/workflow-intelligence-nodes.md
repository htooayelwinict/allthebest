# appv24 Workflow Intelligence Nodes

Workflow intelligence nodes are generic platform primitives. They are not finance-specific and they are not agent loops.

## Built-In Actions

```text
source.fetch_items
source.fetch_document
llm.extract_structured
llm.classify
llm.summarize
policy.check
notification.create_draft
```

These actions must be declared in `connectors.yaml`, allowed in `permissions.yaml`, and referenced exactly from `workflow.yaml` steps.

## LLM Nodes

LLM nodes are typed transforms:

```yaml
uses: llm.extract_structured
with:
  task: extract_financial_catalysts
  input_ref: fetch_news.items
  output_contract: event_alerts_v1
side_effect_class: read
```

Rules:

- Use exactly one of `with.output_contract` or `with.output_schema`.
- Prefer compact `output_contract` names to avoid repeating large schemas in prompts and workflow packages.
- Built-in contracts include `event_alerts_v1`, `classification_v1`, `items_v1`, and `summary_v1`.
- LLM nodes must be read-only typed transforms.
- LLM nodes must not send, create, update, delete, publish, provision, route dynamically, call tools, or require approvals.

## Policy Gates

`policy.check` is deterministic:

```yaml
uses: policy.check
with:
  checks:
    require_source_urls: true
    require_source_timestamps: true
    disallow_financial_advice: true
    min_confidence: medium
side_effect_class: read
```

Do not rely on LLM judgment as the policy gate.

## Financial Catalyst Alerts

Financial catalyst alerts are an example package, not runtime logic.

Allowed:

- monitor public sources
- extract structured signals
- require source links and timestamps
- draft an informational alert

Not allowed:

- buy/sell/hold recommendations
- price targets
- personalized financial advice
- trade placement
- finance-specific executable actions such as `finance.find_catalysts`

See `examples/appv24/workflows/financial-catalyst-alerts/`.
