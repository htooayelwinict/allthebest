"""CLI entrypoint for the pi+hermes-compliant appv24 stack."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

from appv24.ai.env_config import ModelConfig, get_default_model_for_provider, load_model_config
from appv24.ai.model_resolver import ScopedModel, resolve_cli_model, resolve_model_scope
from appv24.ai.models import get_model, get_models, get_providers, has_configured_auth, register_model, set_auth_credential
from appv24.ai.providers.capabilities import ProviderParamWarning, build_generation_payload
from appv24.ai.providers.catalog import determine_api_mode, normalize_provider
from appv24.ai.providers.model_catalog import get_live_openrouter_models
from appv24.ai.providers.params import GenerationParams, merge_generation_params, params_from_mapping
from appv24.ai.register_builtins import register_builtin_providers
from appv24.ai.types import Model
from appv24.app import CodingApp
from appv24.coding_agent.config import get_agent_dir, get_auth_path
from appv24.coding_agent.agent_session_services import _new_session_path
from appv24.coding_agent.export_html import export_from_file
from appv24.profiles import (
    DEFAULT_PROFILE_ID,
    ProfileError,
    get_profile,
    list_profiles,
    profile_coding_app_kwargs,
    validate_builtin_profiles,
)
from appv24.tui.interactive_mode import InteractiveMode
from appv24.workflows.live_validation import (
    assistant_text_from_messages,
    format_workflow_quality_report,
    format_workflow_validation_report,
    should_validate_workflow_drafts,
    workflow_quality_result_for_draft,
)
from appv24.workflows.live_env_template import build_workflow_live_env_template, format_workflow_live_env_template
from appv24.workflows.live_preflight import (
    format_live_draft_preflight_report,
    format_live_production_preflight_report,
    format_live_read_preflight_report,
    preflight_live_draft_workflow,
    preflight_live_production_workflow,
    preflight_live_read_workflow,
)
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_readiness import format_workflow_live_readiness_report, inspect_workflow_live_readiness
from appv24.workflows.live_runner import run_live_draft_workflow, run_live_production_workflow, run_live_read_workflow
from appv24.workflows.live_transports import (
    build_live_draft_transports_from_env,
    build_live_production_transports_from_connector_config,
    build_live_production_transports_from_env,
    build_live_read_transports_from_connector_config,
    build_live_read_transports_from_env,
)
from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.credential_refs import credential_resolver_for_package_connector_config
from appv24.workflows.quality_gate import check_workflow_quality_package
from appv24.workflows.repair import (
    WorkflowDraftRepairConfig,
    WorkflowDraftRepairResult,
    format_workflow_repair_stop_report,
    repair_workflow_draft_messages,
)
from appv24.workflows.activation import WorkflowActivationStore, format_workflow_activation_list
from appv24.workflows.approval_commands import (
    format_workflow_approval_command_result,
    format_workflow_approval_list,
    list_workflow_approval_requests,
    record_workflow_approval_command,
)
from appv24.workflows.drafts import WorkflowDraftSaveError, save_workflow_draft_blocks
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.bundle_export import (
    check_workflow_bundle,
    format_workflow_bundle_check,
    format_workflow_bundle_export,
    write_workflow_bundle,
)
from appv24.workflows.capability_catalog import build_workflow_capability_catalog, format_workflow_capability_catalog
from appv24.workflows.connector_registry import inspect_workflow_connector_registry
from appv24.workflows.connector_contracts import workflow_operator_env_from_env
from appv24.workflows.explain import (
    explain_workflow_activation_run,
    explain_workflow_package,
    explain_workflow_run,
    format_workflow_package_explanation,
    format_workflow_run_explanation,
)
from appv24.workflows.guided_run import (
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    prepare_guided_workflow_run,
)
from appv24.workflows.iac_export import export_deployment_intent_from_path, format_deployment_intent
from appv24.workflows.live_plan import build_workflow_live_plan_from_path, format_workflow_live_plan
from appv24.workflows.phase_runner import (
    format_workflow_phase_run_report,
    run_workflow_approved_live_draft,
    run_workflow_approved_live_production,
    run_workflow_live_read_to_approval,
)
from appv24.workflows.runner import format_workflow_run_report, run_workflow
from appv24.workflows.scheduler import (
    format_trigger_loop_report,
    format_trigger_sweep_report,
    run_activation_scheduler_loop,
    run_due_activation_sweep,
    run_due_activation_tick,
)
from appv24.workflows.staging import run_workflow_draft_stages, should_stage_workflow_drafts
from appv24.workflows.validator import validate_workflow_draft_text, validate_workflow_package
from appv24.workflows.webhook_ingress import WebhookReplayStore, handle_local_webhook_run
from appv24.workflows.webhook_server import (
    WorkflowWebhookServerConfig,
    WorkflowWebhookServerReport,
    format_workflow_webhook_server_report,
    serve_workflow_webhooks,
)


logger = logging.getLogger(__name__)


class _CliModelRegistry:
    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def get_all(self) -> list[Model]:
        return list(self._models)

    getAll = get_all

    def get_available(self) -> list[Model]:
        return list(self._models)

    getAvailable = get_available

    def find(self, provider: str, model_id: str) -> Model | None:
        local = next((model for model in self._models if model.provider == provider and model.id == model_id), None)
        if local is not None:
            return local
        registered = get_model(provider, model_id)
        if registered is not None:
            return registered
        return None

    def has_configured_auth(self, model: Model) -> bool:
        return has_configured_auth(model)

    hasConfiguredAuth = has_configured_auth


_VALID_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}
_MAX_WORKFLOW_SERVICE_TICKS = 1_000_000


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _resolve_dotenv_path(dotenv_arg: str | None, *, search_start: Path | None = None) -> Path:
    if dotenv_arg is not None:
        dotenv_path = Path(dotenv_arg).expanduser()
        if dotenv_path.is_absolute():
            return dotenv_path
        base = _npm_initial_cwd() or Path.cwd()
        return (base / dotenv_path).resolve()
    current = (search_start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return Path(".env")


def _resolve_cwd_path(cwd_arg: str) -> Path:
    cwd_path = Path(cwd_arg).expanduser()
    if cwd_path.is_absolute():
        return cwd_path.resolve()
    npm_initial_cwd = _npm_initial_cwd()
    if npm_initial_cwd is not None:
        return (npm_initial_cwd / cwd_path).resolve()
    return cwd_path.resolve()


def _npm_initial_cwd() -> Path | None:
    initial_cwd = os.environ.get("INIT_CWD")
    if not initial_cwd or not os.environ.get("npm_lifecycle_event"):
        return None
    return Path(initial_cwd).expanduser().resolve()


def _load_persisted_auth_credentials() -> None:
    auth_path = Path(get_auth_path()).expanduser()
    try:
        parsed = json.loads(auth_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(parsed, dict):
        return
    for provider, credential in parsed.items():
        if isinstance(provider, str) and isinstance(credential, dict):
            set_auth_credential(provider, dict(credential))


@dataclass(frozen=True)
class _StartupModelSelection:
    model: Model
    thinking_level: str | None = None
    scoped_models: list[ScopedModel] = field(default_factory=list)


def _model_from_env(
    dotenv_path: str | Path,
    *,
    config: ModelConfig | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
) -> Model:
    return _startup_model_from_env(
        dotenv_path,
        config=config,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_thinking=cli_thinking,
        cli_models=cli_models,
    ).model


def _startup_model_from_env(
    dotenv_path: str | Path,
    *,
    config: ModelConfig | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
) -> _StartupModelSelection:
    config = config or load_model_config("APPV24_WORKER_LLM", dotenv_path)
    env_model = _env_model_from_config(config)
    registered_models = _registered_models_with_env_fallback(env_model)
    live_models = _load_live_startup_models(
        env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
    )
    registry = _CliModelRegistry(_dedupe_startup_models([*registered_models, *live_models]))
    scoped_models = resolve_model_scope(cli_models or [], registry) if cli_models else []
    if not cli_model:
        if scoped_models:
            scoped = scoped_models[0]
            return _StartupModelSelection(
                model=scoped.model,
                thinking_level=cli_thinking or scoped.thinking_level,
                scoped_models=scoped_models,
            )
        return _StartupModelSelection(
            model=_matching_live_model(env_model, live_models) or env_model,
            thinking_level=cli_thinking,
            scoped_models=scoped_models,
        )

    resolved = resolve_cli_model(
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_thinking=cli_thinking,
        model_registry=registry,
    )
    if resolved.warning:
        print(f"Warning: {resolved.warning}", file=sys.stderr)
    if resolved.error:
        raise ValueError(resolved.error)
    if resolved.model is not None:
        return _StartupModelSelection(
            model=resolved.model,
            thinking_level=cli_thinking or resolved.thinking_level,
            scoped_models=scoped_models,
        )
    return _StartupModelSelection(model=env_model, thinking_level=cli_thinking, scoped_models=scoped_models)


def _registered_models_with_env_fallback(env_model: Model) -> list[Model]:
    models = [model for provider in get_providers() for model in get_models(provider)]
    if not any(model.provider == env_model.provider and model.id == env_model.id for model in models):
        models.append(env_model)
    return models


def _env_model_from_config(config: ModelConfig) -> Model:
    model_id = config.model or get_default_model_for_provider("openrouter") or "moonshotai/kimi-k2.6"
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openrouter",
        base_url=config.base_url,
        reasoning=False,
        context_window=128000,
        max_tokens=config.max_tokens or 8192,
    )


def _load_live_startup_models(
    env_model: Model,
    *,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool = False,
) -> list[Model]:
    should_fetch = _should_fetch_live_startup_models(
        env_model=env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
        list_models=list_models,
    ) or _implicit_default_openrouter_startup(
        env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
        list_models=list_models,
    )
    if not should_fetch or not _startup_live_catalog_enabled():
        return []
    try:
        return get_live_openrouter_models(base_model=env_model, force_refresh=True)
    except Exception as exc:  # noqa: BLE001 - startup must preserve custom fallback when live catalog fails.
        logger.warning("OpenRouter live model catalog unavailable during startup: %s", exc, exc_info=True)
        return []


def _should_fetch_live_startup_models(
    *,
    env_model: Model,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool,
) -> bool:
    if cli_provider:
        return normalize_provider(cli_provider) == "openrouter"
    if list_models:
        return False
    if cli_model and _model_reference_points_to_openrouter(cli_model):
        return True
    if cli_model and "/" in cli_model and normalize_provider(env_model.provider) == "openrouter":
        return True
    return any(
        _model_reference_points_to_openrouter(pattern) or _model_pattern_requests_live_catalog(pattern)
        for pattern in cli_models or []
    )


def _startup_live_catalog_enabled() -> bool:
    raw = os.environ.get("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true").strip().lower()
    return raw not in _FALSE_ENV_VALUES


def _model_reference_points_to_openrouter(reference: str) -> bool:
    text = reference.strip()
    if "/" not in text:
        return False
    prefix = text.split("/", 1)[0].strip()
    return bool(prefix) and normalize_provider(prefix) == "openrouter"


def _model_pattern_requests_live_catalog(pattern: str) -> bool:
    text = pattern.strip()
    if "*" not in text and "?" not in text and "[" not in text:
        return False
    if "/" not in text:
        return False
    prefix = text.split("/", 1)[0].strip()
    return bool(prefix) and normalize_provider(prefix) == "openrouter"


def _implicit_default_openrouter_startup(
    env_model: Model,
    *,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool,
) -> bool:
    return (
        not list_models
        and not cli_provider
        and not cli_model
        and not cli_models
        and normalize_provider(env_model.provider) == "openrouter"
    )


def _dedupe_startup_models(models: list[Model]) -> list[Model]:
    deduped: dict[tuple[str, str], Model] = {}
    for model in models:
        deduped[(model.provider, model.id)] = model
    return list(deduped.values())


def _matching_live_model(env_model: Model, live_models: list[Model]) -> Model | None:
    wanted = (normalize_provider(env_model.provider), env_model.id.lower())
    for model in live_models:
        if (normalize_provider(model.provider), model.id.lower()) == wanted:
            return model
    return None


def _hydrate_live_models_for_list(config: ModelConfig, args: argparse.Namespace) -> None:
    env_model = _env_model_from_config(config)
    cli_models = _split_models_arg(args.models)
    live_models = _load_live_startup_models(
        env_model,
        cli_provider=args.provider,
        cli_model=args.model,
        cli_models=cli_models,
        list_models=True,
    )
    if _startup_live_catalog_enabled() and not live_models and _should_fetch_live_startup_models(
        env_model=env_model,
        cli_provider=args.provider,
        cli_model=args.model,
        cli_models=cli_models,
        list_models=True,
    ):
        print("OpenRouter live model catalog unavailable; showing registered models only.", file=sys.stderr)
    for model in live_models:
        register_model(model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the appv24 pi+hermes coding app")
    parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts the interactive TUI.")
    parser.add_argument("--cwd", default=".", help="Working directory for tools")
    parser.add_argument(
        "--dotenv",
        default=None,
        help="Dotenv file for APPV24_WORKER_LLM/OpenRouter settings; defaults to nearest .env in --cwd or parents",
    )
    parser.add_argument("--provider", help="Provider name for --model resolution")
    parser.add_argument("--model", help='Model pattern or ID, including optional "provider/id" form')
    parser.add_argument("--models", help="Comma-separated model patterns for scoped cycling")
    parser.add_argument("--thinking", help="Set thinking level: off, minimal, low, medium, high, xhigh")
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_ID,
        help=(
            "Built-in app profile to use: coding, read-only, planning, research-lite, "
            "sre-lite, security-lab-lite, workflow-designer-lite"
        ),
    )
    parser.add_argument("--list-profiles", action="store_true", help="List built-in profiles and exit")
    parser.add_argument("--list-models", action="store_true", help="List available provider/model IDs and exit")
    parser.add_argument("--verbose-models", action="store_true", help="Show model metadata with --list-models")
    parser.add_argument("--list-providers", action="store_true", help="List available providers and exit")
    parser.add_argument("--temperature", help="Override generation temperature")
    parser.add_argument("--top-p", help="Override nucleus sampling top_p")
    parser.add_argument("--max-tokens", type=_positive_int_arg, help="Override generation max tokens")
    parser.add_argument("--timeout-seconds", help="Override provider request timeout")
    parser.add_argument("--provider-sort", help="Override provider routing sort preference where supported")
    parser.add_argument("--stop", help="Comma-separated or JSON-array stop sequences")
    parser.add_argument("--tui", action="store_true", help="Render live agent events with the ported differential TUI")
    parser.add_argument("--plain", action="store_true", help="Use the plain stdin loop instead of the interactive TUI")
    parser.add_argument(
        "--max-iterations",
        type=_positive_int_arg,
        help="Maximum tool-calling model iterations per turn (Hermes default: 90)",
    )
    parser.add_argument(
        "--tool-loop-hard-stop",
        action="store_true",
        help="Enable Hermes hard-stop thresholds for repeated failed/non-progressing tool calls",
    )
    parser.add_argument("--export", help="Export a session JSONL file to standalone HTML and exit")
    args = parser.parse_args(argv)

    try:
        validate_builtin_profiles()
        selected_profile = get_profile(args.profile)
    except ProfileError as error:
        parser.error(str(error))

    if args.list_profiles:
        _print_profile_list()
        return 0

    if args.export:
        output_path = args.prompt[0] if args.prompt else None
        try:
            exported_path = export_from_file(args.export, output_path)
        except Exception as error:  # noqa: BLE001 - CLI should convert export failures to an exit code.
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print(f"Exported to: {exported_path}")
        return 0

    cwd_path = _resolve_cwd_path(args.cwd)
    if not cwd_path.exists():
        print(f"Error: working directory does not exist: {cwd_path}", file=sys.stderr)
        return 1
    if not cwd_path.is_dir():
        print(f"Error: working directory is not a directory: {cwd_path}", file=sys.stderr)
        return 1

    if args.thinking and args.thinking not in _VALID_THINKING_LEVELS:
        print(
            f'Warning: Invalid thinking level "{args.thinking}". '
            f"Valid values: {', '.join(_VALID_THINKING_LEVELS)}",
            file=sys.stderr,
        )
        args.thinking = None

    effective_provider = args.provider or selected_profile.default_provider
    effective_model = args.model or (None if args.models else selected_profile.default_model)
    effective_thinking = args.thinking or selected_profile.thinking_level

    dotenv_path = _resolve_dotenv_path(args.dotenv, search_start=cwd_path)
    try:
        config = _config_with_cli_generation_params(load_model_config("APPV24_WORKER_LLM", dotenv_path), args)
    except ValueError as error:
        parser.error(str(error))
    register_builtin_providers(dotenv_path=dotenv_path, config=config)
    _load_persisted_auth_credentials()
    if args.list_providers:
        _print_provider_list()
        return 0
    if args.list_models:
        _hydrate_live_models_for_list(config, args)
        _print_model_list(verbose=args.verbose_models)
        return 0
    try:
        startup = _startup_model_from_env(
            dotenv_path,
            config=config,
            cli_provider=effective_provider,
            cli_model=effective_model,
            cli_thinking=effective_thinking,
            cli_models=_split_models_arg(args.models),
        )
    except ValueError as error:
        parser.error(str(error))
    generation_warnings = _generation_param_warnings_for_model(startup.model, config.generation_params)
    _print_generation_param_warnings(generation_warnings)
    runtime_options: dict[str, object] = profile_coding_app_kwargs(selected_profile)
    if args.max_iterations is not None:
        runtime_options["max_iterations"] = args.max_iterations
    if args.tool_loop_hard_stop:
        guardrails = dict(runtime_options.get("tool_loop_guardrails") or {})
        guardrails["hard_stop_enabled"] = True
        runtime_options["tool_loop_guardrails"] = guardrails
    agent_dir = get_agent_dir()
    session_path, session_id = _new_session_path(str(cwd_path), agent_dir)

    app = CodingApp(
        cwd=str(cwd_path),
        model=startup.model,
        thinking_level=startup.thinking_level or "off",
        scoped_models=startup.scoped_models,
        enable_tui=args.tui or not args.prompt and not args.plain,
        session_path=session_path,
        session_id=session_id,
        agent_dir=agent_dir,
        **runtime_options,
    )
    app.profile_id = selected_profile.id

    prompt = " ".join(args.prompt).strip()
    if prompt:
        if _handle_workflow_explain_command(app, prompt):
            return 0
        if _handle_workflow_export_bundle_command(app, prompt):
            return 0
        if _handle_workflow_bundle_check_command(app, prompt):
            return 0
        if _handle_workflow_export_intent_command(app, prompt):
            return 0
        if _handle_workflow_connectors_command(app, prompt):
            return 0
        if _handle_workflow_catalog_command(app, prompt):
            return 0
        if _handle_workflow_run_guided_command(app, prompt):
            return 0
        if _handle_workflow_continue_guided_command(app, prompt):
            return 0
        if _handle_workflow_live_plan_command(app, prompt):
            return 0
        if _handle_workflow_run_approval_command(app, prompt):
            return 0
        if _handle_workflow_run_local_command(app, prompt):
            return 0
        if _handle_workflow_approvals_command(app, prompt):
            return 0
        if _handle_workflow_approval_command(app, prompt):
            return 0
        if _handle_workflow_live_env_template_command(app, prompt):
            return 0
        if _handle_workflow_live_ready_command(app, prompt):
            return 0
        if _handle_workflow_live_draft_preflight_command(app, prompt):
            return 0
        if _handle_workflow_live_read_preflight_command(app, prompt):
            return 0
        if _handle_workflow_live_production_preflight_command(app, prompt):
            return 0
        if _handle_workflow_run_live_read_command(app, prompt):
            return 0
        if _handle_workflow_run_live_read_to_approval_command(app, prompt):
            return 0
        if _handle_workflow_run_live_draft_command(app, prompt):
            return 0
        if _handle_workflow_run_live_production_command(app, prompt):
            return 0
        if _handle_workflow_webhook_serve_command(app, prompt):
            return 0
        if _handle_workflow_activation_command(app, prompt):
            return 0
        _run_prompt_for_profile(app, selected_profile.id, prompt)
        repair_result = _repair_workflow_draft_if_needed(app, selected_profile.id)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id, repair_result)
        return 0

    if not args.plain:
        return InteractiveMode(
            app,
            generation_params=config.generation_params,
            generation_param_warnings=generation_warnings,
        ).run()

    while True:
        try:
            prompt = input("appv24> ").strip()
        except EOFError:
            return 0
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if not prompt:
            continue
        if _handle_workflow_explain_command(app, prompt):
            continue
        if _handle_workflow_export_bundle_command(app, prompt):
            continue
        if _handle_workflow_bundle_check_command(app, prompt):
            continue
        if _handle_workflow_export_intent_command(app, prompt):
            continue
        if _handle_workflow_connectors_command(app, prompt):
            continue
        if _handle_workflow_catalog_command(app, prompt):
            continue
        if _handle_workflow_run_guided_command(app, prompt):
            continue
        if _handle_workflow_continue_guided_command(app, prompt):
            continue
        if _handle_workflow_live_plan_command(app, prompt):
            continue
        if _handle_workflow_run_approval_command(app, prompt):
            continue
        if _handle_workflow_run_local_command(app, prompt):
            continue
        if _handle_workflow_approvals_command(app, prompt):
            continue
        if _handle_workflow_approval_command(app, prompt):
            continue
        if _handle_workflow_live_env_template_command(app, prompt):
            continue
        if _handle_workflow_live_ready_command(app, prompt):
            continue
        if _handle_workflow_live_draft_preflight_command(app, prompt):
            continue
        if _handle_workflow_live_read_preflight_command(app, prompt):
            continue
        if _handle_workflow_live_production_preflight_command(app, prompt):
            continue
        if _handle_workflow_run_live_read_command(app, prompt):
            continue
        if _handle_workflow_run_live_read_to_approval_command(app, prompt):
            continue
        if _handle_workflow_run_live_draft_command(app, prompt):
            continue
        if _handle_workflow_run_live_production_command(app, prompt):
            continue
        if _handle_workflow_webhook_serve_command(app, prompt):
            continue
        if _handle_workflow_activation_command(app, prompt):
            continue
        if _handle_workflow_save_command(app, selected_profile.id, prompt):
            continue
        _run_prompt_for_profile(app, selected_profile.id, prompt)
        repair_result = _repair_workflow_draft_if_needed(app, selected_profile.id)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id, repair_result)


def _split_models_arg(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _generation_params_from_args(args: argparse.Namespace) -> GenerationParams:
    values = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "timeout_seconds": args.timeout_seconds,
        "provider_sort": args.provider_sort,
        "stop": args.stop,
    }
    return params_from_mapping(values, source="cli")


def _config_with_cli_generation_params(config: ModelConfig, args: argparse.Namespace) -> ModelConfig:
    cli_params = _generation_params_from_args(args)
    merged = merge_generation_params(config.generation_params, cli_params)
    return replace(
        config,
        temperature=merged.temperature if merged.temperature is not None else config.temperature,
        top_p=merged.top_p if merged.top_p is not None else config.top_p,
        max_tokens=merged.max_tokens if merged.max_tokens is not None else config.max_tokens,
        timeout_seconds=merged.timeout_seconds if merged.timeout_seconds is not None else config.timeout_seconds,
        provider_sort=merged.provider_sort if merged.provider_sort is not None else config.provider_sort,
        stop=list(merged.stop) if merged.stop else list(config.stop),
        generation_params=merged,
    )


def _print_provider_list() -> None:
    providers = sorted(set(get_providers()))
    for provider in providers:
        print(provider)


def _print_model_list(*, verbose: bool = False) -> None:
    for provider in sorted(get_providers()):
        for model in sorted(get_models(provider), key=lambda item: item.id):
            if not verbose:
                print(f"{provider}/{model.id}")
                continue
            input_types = ",".join(getattr(model, "input", []) or [])
            reasoning = "true" if getattr(model, "reasoning", False) else "false"
            print(
                f"{provider}/{model.id} "
                f"context={getattr(model, 'context_window', 0)} "
                f"max_tokens={getattr(model, 'max_tokens', 0)} "
                f"reasoning={reasoning} "
                f"input={input_types or 'text'}"
            )


def _print_profile_list() -> None:
    for profile in list_profiles():
        print(f"{profile.id}\t{profile.description}")


def _generation_param_warnings_for_model(
    model: Model,
    params: GenerationParams,
) -> list[ProviderParamWarning]:
    try:
        payload = build_generation_payload(
            provider=model.provider,
            api_mode=determine_api_mode(model.provider, model.base_url),
            params=params,
            tools_enabled=True,
        )
    except ValueError:
        return []
    return list(payload.warnings)


def _print_generation_param_warnings(warnings: list[ProviderParamWarning]) -> None:
    for warning in warnings:
        print(
            f"Warning: generation parameter {warning.param} {warning.action}: {warning.reason}",
            file=sys.stderr,
        )


def _print_last_assistant(app: CodingApp) -> None:
    for message in reversed(app.messages):
        if getattr(message, "role", None) != "assistant":
            continue
        texts = [block.text for block in getattr(message, "content", []) if getattr(block, "type", None) == "text"]
        if texts:
            print("".join(texts))
        return


def _run_prompt_for_profile(app: CodingApp, profile_id: str, prompt: str) -> None:
    if not should_stage_workflow_drafts(profile_id):
        app.run_turn(prompt)
        return

    def on_stage(index: int, total: int, stage_id: str) -> None:
        print(f"Workflow draft stage: {stage_id} {index}/{total}")

    run_workflow_draft_stages(prompt, app.run_turn, on_stage=on_stage)


def _repair_workflow_draft_if_needed(app: CodingApp, profile_id: str) -> WorkflowDraftRepairResult | None:
    if not should_validate_workflow_drafts(profile_id):
        return None

    def on_attempt(attempt: int, max_attempts: int | None, _validation) -> None:
        suffix = f"{attempt}/{max_attempts}" if max_attempts is not None else str(attempt)
        print(f"Workflow draft repair: attempt {suffix}")

    try:
        return repair_workflow_draft_messages(
            app.messages,
            app.run_turn,
            config=WorkflowDraftRepairConfig(max_issues=8, max_repeated_validation_results=4),
            on_attempt=on_attempt,
        )
    except Exception as error:  # noqa: BLE001 - repair failures are rendered as product feedback
        print(f"Workflow draft repair failed: {error}")
        return None


def _print_workflow_validation_if_needed(
    app: CodingApp,
    profile_id: str,
    repair_result: WorkflowDraftRepairResult | None = None,
) -> None:
    if not should_validate_workflow_drafts(profile_id):
        return

    if repair_result is not None:
        result = repair_result.validation
    else:
        text = assistant_text_from_messages(app.messages)
        result = validate_workflow_draft_text(text) if text.strip() else None

    if result is None:
        return

    if repair_result is not None:
        repair_stop_report = format_workflow_repair_stop_report(repair_result)
        if repair_stop_report:
            print(repair_stop_report)

    print(format_workflow_validation_report(result))
    quality = workflow_quality_result_for_draft(result)
    if quality is not None:
        print(format_workflow_quality_report(quality))


def _workflow_connector_config(env=None) -> ConnectorConfigResolver:
    return ConnectorConfigResolver(env=env or os.environ)


def _workflow_live_read_transports(connector_config: ConnectorConfigResolver):
    transports = dict(build_live_read_transports_from_env(connector_config.env))
    transports.update(build_live_read_transports_from_connector_config(connector_config))
    return transports


def _workflow_live_production_transports(connector_config: ConnectorConfigResolver):
    transports = dict(build_live_production_transports_from_env(connector_config.env))
    transports.update(build_live_production_transports_from_connector_config(connector_config))
    return transports


def _handle_workflow_activation_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_activation_command(prompt):
        return False

    args = prompt.split()
    command = args[0] if args else ""
    try:
        store = WorkflowActivationStore(_workflow_activation_root(app.cwd))
        if command == "/workflow-activation-list":
            if len(args) != 1:
                raise ValueError("expected /workflow-activation-list")
            print(format_workflow_activation_list(store.list_statuses()))
        elif command in {"/workflow-activation-enable", "/workflow-activation-disable"}:
            if len(args) != 2:
                raise ValueError(f"expected {command} <id>")
            activation_id = _resolve_workflow_activation_id(args[1])
            activation = store.set_enabled(activation_id, command == "/workflow-activation-enable")
            state = "enabled" if activation.enabled else "disabled"
            print(f"Workflow activation {state}: {activation.activation_id}")
        elif command == "/workflow-activate":
            if len(args) not in {2, 3}:
                raise ValueError("expected /workflow-activate <path> [fixture]")
            package_path = _resolve_workflow_package_path(app.cwd, args[1])
            activation = store.create_activation(
                package_path,
                trigger_type="manual",
                fixture_name=args[2] if len(args) == 3 else "happy_path.yaml",
                mode=WorkflowRunMode.LOCAL,
                approve_all=True,
            )
            print(
                "\n".join(
                    [
                        f"Workflow activation created: {activation.activation_id}",
                        f"workflow: {activation.workflow_name}",
                        f"trigger: {activation.trigger_type}",
                        f"fixture: {activation.fixture_name}",
                        f"mode: {activation.mode.value}",
                    ]
                )
            )
        elif command == "/workflow-activation-run":
            if len(args) != 2:
                raise ValueError("expected /workflow-activation-run <id>")
            activation_id = _resolve_workflow_activation_id(args[1])
            report = store.run_activation(activation_id)
            rendered = format_workflow_run_report(report).replace("Workflow local run:", "Workflow activation run:", 1)
            print(f"{rendered}\nactivation: {activation_id}")
        elif command == "/workflow-activation-tick":
            if len(args) != 2:
                raise ValueError("expected /workflow-activation-tick <id>")
            decision = run_due_activation_tick(
                store,
                _resolve_workflow_activation_id(args[1]),
                now=datetime.now(timezone.utc),
            )
            lines = [
                f"Workflow activation tick: {decision.status}",
                f"activation: {decision.activation_id}",
                f"trigger: {decision.trigger_type}",
                f"reason: {decision.reason}",
            ]
            if decision.run_id:
                lines.append(f"run: {decision.run_id}")
            if decision.run_status:
                lines.append(f"run status: {decision.run_status}")
            print("\n".join(lines))
        elif command == "/workflow-activation-tick-all":
            if len(args) != 1:
                raise ValueError("expected /workflow-activation-tick-all")
            report = run_due_activation_sweep(store, now=datetime.now(timezone.utc))
            print(format_trigger_sweep_report(report))
        elif command == "/workflow-activation-loop":
            if len(args) not in {2, 3}:
                raise ValueError("expected /workflow-activation-loop <ticks> [interval-seconds]")
            report = run_activation_scheduler_loop(
                store,
                ticks=_parse_activation_loop_ticks(args[1]),
                interval_seconds=_parse_activation_loop_interval(args[2]) if len(args) == 3 else 5.0,
            )
            print(format_trigger_loop_report(report))
        elif command == "/workflow-activation-webhook-run":
            if len(args) not in {3, 4}:
                raise ValueError("expected /workflow-activation-webhook-run <id> <payload-file> [replay-key]")
            secret = os.environ.get("APPV24_WORKFLOW_WEBHOOK_SECRET", "")
            if not secret:
                raise ValueError("APPV24_WORKFLOW_WEBHOOK_SECRET is required")
            payload_path = _resolve_workflow_payload_path(app.cwd, args[2])
            headers = {"x-appv24-webhook-secret": secret}
            if len(args) == 4:
                headers["x-appv24-replay-key"] = _resolve_webhook_replay_key(args[3])
            decision = handle_local_webhook_run(
                store,
                _resolve_workflow_activation_id(args[1]),
                payload=payload_path.read_bytes(),
                headers=headers,
                shared_secret=secret,
                replay_store=WebhookReplayStore(_workflow_webhook_replay_root(app.cwd)),
                now=datetime.now(timezone.utc),
            )
            lines = [
                f"Workflow activation webhook run: {decision.status}",
                f"activation: {decision.activation_id}",
                f"reason: {decision.reason}",
            ]
            if decision.replay_key:
                lines.append(f"replay key: {decision.replay_key}")
            if decision.run_id:
                lines.append(f"run: {decision.run_id}")
            if decision.run_status:
                lines.append(f"run status: {decision.run_status}")
            print("\n".join(lines))
        elif command == "/workflow-activation-run-explain":
            if len(args) not in {2, 3}:
                raise ValueError("expected /workflow-activation-run-explain <id> [run-id]")
            activation_id = _resolve_workflow_activation_id(args[1])
            explanation = explain_workflow_activation_run(store, activation_id, args[2] if len(args) == 3 else None)
            print(format_workflow_run_explanation(explanation))
        elif command == "/workflow-activation-status":
            if len(args) != 2:
                raise ValueError("expected /workflow-activation-status <id>")
            status = store.status(_resolve_workflow_activation_id(args[1]))
            print(
                "\n".join(
                    [
                        "Workflow activation status:",
                        f"activation: {status['activation_id']}",
                        f"workflow: {status['workflow_name']}",
                        f"trigger: {status['trigger_type']}",
                        f"enabled: {status['enabled']}",
                        f"package: {status['package_status']}",
                        f"last run: {status['last_run_id'] or 'none'}",
                        f"last status: {status['last_status'] or 'none'}",
                        f"runs: {status['runs']}",
                    ]
                )
            )
        elif command == "/workflow-activation-history":
            if len(args) != 2:
                raise ValueError("expected /workflow-activation-history <id>")
            activation_id = _resolve_workflow_activation_id(args[1])
            runs = store.history(activation_id)
            lines = [f"Workflow activation history: {activation_id}"]
            if runs:
                lines.extend(f"run-{index}: {run_id}" for index, run_id in enumerate(runs, start=1))
            else:
                lines.append("none")
            print("\n".join(lines))
        else:
            raise ValueError("unknown workflow activation command")
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow activation failed: {error}")
    return True


def _handle_workflow_webhook_serve_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_webhook_serve_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) not in {2, 3, 4}:
            raise ValueError("expected /workflow-webhook-serve <max-requests> [host] [port]")
        secret = os.environ.get("APPV24_WORKFLOW_WEBHOOK_SECRET", "")
        if not secret:
            raise ValueError("APPV24_WORKFLOW_WEBHOOK_SECRET is required")
        max_requests = _parse_webhook_server_max_requests(args[1])
        host = args[2] if len(args) >= 3 else "127.0.0.1"
        port = _parse_webhook_server_port(args[3]) if len(args) == 4 else 8765
        report = serve_workflow_webhooks(
            WorkflowWebhookServerConfig(
                activation_root=_workflow_activation_root(app.cwd),
                replay_root=_workflow_webhook_replay_root(app.cwd),
                shared_secret=secret,
                host=host,
                port=port,
            ),
            max_requests=max_requests,
        )
        print(format_workflow_webhook_server_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow webhook server failed: {error}")
    return True


def _handle_workflow_export_intent_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_export_intent_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) != 2:
            raise ValueError("expected /workflow-export-intent <path>")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        print(format_deployment_intent(export_deployment_intent_from_path(package_path)))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow deployment intent export failed: {error}")
    return True


def _handle_workflow_export_bundle_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_export_bundle_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) not in {2, 3}:
            raise ValueError("expected /workflow-export-bundle <path> [output-dir]")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        output_dir = _resolve_workflow_package_path(app.cwd, args[2]) if len(args) == 3 else None
        bundle = write_workflow_bundle(package_path, output_dir=output_dir, env=os.environ)
        print(format_workflow_bundle_export(bundle))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow bundle export failed: {error}")
    return True


def _handle_workflow_bundle_check_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_bundle_check_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) != 2:
            raise ValueError("expected /workflow-bundle-check <bundle-dir>")
        bundle_dir = _resolve_workflow_package_path(app.cwd, args[1])
        print(format_workflow_bundle_check(check_workflow_bundle(bundle_dir)))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow bundle check failed: {error}")
    return True


def _handle_workflow_connectors_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_connectors_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) != 2:
            raise ValueError("expected /workflow-connectors <path>")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        print(inspect_workflow_connector_registry(package_path, env=os.environ).to_text())
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow connector registry failed: {error}")
    return True


def _handle_workflow_catalog_command(app: CodingApp, prompt: str) -> bool:
    if prompt.strip() != "/workflow-catalog":
        return False
    print(format_workflow_capability_catalog(build_workflow_capability_catalog()))
    return True


def _handle_workflow_run_guided_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_guided_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) not in {2, 3}:
            raise ValueError("expected /workflow-run-guided <path> [fixture]")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        fixture_name = args[2] if len(args) == 3 else "happy_path.yaml"
        connector_config = _workflow_connector_config()
        report = prepare_guided_workflow_run(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=fixture_name,
                mode=WorkflowRunMode.LIVE_READ,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            live_read_broker=LiveWorkflowBroker(_workflow_live_read_transports(connector_config), mode="live-read"),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
        )
        print(format_guided_workflow_run_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow guided run failed: {error}")
    return True


def _handle_workflow_continue_guided_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_continue_guided_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) not in {3, 4}:
            raise ValueError("expected /workflow-continue-guided <path> <approval-run-id> [fixture]")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        approval_run_id = args[2]
        fixture_name = args[3] if len(args) == 4 else "happy_path.yaml"
        connector_config = _workflow_connector_config()
        report = continue_guided_workflow_run(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=fixture_name,
                mode=WorkflowRunMode.LIVE_PRODUCTION,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            approval_run_id=approval_run_id,
            live_production_broker=LiveWorkflowBroker(
                _workflow_live_production_transports(connector_config),
                mode="live-production",
                production_enabled=True,
            ),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
            env=os.environ,
        )
        print(format_guided_workflow_run_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow guided continue failed: {error}")
    return True


def _handle_workflow_live_plan_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_live_plan_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) not in {2, 3}:
            raise ValueError("expected /workflow-live-plan <path> [fixture]")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        fixture_name = args[2] if len(args) == 3 else "happy_path.yaml"
        print(format_workflow_live_plan(build_workflow_live_plan_from_path(package_path, fixture_name)))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live plan failed: {error}")
    return True


def _handle_workflow_explain_command(app: CodingApp, prompt: str) -> bool:
    if not (_is_workflow_explain_command(prompt) or _is_workflow_run_explain_command(prompt)):
        return False

    args = prompt.split()
    try:
        if _is_workflow_explain_command(prompt):
            if len(args) != 2:
                raise ValueError("expected /workflow-explain <path>")
            package_path = _resolve_workflow_package_path(app.cwd, args[1])
            print(format_workflow_package_explanation(explain_workflow_package(package_path)))
            return True

        if len(args) not in {2, 3}:
            raise ValueError("expected /workflow-run-explain <path> [run-id]")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        print(format_workflow_run_explanation(explain_workflow_run(package_path, args[2] if len(args) == 3 else None)))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow explain failed: {error}")
    return True


def _handle_workflow_run_local_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_command(prompt):
        return False

    parsed = _parse_workflow_run_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        report = run_workflow(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LOCAL,
                approve_all=parsed.approve_all,
            )
        )
        rendered = format_workflow_run_report(report)
        if parsed.label == "sandbox":
            rendered = rendered.replace("Workflow local run:", "Workflow sandbox run:", 1)
        print(rendered)
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow local run failed: {error}")
    return True


def _handle_workflow_run_approval_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_approval_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) not in {2, 3}:
            raise ValueError("expected /workflow-run-approval <path> [fixture]")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        report = run_workflow(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=args[2] if len(args) == 3 else None,
                mode=WorkflowRunMode.LOCAL,
                approve_all=False,
            )
        )
        print(format_workflow_run_report(report).replace("Workflow local run:", "Workflow approval prep run:", 1))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow approval prep run failed: {error}")
    return True


def _handle_workflow_approval_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_approval_command(prompt):
        return False

    args = prompt.split()
    command = args[0] if args else ""
    try:
        if len(args) != 4:
            raise ValueError(f"expected {command} <path> <run-id> <approval-request-id>")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        result = record_workflow_approval_command(
            package_path,
            args[2],
            args[3],
            approved=command == "/workflow-approve",
            decided_by="cli",
        )
        print(format_workflow_approval_command_result(result))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow approval failed: {error}")
    return True


def _handle_workflow_approvals_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_approvals_command(prompt):
        return False

    args = prompt.split()
    try:
        if len(args) != 3:
            raise ValueError("expected /workflow-approvals <path> <run-id>")
        package_path = _resolve_workflow_package_path(app.cwd, args[1])
        result = list_workflow_approval_requests(package_path, args[2])
        print(format_workflow_approval_list(result))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow approvals failed: {error}")
    return True


def _handle_workflow_live_ready_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_live_ready_command(prompt):
        return False

    parsed = _parse_workflow_live_ready_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        report = inspect_workflow_live_readiness(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=parsed.mode,
                approval_run_id=parsed.approval_run_id,
                connector_allowlist=parsed.connector_allowlist,
            ),
            env=os.environ,
            connector_config=connector_config,
        )
        print(format_workflow_live_readiness_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live readiness failed: {error}")
    return True


def _handle_workflow_live_env_template_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_live_env_template_command(prompt):
        return False

    parsed = _parse_workflow_live_env_template_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        template = build_workflow_live_env_template(
            WorkflowRunRequest(
                package_path=package_path,
                mode=parsed.mode,
                connector_allowlist=parsed.connector_allowlist,
            )
        )
        print(format_workflow_live_env_template(template))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live env template failed: {error}")
    return True


def _is_workflow_export_intent_command(prompt: str) -> bool:
    return prompt == "/workflow-export-intent" or prompt.startswith("/workflow-export-intent ")


def _is_workflow_export_bundle_command(prompt: str) -> bool:
    return prompt == "/workflow-export-bundle" or prompt.startswith("/workflow-export-bundle ")


def _is_workflow_bundle_check_command(prompt: str) -> bool:
    return prompt == "/workflow-bundle-check" or prompt.startswith("/workflow-bundle-check ")


def _is_workflow_connectors_command(prompt: str) -> bool:
    return prompt == "/workflow-connectors" or prompt.startswith("/workflow-connectors ")


def _is_workflow_run_guided_command(prompt: str) -> bool:
    return prompt == "/workflow-run-guided" or prompt.startswith("/workflow-run-guided ")


def _is_workflow_continue_guided_command(prompt: str) -> bool:
    return prompt == "/workflow-continue-guided" or prompt.startswith("/workflow-continue-guided ")


def _is_workflow_live_plan_command(prompt: str) -> bool:
    return prompt == "/workflow-live-plan" or prompt.startswith("/workflow-live-plan ")


def _is_workflow_explain_command(prompt: str) -> bool:
    return prompt == "/workflow-explain" or prompt.startswith("/workflow-explain ")


def _is_workflow_run_explain_command(prompt: str) -> bool:
    return prompt == "/workflow-run-explain" or prompt.startswith("/workflow-run-explain ")


def _is_workflow_run_local_command(prompt: str) -> bool:
    return prompt == "/workflow-run-local" or prompt.startswith("/workflow-run-local ")


def _is_workflow_run_sandbox_command(prompt: str) -> bool:
    return prompt == "/workflow-run-sandbox" or prompt.startswith("/workflow-run-sandbox ")


def _is_workflow_run_approval_command(prompt: str) -> bool:
    return prompt == "/workflow-run-approval" or prompt.startswith("/workflow-run-approval ")


def _is_workflow_run_command(prompt: str) -> bool:
    return (
        _is_workflow_run_local_command(prompt)
        or _is_workflow_run_sandbox_command(prompt)
        or prompt == "/workflow-run"
        or prompt.startswith("/workflow-run ")
    )


@dataclass(frozen=True)
class _WorkflowRunCommand:
    package_path: str = ""
    fixture_name: str | None = None
    approve_all: bool = False
    label: str = "local"
    error: str | None = None


def _parse_workflow_run_command(prompt: str) -> _WorkflowRunCommand:
    args = prompt.split()
    command = args[0] if args else ""
    if command == "/workflow-run-local":
        if len(args) < 2:
            return _WorkflowRunCommand(error="missing package path")
        if len(args) > 3:
            return _WorkflowRunCommand(error="expected /workflow-run-local <path> [fixture]")
        return _WorkflowRunCommand(package_path=args[1], fixture_name=args[2] if len(args) == 3 else None, approve_all=True)

    if command == "/workflow-run-sandbox":
        if len(args) < 2:
            return _WorkflowRunCommand(error="missing package path")
        if len(args) > 3:
            return _WorkflowRunCommand(error="expected /workflow-run-sandbox <path> [fixture]")
        return _WorkflowRunCommand(
            package_path=args[1],
            fixture_name=args[2] if len(args) == 3 else None,
            approve_all=True,
            label="sandbox",
        )

    if command != "/workflow-run":
        return _WorkflowRunCommand(error="unknown workflow run command")
    if len(args) < 5:
        return _WorkflowRunCommand(error="expected /workflow-run <path> <fixture> --mode local --approve-all")
    package_path = args[1]
    fixture_name = args[2]
    flags = args[3:]
    if "--mode" not in flags:
        return _WorkflowRunCommand(error="missing --mode local")
    mode_index = flags.index("--mode")
    if mode_index + 1 >= len(flags) or flags[mode_index + 1] != "local":
        return _WorkflowRunCommand(error="only --mode local is supported")
    if "--approve-all" not in flags:
        return _WorkflowRunCommand(error="missing --approve-all")
    extra = list(flags)
    del extra[mode_index : mode_index + 2]
    extra.remove("--approve-all")
    if extra:
        return _WorkflowRunCommand(error=f"unsupported workflow-run flags: {' '.join(extra)}")
    return _WorkflowRunCommand(package_path=package_path, fixture_name=fixture_name, approve_all=True)


def _handle_workflow_live_draft_preflight_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_live_draft_preflight_command(prompt):
        return False

    parsed = _parse_workflow_live_draft_preflight_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        report = preflight_live_draft_workflow(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_DRAFT,
                approval_run_id=parsed.approval_run_id,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
            available_transports=build_live_draft_transports_from_env(os.environ).keys(),
        )
        print(format_live_draft_preflight_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-draft preflight failed: {error}")
    return True


def _handle_workflow_live_read_preflight_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_live_read_preflight_command(prompt):
        return False

    parsed = _parse_workflow_live_read_preflight_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        report = preflight_live_read_workflow(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_READ,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
            available_transports=_workflow_live_read_transports(connector_config).keys(),
        )
        print(format_live_read_preflight_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-read preflight failed: {error}")
    return True


def _handle_workflow_live_production_preflight_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_live_production_preflight_command(prompt):
        return False

    parsed = _parse_workflow_live_production_preflight_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        report = preflight_live_production_workflow(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_PRODUCTION,
                approval_run_id=parsed.approval_run_id,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
            available_transports=_workflow_live_production_transports(connector_config).keys(),
        )
        print(format_live_production_preflight_report(report))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-production preflight failed: {error}")
    return True


def _handle_workflow_run_live_draft_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_live_draft_command(prompt):
        return False

    parsed = _parse_workflow_run_live_draft_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        phase_report = run_workflow_approved_live_draft(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_DRAFT,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            approval_run_id=parsed.approval_run_id,
            live_draft_broker=LiveWorkflowBroker(build_live_draft_transports_from_env(os.environ), mode="live-draft"),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
        )
        report = phase_report.live_draft_report
        if report is None:
            raise RuntimeError("live-draft phase did not produce a run report")
        print(format_workflow_run_report(report).replace("Workflow local run:", "Workflow live-draft run:", 1))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-draft run failed: {error}")
    return True


def _handle_workflow_run_live_production_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_live_production_command(prompt):
        return False

    parsed = _parse_workflow_run_live_production_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        phase_report = run_workflow_approved_live_production(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_PRODUCTION,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            approval_run_id=parsed.approval_run_id,
            live_production_broker=LiveWorkflowBroker(
                _workflow_live_production_transports(connector_config),
                mode="live-production",
                production_enabled=True,
            ),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
        )
        report = phase_report.live_production_report
        if report is None:
            raise RuntimeError("live-production phase did not produce a run report")
        print(format_workflow_run_report(report).replace("Workflow local run:", "Workflow live-production run:", 1))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-production run failed: {error}")
    return True


def _handle_workflow_run_live_read_to_approval_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_live_read_to_approval_command(prompt):
        return False

    parsed = _parse_workflow_run_live_read_to_approval_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        report = run_workflow_live_read_to_approval(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_READ,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            live_read_broker=LiveWorkflowBroker(_workflow_live_read_transports(connector_config), mode="live-read"),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
        )
        print(format_workflow_phase_run_report(report, title="Workflow live-read-to-approval run"))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-read-to-approval run failed: {error}")
    return True


def _handle_workflow_run_live_read_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_run_live_read_command(prompt):
        return False

    parsed = _parse_workflow_run_live_read_command(prompt)
    try:
        if parsed.error is not None:
            raise ValueError(parsed.error)
        package_path = _resolve_workflow_package_path(app.cwd, parsed.package_path)
        connector_config = _workflow_connector_config()
        report = run_live_read_workflow(
            WorkflowRunRequest(
                package_path=package_path,
                fixture_name=parsed.fixture_name,
                mode=WorkflowRunMode.LIVE_READ,
                connector_allowlist=parsed.connector_allowlist,
                operator_env=workflow_operator_env_from_env(os.environ),
            ),
            broker=LiveWorkflowBroker(_workflow_live_read_transports(connector_config), mode="live-read"),
            credential_resolver=credential_resolver_for_package_connector_config(package_path, connector_config),
        )
        print(format_workflow_run_report(report).replace("Workflow local run:", "Workflow live-read run:", 1))
    except Exception as error:  # noqa: BLE001 - CLI command failures should print, not crash the loop.
        print(f"Workflow live-read run failed: {error}")
    return True


def _is_workflow_approval_command(prompt: str) -> bool:
    return (
        prompt == "/workflow-approve"
        or prompt.startswith("/workflow-approve ")
        or prompt == "/workflow-deny"
        or prompt.startswith("/workflow-deny ")
    )


def _is_workflow_approvals_command(prompt: str) -> bool:
    return prompt == "/workflow-approvals" or prompt.startswith("/workflow-approvals ")


def _is_workflow_live_draft_preflight_command(prompt: str) -> bool:
    return prompt == "/workflow-live-draft-preflight" or prompt.startswith("/workflow-live-draft-preflight ")


def _is_workflow_live_read_preflight_command(prompt: str) -> bool:
    return prompt == "/workflow-live-read-preflight" or prompt.startswith("/workflow-live-read-preflight ")


def _is_workflow_live_production_preflight_command(prompt: str) -> bool:
    return prompt == "/workflow-live-production-preflight" or prompt.startswith("/workflow-live-production-preflight ")


def _is_workflow_live_ready_command(prompt: str) -> bool:
    return prompt == "/workflow-live-ready" or prompt.startswith("/workflow-live-ready ")


def _is_workflow_live_env_template_command(prompt: str) -> bool:
    return prompt == "/workflow-live-env-template" or prompt.startswith("/workflow-live-env-template ")


def _is_workflow_run_live_draft_command(prompt: str) -> bool:
    return prompt == "/workflow-run-live-draft" or prompt.startswith("/workflow-run-live-draft ")


def _is_workflow_run_live_read_command(prompt: str) -> bool:
    return prompt == "/workflow-run-live-read" or prompt.startswith("/workflow-run-live-read ")


def _is_workflow_run_live_read_to_approval_command(prompt: str) -> bool:
    return prompt == "/workflow-run-live-read-to-approval" or prompt.startswith("/workflow-run-live-read-to-approval ")


def _is_workflow_run_live_production_command(prompt: str) -> bool:
    return prompt == "/workflow-run-live-production" or prompt.startswith("/workflow-run-live-production ")


@dataclass(frozen=True)
class _WorkflowLiveDraftPreflightCommand:
    package_path: str = ""
    fixture_name: str = ""
    approval_run_id: str = ""
    connector_allowlist: tuple[str, ...] = ()
    error: str | None = None


def _parse_workflow_live_draft_preflight_command(prompt: str) -> _WorkflowLiveDraftPreflightCommand:
    args = prompt.split()
    expected = "expected /workflow-live-draft-preflight <path> <fixture> <approval-run-id> --allow <action>[,<action>]"
    if len(args) != 6 or args[0] != "/workflow-live-draft-preflight" or args[4] != "--allow":
        return _WorkflowLiveDraftPreflightCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[5].split(",") if action.strip())
    if not allowlist:
        return _WorkflowLiveDraftPreflightCommand(error=expected)
    return _WorkflowLiveDraftPreflightCommand(
        package_path=args[1],
        fixture_name=args[2],
        approval_run_id=args[3],
        connector_allowlist=allowlist,
    )


@dataclass(frozen=True)
class _WorkflowLiveReadPreflightCommand:
    package_path: str = ""
    fixture_name: str = ""
    connector_allowlist: tuple[str, ...] = ()
    error: str | None = None


def _parse_workflow_live_read_preflight_command(prompt: str) -> _WorkflowLiveReadPreflightCommand:
    args = prompt.split()
    expected = "expected /workflow-live-read-preflight <path> <fixture> --allow <action>[,<action>]"
    if len(args) != 5 or args[0] != "/workflow-live-read-preflight" or args[3] != "--allow":
        return _WorkflowLiveReadPreflightCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[4].split(",") if action.strip())
    if not allowlist:
        return _WorkflowLiveReadPreflightCommand(error=expected)
    return _WorkflowLiveReadPreflightCommand(
        package_path=args[1],
        fixture_name=args[2],
        connector_allowlist=allowlist,
    )


def _parse_workflow_live_production_preflight_command(prompt: str) -> _WorkflowLiveDraftPreflightCommand:
    args = prompt.split()
    expected = "expected /workflow-live-production-preflight <path> <fixture> <approval-run-id> --allow <action>[,<action>]"
    if len(args) != 6 or args[0] != "/workflow-live-production-preflight" or args[4] != "--allow":
        return _WorkflowLiveDraftPreflightCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[5].split(",") if action.strip())
    if not allowlist:
        return _WorkflowLiveDraftPreflightCommand(error=expected)
    return _WorkflowLiveDraftPreflightCommand(
        package_path=args[1],
        fixture_name=args[2],
        approval_run_id=args[3],
        connector_allowlist=allowlist,
    )


@dataclass(frozen=True)
class _WorkflowLiveReadyCommand:
    mode: WorkflowRunMode = WorkflowRunMode.LIVE_READ
    package_path: str = ""
    fixture_name: str = ""
    approval_run_id: str | None = None
    connector_allowlist: tuple[str, ...] = ()
    error: str | None = None


def _parse_workflow_live_ready_command(prompt: str) -> _WorkflowLiveReadyCommand:
    args = prompt.split()
    expected = (
        "expected /workflow-live-ready <live-read|live-draft|live-production> "
        "<path> <fixture> [approval-run-id] --allow <action>[,<action>]"
    )
    if len(args) < 6 or args[0] != "/workflow-live-ready":
        return _WorkflowLiveReadyCommand(error=expected)
    mode = _workflow_live_ready_mode(args[1])
    if mode is None:
        return _WorkflowLiveReadyCommand(error=expected)
    if mode is WorkflowRunMode.LIVE_READ:
        if len(args) != 6 or args[4] != "--allow":
            return _WorkflowLiveReadyCommand(error=expected)
        allowlist = _parse_allowlist(args[5])
        if not allowlist:
            return _WorkflowLiveReadyCommand(error=expected)
        return _WorkflowLiveReadyCommand(mode=mode, package_path=args[2], fixture_name=args[3], connector_allowlist=allowlist)
    if len(args) != 7 or args[5] != "--allow":
        return _WorkflowLiveReadyCommand(error=expected)
    allowlist = _parse_allowlist(args[6])
    if not allowlist:
        return _WorkflowLiveReadyCommand(error=expected)
    return _WorkflowLiveReadyCommand(
        mode=mode,
        package_path=args[2],
        fixture_name=args[3],
        approval_run_id=args[4],
        connector_allowlist=allowlist,
    )


def _parse_workflow_live_env_template_command(prompt: str) -> _WorkflowLiveReadyCommand:
    args = prompt.split()
    expected = "expected /workflow-live-env-template <live-read|live-draft|live-production> <path> --allow <action>[,<action>]"
    if len(args) != 5 or args[0] != "/workflow-live-env-template" or args[3] != "--allow":
        return _WorkflowLiveReadyCommand(error=expected)
    mode = _workflow_live_ready_mode(args[1])
    if mode is None:
        return _WorkflowLiveReadyCommand(error=expected)
    allowlist = _parse_allowlist(args[4])
    if not allowlist:
        return _WorkflowLiveReadyCommand(error=expected)
    return _WorkflowLiveReadyCommand(mode=mode, package_path=args[2], connector_allowlist=allowlist)


def _workflow_live_ready_mode(value: str) -> WorkflowRunMode | None:
    return {
        "live-read": WorkflowRunMode.LIVE_READ,
        "live-draft": WorkflowRunMode.LIVE_DRAFT,
        "live-production": WorkflowRunMode.LIVE_PRODUCTION,
    }.get(value)


def _parse_allowlist(value: str) -> tuple[str, ...]:
    return tuple(action.strip() for action in value.split(",") if action.strip())


@dataclass(frozen=True)
class _WorkflowRunLiveDraftCommand:
    package_path: str = ""
    fixture_name: str = ""
    approval_run_id: str = ""
    connector_allowlist: tuple[str, ...] = ()
    error: str | None = None


def _parse_workflow_run_live_draft_command(prompt: str) -> _WorkflowRunLiveDraftCommand:
    args = prompt.split()
    expected = "expected /workflow-run-live-draft <path> <fixture> <approval-run-id> --allow <action>[,<action>]"
    if len(args) != 6 or args[0] != "/workflow-run-live-draft" or args[4] != "--allow":
        return _WorkflowRunLiveDraftCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[5].split(",") if action.strip())
    if not allowlist:
        return _WorkflowRunLiveDraftCommand(error=expected)
    return _WorkflowRunLiveDraftCommand(
        package_path=args[1],
        fixture_name=args[2],
        approval_run_id=args[3],
        connector_allowlist=allowlist,
    )


@dataclass(frozen=True)
class _WorkflowRunLiveReadCommand:
    package_path: str = ""
    fixture_name: str = ""
    connector_allowlist: tuple[str, ...] = ()
    error: str | None = None


def _parse_workflow_run_live_read_command(prompt: str) -> _WorkflowRunLiveReadCommand:
    args = prompt.split()
    expected = "expected /workflow-run-live-read <path> <fixture> --allow <action>[,<action>]"
    if len(args) != 5 or args[0] != "/workflow-run-live-read" or args[3] != "--allow":
        return _WorkflowRunLiveReadCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[4].split(",") if action.strip())
    if not allowlist:
        return _WorkflowRunLiveReadCommand(error=expected)
    return _WorkflowRunLiveReadCommand(
        package_path=args[1],
        fixture_name=args[2],
        connector_allowlist=allowlist,
    )


def _parse_workflow_run_live_read_to_approval_command(prompt: str) -> _WorkflowRunLiveReadCommand:
    args = prompt.split()
    expected = "expected /workflow-run-live-read-to-approval <path> <fixture> --allow <action>[,<action>]"
    if len(args) != 5 or args[0] != "/workflow-run-live-read-to-approval" or args[3] != "--allow":
        return _WorkflowRunLiveReadCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[4].split(",") if action.strip())
    if not allowlist:
        return _WorkflowRunLiveReadCommand(error=expected)
    return _WorkflowRunLiveReadCommand(
        package_path=args[1],
        fixture_name=args[2],
        connector_allowlist=allowlist,
    )


def _parse_workflow_run_live_production_command(prompt: str) -> _WorkflowRunLiveDraftCommand:
    args = prompt.split()
    expected = "expected /workflow-run-live-production <path> <fixture> <approval-run-id> --allow <action>[,<action>]"
    if len(args) != 6 or args[0] != "/workflow-run-live-production" or args[4] != "--allow":
        return _WorkflowRunLiveDraftCommand(error=expected)
    allowlist = tuple(action.strip() for action in args[5].split(",") if action.strip())
    if not allowlist:
        return _WorkflowRunLiveDraftCommand(error=expected)
    return _WorkflowRunLiveDraftCommand(
        package_path=args[1],
        fixture_name=args[2],
        approval_run_id=args[3],
        connector_allowlist=allowlist,
    )


def _is_workflow_activation_command(prompt: str) -> bool:
    return (
        prompt == "/workflow-activate"
        or prompt.startswith("/workflow-activate ")
        or prompt == "/workflow-activation-enable"
        or prompt.startswith("/workflow-activation-enable ")
        or prompt == "/workflow-activation-disable"
        or prompt.startswith("/workflow-activation-disable ")
        or prompt == "/workflow-activation-list"
        or prompt.startswith("/workflow-activation-list ")
        or prompt == "/workflow-activation-run"
        or prompt.startswith("/workflow-activation-run ")
        or prompt == "/workflow-activation-tick"
        or prompt.startswith("/workflow-activation-tick ")
        or prompt == "/workflow-activation-tick-all"
        or prompt.startswith("/workflow-activation-tick-all ")
        or prompt == "/workflow-activation-loop"
        or prompt.startswith("/workflow-activation-loop ")
        or prompt == "/workflow-activation-webhook-run"
        or prompt.startswith("/workflow-activation-webhook-run ")
        or prompt == "/workflow-activation-run-explain"
        or prompt.startswith("/workflow-activation-run-explain ")
        or prompt == "/workflow-activation-status"
        or prompt.startswith("/workflow-activation-status ")
        or prompt == "/workflow-activation-history"
        or prompt.startswith("/workflow-activation-history ")
    )


def _is_workflow_webhook_serve_command(prompt: str) -> bool:
    return prompt == "/workflow-webhook-serve" or prompt.startswith("/workflow-webhook-serve ")


def _workflow_activation_root(cwd: str | Path) -> Path:
    return Path(cwd).resolve() / ".appv24" / "workflow-activations"


def _workflow_webhook_replay_root(cwd: str | Path) -> Path:
    return Path(cwd).resolve() / ".appv24" / "webhook-replays"


def _resolve_workflow_package_path(cwd: str | Path, path_text: str) -> Path:
    base = Path(cwd).resolve()
    raw_path = Path(path_text).expanduser()
    candidate = raw_path.resolve() if raw_path.is_absolute() else (base / raw_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as error:
        raise ValueError("package path must stay inside the current working directory") from error
    return candidate


def _resolve_workflow_payload_path(cwd: str | Path, path_text: str) -> Path:
    base = Path(cwd).resolve()
    raw_path = Path(path_text).expanduser()
    candidate = raw_path.resolve() if raw_path.is_absolute() else (base / raw_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as error:
        raise ValueError("webhook payload path must stay inside the current working directory") from error
    if not candidate.is_file():
        raise ValueError(f"webhook payload file not found: {path_text}")
    return candidate


def _resolve_workflow_activation_id(value: str) -> str:
    activation_id = value.strip()
    if not activation_id:
        raise ValueError("missing activation id")
    if "/" in activation_id or "\\" in activation_id or activation_id in {".", ".."} or ".." in activation_id:
        raise ValueError("activation id must not contain path traversal")
    if not activation_id.startswith("activation-"):
        raise ValueError("activation id must start with activation-")
    return activation_id


def _resolve_webhook_replay_key(value: str) -> str:
    replay_key = value.strip()
    if not replay_key:
        raise ValueError("missing webhook replay key")
    return replay_key


def _parse_activation_loop_ticks(value: str) -> int:
    try:
        ticks = int(value)
    except ValueError as error:
        raise ValueError("activation loop ticks must be an integer") from error
    if ticks < 1 or ticks > _MAX_WORKFLOW_SERVICE_TICKS:
        raise ValueError(f"activation loop ticks must be between 1 and {_MAX_WORKFLOW_SERVICE_TICKS}")
    return ticks


def _parse_activation_loop_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as error:
        raise ValueError("activation loop interval must be a number") from error
    if interval < 0 or interval > 3600:
        raise ValueError("activation loop interval must be between 0 and 3600 seconds")
    return interval


def _parse_webhook_server_max_requests(value: str) -> int:
    try:
        max_requests = int(value)
    except ValueError as error:
        raise ValueError("webhook server max requests must be an integer") from error
    if max_requests < 1 or max_requests > _MAX_WORKFLOW_SERVICE_TICKS:
        raise ValueError(f"webhook server max requests must be between 1 and {_MAX_WORKFLOW_SERVICE_TICKS}")
    return max_requests


def _parse_webhook_server_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError("webhook server port must be an integer") from error
    if port < 0 or port > 65535:
        raise ValueError("webhook server port must be between 0 and 65535")
    return port


def _handle_workflow_save_command(app: CodingApp, profile_id: str, prompt: str) -> bool:
    if not _is_workflow_save_command(prompt):
        return False

    if not should_validate_workflow_drafts(profile_id):
        print("Workflow draft save failed: active profile is not workflow-designer-lite")
        return True

    relative_path = prompt.partition(" ")[2].strip()
    if not relative_path:
        print("Workflow draft save failed: missing relative path")
        return True

    text = assistant_text_from_messages(app.messages)
    result = validate_workflow_draft_text(text)
    if not result.ok:
        print("Workflow draft save failed: last workflow draft is not valid")
        print(format_workflow_validation_report(result))
        return True
    quality = workflow_quality_result_for_draft(result)
    if quality is not None and not quality.ok:
        print("Workflow draft save failed: last workflow draft does not pass quality gate")
        print(format_workflow_quality_report(quality))
        return True

    try:
        saved_path = save_workflow_draft_blocks(result.blocks, cwd=app.cwd, relative_path=relative_path)
    except WorkflowDraftSaveError as error:
        print(f"Workflow draft save failed: {error}")
        return True

    disk_validation = validate_workflow_package(saved_path)
    print(f"Workflow draft saved: {saved_path}")
    print(_format_workflow_package_validation_report(disk_validation))
    if disk_validation.ok:
        disk_quality = check_workflow_quality_package(saved_path)
        print(format_workflow_quality_report(disk_quality, label="Workflow package quality gate"))
    return True


def _is_workflow_save_command(prompt: str) -> bool:
    return prompt == "/workflow-save" or prompt.startswith("/workflow-save ")


def _format_workflow_package_validation_report(result, max_issues: int = 12) -> str:
    if result.ok:
        return "Workflow package validation: passed"

    visible_count = max(0, max_issues)
    lines = ["Workflow package validation: failed"]
    lines.extend(f"- {issue.code} {issue.location}: {issue.message}" for issue in result.issues[:visible_count])

    remaining = len(result.issues) - visible_count
    if remaining > 0:
        lines.append(f"- ... {remaining} more issue(s)")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
