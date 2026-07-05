from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = ROOT / ".env"

# Default workhorse — cheap, good enough for structured JSON and edits.
DEFAULT_MODEL = "gpt-4o-mini"
# Deep reasoning — only for tasks where mini often fails (full draft, full plan).
DEFAULT_PREMIUM_MODEL = "gpt-5.4-nano"

# Actions that need the premium model. Everything else uses AI_MODEL (mini).
PREMIUM_ACTIONS = frozenset(
    {
        "ai_pipeline_draft",  # Daily Brief: full author draft from rich context
        "content_plan_full",  # Generate/rebuild the whole week content plan
    }
)


@dataclass(frozen=True)
class AIGatewayConfig:
    api_key: str
    base_url: str
    model: str
    premium_model: str

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class AIGatewayError(RuntimeError):
    pass


class AIGateway:
    """Single entry point for all model calls."""

    def __init__(self, config: AIGatewayConfig | None = None) -> None:
        self.config = config or load_ai_config()

    def is_configured(self) -> bool:
        return self.config.is_configured

    def model_for(self, action: str | None = None) -> str:
        return resolve_model_for_action(self.config, action)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        action: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.is_configured:
            raise AIGatewayError("ProxyAPI не настроен.")

        chosen_model = model or resolve_model_for_action(self.config, action)
        payload = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        response = self._post_chat_completions(payload)
        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        try:
            parsed = json.loads(str(content))
        except json.JSONDecodeError as exc:
            raise AIGatewayError("AI вернул ответ не в JSON-формате.") from exc
        if not isinstance(parsed, dict):
            raise AIGatewayError("AI вернул неподдерживаемый формат ответа.")
        return parsed

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise AIGatewayError(f"ProxyAPI вернул ошибку {exc.code}: {detail}") from exc
        except OSError as exc:
            raise AIGatewayError(f"ProxyAPI недоступен: {exc}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIGatewayError("ProxyAPI вернул ответ не в JSON-формате.") from exc
        if not isinstance(parsed, dict):
            raise AIGatewayError("ProxyAPI вернул неподдерживаемый формат ответа.")
        return parsed


def resolve_model_for_action(config: AIGatewayConfig, action: str | None = None) -> str:
    if action and action in PREMIUM_ACTIONS:
        return config.premium_model or config.model
    return config.model


def load_ai_config(env_path: Path = DEFAULT_ENV_PATH) -> AIGatewayConfig:
    values = _read_env_file(env_path)
    model = values.get("AI_MODEL", os.environ.get("AI_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    premium = values.get("AI_PREMIUM_MODEL", os.environ.get("AI_PREMIUM_MODEL", "")).strip()
    # Legacy: single AI_MODEL pointed at an expensive model — split into mini + premium.
    if not premium and _looks_premium_model(model):
        premium = model
        model = DEFAULT_MODEL
    if not premium:
        premium = model
    return AIGatewayConfig(
        api_key=values.get("PROXY_API_KEY", os.environ.get("PROXY_API_KEY", "")).strip(),
        base_url=values.get("PROXY_API_BASE_URL", os.environ.get("PROXY_API_BASE_URL", "")).strip(),
        model=model,
        premium_model=premium,
    )


def _looks_premium_model(name: str) -> bool:
    lowered = name.lower()
    if "mini" in lowered:
        return False
    return any(marker in lowered for marker in ("5.4", "5-4", "o1", "opus", "nano"))


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
