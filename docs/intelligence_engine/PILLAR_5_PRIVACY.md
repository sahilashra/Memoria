# Pillar 5 — Privacy, Security & Edge Deployment

> **The "Trust Layer"** — because tracking user behaviour introduces severe
> privacy risks, every design decision defaults to the most restrictive option.
> Coverage today: **60%** | Status: 🔶 Partial

---

## Goal

Ensure the Intelligence Engine is trustworthy enough to run on a user's
primary work machine, handling sensitive corporate data. Every byte of
telemetry must be:
1. Processed locally by default
2. Scrubbed of PII before storage
3. Accessible and deletable by the user at any time
4. Never executed as automation without explicit one-click confirmation

---

## What Already Exists

### Local-first architecture
- All Memoria data lives in `~/.memoria/` — SQLite DB, Memory Banks, activity
  log, graph JSON. Nothing is sent to any cloud service.
- `tracker.py`: explicit `--export` flag required to share data; no
  auto-upload path exists.

### Local LLM support
- LiteLLM + Ollama integration already supports fully local inference:
  `model: ollama/llama3` in `config.yaml` → zero cloud calls.

### Access control
- `rbac.py` — four roles, six permissions, per-project scoping,
  `policy.yaml`-driven.

### Companion threshold gate
- `companion.py` — `min_score` parameter prevents surfacing low-confidence
  results. User can tune or disable.

---

## What Is Missing

| Gap | Description | Effort |
|-----|-------------|--------|
| NER-based PII masking pipeline | Local spaCy / Presidio NER run on every captured event before it is written to the DB. Never optional — always on. | Medium |
| Quantized local SLM deployment | Bundle a quantized small language model (e.g. Phi-3-mini / Gemma-2B via llama.cpp or CoreML/DirectML) so inference works even without Ollama installed | Large |
| User-in-the-loop automation governance | Hard confirmation gate before any generated script executes: show what the script will do, require explicit one-click "Run" per step | Medium |
| Data retention controls | Per-event TTL; `--clear-after 30d` flag; GDPR-style delete-all command | Small |
| Telemetry audit dashboard | Simple UI showing: what was captured today, what was masked, what was sent to the LLM | Medium |
| Encryption at rest | Optional AES-256 encryption of `activity.db` and `interactions` table (key stored in OS keychain) | Medium |

---

## Architecture Decisions (Resolved ✅)

> **D2 →** Local forever. `sync_allowed: false`. DPAPI (Windows) / Keychain (macOS) encryption so even IT admins cannot read `activity.db` without the developer's OS session.
>
> **D3 →** Windows + macOS. DPAPI + win32crypt on Windows, `keyring` + Keychain on macOS, `secretstorage` on Linux.
>
> **D5 →** Accessibility API primary, local Ollama VLM fallback. Screenshots are opt-in, auto-deleted after 7 days, blocked for password manager / payroll apps.

## Architecture Decision

**Secrets scanner on every write path + DPAPI/Keychain encryption: both are hard requirements, not nice-to-haves. Fail closed on privacy.**

The two blockers for any corporate deployment are: (1) a leaked AWS key in `activity.db` is a security incident; (2) an IT admin reading `activity.db` without developer consent violates the privacy contract. Both are solved architecturally — not by policy.

**Fail closed principle:** if the secrets scanner throws an exception, the event is dropped, not written unmasked. If encryption setup fails at first run, the daemon refuses to start rather than writing plaintext.

## Data Residency Design

### Secrets Scanner — Runs Synchronously Before Every INSERT

```python
SECRETS_PATTERNS = [
    (r'\bAKIA[0-9A-Z]{16}\b',
     '[REDACTED_AWS_KEY_ID]'),
    (r'(?i)aws_secret[_\s]*=\s*["\']?([A-Za-z0-9/+=]{40})["\']?',
     '[REDACTED_AWS_SECRET]'),
    (r'\bghp_[A-Za-z0-9]{36,}\b',
     '[REDACTED_GH_PAT]'),
    (r'\bgithub_pat_[A-Za-z0-9_]{82,}\b',
     '[REDACTED_GH_PAT]'),
    (r'(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}',
     'Bearer [REDACTED_TOKEN]'),
    (r'(?i)(postgres|mysql|mongodb)://[^\s\'"]+:[^\s\'"@]+@',
     '[REDACTED_DB_CONNSTR]://'),
    (r'\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b',
     '[REDACTED_JWT]'),
    (r'(?i)(SECRET|PASSWORD|TOKEN|API_KEY)\s*=\s*["\']?([^\s"\']{8,})["\']?',
     r'\1=[REDACTED_SECRET]'),
    (r'-----BEGIN [^\-]+PRIVATE KEY-----[\s\S]+?-----END [^\-]+PRIVATE KEY-----',
     '[REDACTED_PRIVATE_KEY]'),
]
```

Typed redaction labels (`[REDACTED_AWS_KEY_ID]`) are semantically useful to LLM context without being reversible. A compiled multi-pattern scan over a 4KB clipboard payload runs in <2ms.

### Screenshot App Blocklist

Screenshots are **disabled by default**. When enabled, these apps are always blocked:

```python
SCREENSHOT_BLOCKED_APPS = {
    # process names
    "1password", "keepass", "keepassxc", "bitwarden", "dashlane",
    "keychain access", "gnome-keyring",
    # window title substrings (case-insensitive match)
    "password", "master password", "private key", "ssh key",
    "payroll", "performance review", "salary",
}
```

If a screenshot IS stored: auto-delete after `screenshot_retention_days` (default: 7). The image is discarded after local OCR text extraction — never persisted raw.

### `policy.yaml` Schema Extension

```yaml
privacy:
  retention_days: 30
  screenshot_enabled: false          # developer opt-in only; org cannot force true
  screenshot_retention_days: 7
  blocked_apps:
    - "Workday"
    - "BambooHR"
  secrets_scanning: required         # 'required'|'optional'|'off' — org can force 'required'
  shell_command_capture: true
  clipboard_capture: true

audit:
  event_log_path: "~/.memoria/audit.log"
  log_what_was_sent_to_llm: true     # prompt payload hash + token count per call
  developer_viewable: true           # `memoria audit show` exposes last N events

personal_time:
  enabled: true
  windows:
    - { days: "Mon-Fri", start: "12:00", end: "13:00" }  # lunch
    - { days: "Mon-Sun", start: "18:00", end: "08:00" }  # evenings

data_sovereignty:
  sync_allowed: false                # hard-coded false in v1; no code path to override
  central_db_path: null              # if non-null, daemon startup error + user alert
```

**Verification:** `sync_allowed: false` has no code path that sends data anywhere. A CISO can verify by running `lsof -i` — the daemon makes zero external TCP connections.

## Local Model Deployment Strategy

### DPAPI/Keychain Encryption (Hard Requirement)

`activity.db` is encrypted with a key derived from the developer's OS login credentials — an IT admin with filesystem access gets an encrypted blob they cannot open without the developer's active session:

```python
import platform, secrets

def get_db_encryption_key() -> bytes:
    """Retrieve or create DB key from OS credential store. Fail closed if unavailable."""
    system = platform.system()
    if system == "Windows":
        import win32crypt
        stored = _read_or_generate_stored_key()
        return win32crypt.CryptUnprotectData(stored, None, None, None, 0)[1]
    elif system == "Darwin":
        import keyring
        key = keyring.get_password("memoria", "db_key")
        if not key:
            key = secrets.token_hex(32)
            keyring.set_password("memoria", "db_key", key)
        return key.encode()
    else:
        # Linux: secretstorage (libsecret)
        import secretstorage
        with secretstorage.dbus_init() as conn:
            collection = secretstorage.get_default_collection(conn)
            items = list(collection.search_items({"application": "memoria"}))
            if items:
                return items[0].get_secret()
            key = secrets.token_bytes(32)
            collection.create_item("memoria db key", {"application": "memoria"}, key)
            return key
```

### V1 Must-Have Checklist (Blocking for Any Corporate Deployment)

| Requirement | Status | Why It Blocks |
|-------------|--------|---------------|
| Secrets scanner on every write path | 🔴 Not built | A captured AWS key in `activity.db` is a security incident |
| DPAPI/Keychain encryption of `activity.db` | 🔴 Not built | IT admin access to plaintext SQLite is a privacy violation |
| Screenshot opt-in + app blocklist | 🔴 Not built | Password manager screenshots are an immediate blocker |
| `memoria audit show` command | 🔴 Not built | Without it, users cannot verify the tool is "for me, not about me" |

**Nice-to-have for v1, required for v2:**
- Personal time windows (capture pause schedule)
- Automated retention purge (`--clear-after 30d`)
- CISO audit report export (`memoria audit export --format pdf`)
- Per-field capture toggles (disable clipboard but keep shell commands)

---

## Privacy Principles (non-negotiable)

These apply regardless of how D2 is decided:

1. **Capture-mask-store** — PII scrubbing happens synchronously before any event
   is written to disk. The original unmasked text is never persisted.
2. **No silent upload** — no telemetry, no analytics, no crash reports leave
   the machine unless the user initiates an explicit export action.
3. **Automation requires confirmation** — generated scripts are never
   auto-executed. Every execution requires a visible "Run" click.
4. **Full delete** — `memoria intelligence clear` wipes the entire
   `interactions` table, session history, workflow registry, and generated
   scripts.
5. **Scope limiting** — users can exclude specific apps, file patterns, or
   time windows from capture entirely.

---

## Proposed Consent & Governance Model

```
First run:
  ┌────────────────────────────────────────────────────────────┐
  │  Memoria Intelligence Engine — Privacy Setup               │
  │                                                            │
  │  To learn your workflows, Memoria needs to observe:        │
  │  ☑ Active window titles                                    │
  │  ☑ Clipboard content (masked for PII automatically)        │
  │  ☐ Keyboard input (disabled — enable for full workflow map) │
  │  ☐ Screenshots (disabled — enable for UI-aware automation) │
  │                                                            │
  │  All data stays on this machine. You can review, export,   │
  │  or delete everything at any time.                         │
  │                                                            │
  │  [ Configure ]  [ Enable with defaults ]  [ Skip ]         │
  └────────────────────────────────────────────────────────────┘

Before each automation run:
  ┌────────────────────────────────────────────────────────────┐
  │  ▶ Run: "Weekly report migration to CRM"                   │
  │                                                            │
  │  This script will:                                         │
  │  1. Open Chrome → navigate to CRM form                     │
  │  2. Paste clipboard content into Revenue Forecast          │
  │  3. Click Submit                                           │
  │                                                            │
  │  [ Preview Script ]  [ Run Now ]  [ Cancel ]               │
  └────────────────────────────────────────────────────────────┘
```

---

## Quantized Local SLM Options (for offline inference)

| Model | Size (quantized) | VRAM | Runner | Notes |
|-------|-----------------|------|--------|-------|
| Phi-3-mini (4K) | ~2.2GB (Q4) | 3GB | llama.cpp / Ollama | Fast, good at structured output |
| Gemma-2B | ~1.4GB (Q4) | 2GB | llama.cpp | Very small, weaker reasoning |
| Llava-1.6 (7B) | ~4GB (Q4) | 6GB | Ollama | VLM — needed for screenshots |
| UI-TARS (7B) | ~4GB (Q4) | 6GB | llama.cpp | Specialised for UI understanding |

---

## Effort Estimate

| Component | Days |
|-----------|------|
| NER PII masking pipeline | 2–3 |
| User consent setup flow | 2 |
| Automation confirmation gate UI | 2 |
| Data retention controls (TTL, clear) | 1–2 |
| Telemetry audit dashboard | 3 |
| Encryption at rest (optional) | 2–3 |
| Quantized SLM packaging (Phi-3 / llava) | 4–5 |
| **Total** | **16–18 days** |
