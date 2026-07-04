from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class AIGatewayConfig:
    api_key: str
    base_url: str
    model: str

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

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.config.is_configured:
            raise AIGatewayError("ProxyAPI не настроен.")

        payload = {
            "model": self.config.model,
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


def load_ai_config(env_path: Path = DEFAULT_ENV_PATH) -> AIGatewayConfig:
    values = _read_env_file(env_path)
    return AIGatewayConfig(
        api_key=values.get("PROXY_API_KEY", os.environ.get("PROXY_API_KEY", "")).strip(),
        base_url=values.get("PROXY_API_BASE_URL", os.environ.get("PROXY_API_BASE_URL", "")).strip(),
        model=values.get("AI_MODEL", os.environ.get("AI_MODEL", "")).strip(),
    )


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
