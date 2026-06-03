"""
Model abstraction layer — swap any AI provider via config.yaml.
Uses LiteLLM under the hood so the rest of the code never changes.
"""

import yaml
import litellm
from pathlib import Path
from dotenv import load_dotenv

# Module-level load so CLI commands that don't instantiate ModelProvider
# still get keys in the environment on import.
load_dotenv()
load_dotenv(Path.home() / ".memoria" / ".env")

# Suppress LiteLLM's verbose logging
litellm.suppress_debug_info = True


class ModelProvider:
    """
    Single interface for all AI providers.
    Change the model in config.yaml — nothing else needs to change.
    Both config.yaml and .env are re-read on every instantiation so changes
    take effect immediately without restarting the server.
    """

    def __init__(self, config_path: str = "config.yaml"):
        # Reload .env on every instantiation (override=True so changed values
        # replace what was loaded at import time).
        load_dotenv(override=True)
        load_dotenv(Path.home() / ".memoria" / ".env", override=True)
        self.config = self._load_config(config_path)
        self.model = self.config.get("model", "")
        if not self.model:
            raise ValueError(
                "No model configured. "
                "Either run memoria from your project directory (where config.yaml lives), "
                "or pass --config /path/to/config.yaml"
            )
        self.max_tokens = self.config.get("max_tokens_output", 4000)
        # Cumulative token usage across all calls in this session
        self.tokens_in  = 0
        self.tokens_out = 0

    def _load_config(self, config_path: str) -> dict:
        # 1. Try the given path directly
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8-sig") as f:
                return yaml.safe_load(f) or {}

        # 2. Walk up the directory tree from CWD (like git finds .git)
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            candidate = parent / "config.yaml"
            if candidate.exists():
                # Only accept it if it looks like a Memoria config
                try:
                    with open(candidate, "r", encoding="utf-8-sig") as f:
                        data = yaml.safe_load(f) or {}
                    if "model" in data or "books_dir" in data or "ignore_dirs" in data:
                        return data
                except Exception:
                    pass

        # 3. User-level config at ~/.memoria/config.yaml
        user_config = Path.home() / ".memoria" / "config.yaml"
        if user_config.exists():
            with open(user_config, "r", encoding="utf-8-sig") as f:
                return yaml.safe_load(f) or {}

        return {}

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a prompt, get a response. Works for any provider.

        Raises categorised RuntimeError so callers can handle each case:
          [AUTH]      — bad API key / credentials (do not retry)
          [RATELIMIT] — 429 / quota (retry with backoff)
          [TRANSIENT] — 502/503 / connection drop (retry once)
          [ERROR]     — anything else (do not retry)
        """
        try:
            # Ollama (local) gets a longer timeout — large models are slow
            timeout = 600 if "ollama" in self.model.lower() else 300
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self.max_tokens,
                timeout=timeout,
            )
            usage = getattr(response, "usage", None)
            if usage:
                self.tokens_in  += getattr(usage, "prompt_tokens",     0)
                self.tokens_out += getattr(usage, "completion_tokens",  0)
            return response.choices[0].message.content or ""
        except Exception as e:
            msg = str(e)
            low = msg.lower()

            # ── Auth errors — wrong key, missing creds, forbidden ──────
            auth_signals = [
                "401", "403",
                "authenticationerror", "authentication error",
                "invalid api key", "api key not valid", "incorrect api key",
                "unauthorized", "permission denied", "access denied",
                "invalidsignatureexception", "unrecognizedclientexception",
                "nosuchbucket", "credentials",
                "no auth", "no credentials", "credentialserror",
            ]
            if any(s in low for s in auth_signals):
                raise RuntimeError(f"[AUTH] {msg}")

            # ── Rate limit / quota ──────────────────────────────────────
            rate_signals = ["429", "rate limit", "rate_limit", "quota", "too many requests",
                            "resource_exhausted", "resourceexhausted"]
            if any(s in low for s in rate_signals):
                raise RuntimeError(f"[RATELIMIT] {msg}")

            # ── Transient provider errors ───────────────────────────────
            transient_signals = ["502", "503", "overloaded", "unavailable",
                                  "connection", "disconnected", "timeout", "timed out"]
            if any(s in low for s in transient_signals):
                raise RuntimeError(f"[TRANSIENT] {msg}")

            raise RuntimeError(f"[ERROR] {msg}")

    async def async_complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Async version of complete(). Safe to await inside FastAPI and
        asyncio.gather() for parallel sub-unit Memory Bank generation.
        Same error categorisation as complete().
        """
        try:
            timeout = 600 if "ollama" in self.model.lower() else 300
            response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self.max_tokens,
                timeout=timeout,
            )
            usage = getattr(response, "usage", None)
            if usage:
                self.tokens_in  += getattr(usage, "prompt_tokens",     0)
                self.tokens_out += getattr(usage, "completion_tokens",  0)
            return response.choices[0].message.content or ""
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            auth_signals = [
                "401", "403",
                "authenticationerror", "authentication error",
                "invalid api key", "api key not valid", "incorrect api key",
                "unauthorized", "permission denied", "access denied",
                "invalidsignatureexception", "unrecognizedclientexception",
                "nosuchbucket", "credentials",
                "no auth", "no credentials", "credentialserror",
            ]
            if any(s in low for s in auth_signals):
                raise RuntimeError(f"[AUTH] {msg}")
            rate_signals = ["429", "rate limit", "rate_limit", "quota", "too many requests",
                            "resource_exhausted", "resourceexhausted"]
            if any(s in low for s in rate_signals):
                raise RuntimeError(f"[RATELIMIT] {msg}")
            transient_signals = ["502", "503", "overloaded", "unavailable",
                                  "connection", "disconnected", "timeout", "timed out"]
            if any(s in low for s in transient_signals):
                raise RuntimeError(f"[TRANSIENT] {msg}")
            raise RuntimeError(f"[ERROR] {msg}")

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def usage_summary(self) -> str:
        """Human-readable token usage string."""
        if self.tokens_total == 0:
            return ""
        return (
            f"{self.tokens_total:,} tokens "
            f"[dim](↑{self.tokens_in:,} in · ↓{self.tokens_out:,} out)[/dim]"
        )

    @property
    def provider_name(self) -> str:
        """Human-readable provider name for display."""
        model = self.model.lower()
        if "claude" in model:
            return "Anthropic Claude"
        elif "gpt" in model:
            return "OpenAI"
        elif "bedrock" in model:
            return "AWS Bedrock"
        elif "gemini" in model:
            return "Google Gemini"
        elif "ollama" in model:
            return "Ollama (Local)"
        return "AI Provider"
