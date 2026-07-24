"""Guild.ai-hosted Validator — governance and provenance, not the hot path.

The authoritative Validator lives in `core/evolve.py`. It *executes* candidate
patches against real replay cases and counts observed pass rates; its
`Validation.confidence` is derived from those counts and nothing here changes
that. This module is a second opinion that happens to be auditable: the same
promotion gate, hosted as a published, semver-versioned Guild agent, whose every
invocation leaves a typed execution trace we can pull back and render.

Why bother, when we already have a Validator? Because a validator nobody can
audit is worth very little. When a judge asks "what actually decided to promote
this patch, and can you show me?", `guild session events <id>` answers with
timestamps, the exact model, the exact prompt, and the exact verdict. That is
the whole reason Guild is in this system.

**Everything in this module is non-blocking by contract.** Guild agents execute
server-side, so conference wifi sits in the path of every call. Every public
function returns `None` (or an empty list) on *any* failure — timeout, network,
CLI error, unparseable output, missing binary — and never raises. The autonomy
loop must not be stoppable by a sponsor API.

Typical use, alongside the local Validator rather than instead of it::

    from core.guild_validator import validate_via_guild

    local = validator.validate(patch, graph, triggering_case, history)   # authoritative
    governed = validate_via_guild(candidate, triggering, history)        # advisory + trace
    if governed and governed["verdict"] != ("promote" if local.promotable else "reject"):
        log.info("guild validator disagreed with local validator")

Published agent: `ayushgupta4897~swarm-validator`
Agent id:        019f95a6-021f-726e-0000-3f814dd14685
See `recon/guild_impl.md` for versions, trace format, and honest scope notes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration — overridable by env so the demo machine can be re-pointed
# without a code change.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent

GUILD_BIN = os.environ.get("GUILD_BIN", "guild")
AGENT_DIR = Path(os.environ.get(
    "GUILD_AGENT_DIR", _REPO_ROOT / "guild_agents" / "swarm-validator"))
WORKSPACE = os.environ.get("GUILD_WORKSPACE", "ayushgupta4897/swarm-evolution")
AGENT_ID = os.environ.get("GUILD_AGENT_ID", "019f95a6-021f-726e-0000-3f814dd14685")

AGENT_VERSION = os.environ.get(
    "GUILD_AGENT_VERSION", "019f95ad-6ca6-cf83-0000-a97a966c97da")  # v1.1.0
"""Pin the published version we invoke, rather than whatever HEAD happens to be.

This is the point of putting the Validator on Guild at all: the verdict on a
promoted patch is attributable to a specific, immutable, server-validated build.
Set to the empty string to fall back to the locally-bundled working copy, which
is what you want while iterating on the prompt."""

STATE_DIR = Path(os.environ.get("GUILD_STATE_DIR", _REPO_ROOT / "state"))
TRACE_CACHE = STATE_DIR / "guild_trace_latest.jsonl"
VERDICT_CACHE = STATE_DIR / "guild_verdict_latest.json"
TRACE_SAMPLE = STATE_DIR / "guild_trace_sample.jsonl"
"""A real trace captured before the demo. `fetch_trace` falls back to it so the
dashboard renders genuine Guild events even with the venue wifi on fire. It is
labelled as a fallback in the return value; we do not pretend it is live."""


# ---------------------------------------------------------------------------
# Subprocess plumbing
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: float, stdin: str | None = None,
         cwd: Path | None = None) -> str | None:
    """Run a Guild CLI command. Returns stdout, or None on any failure.

    Deliberately swallows everything. The caller is an evolution loop that must
    keep cranking; a Guild outage is a missing trace, not a stopped experiment.
    """
    if shutil.which(GUILD_BIN) is None and not Path(GUILD_BIN).exists():
        log.warning("guild CLI not found at %r — skipping", GUILD_BIN)
        return None

    try:
        proc = subprocess.run(
            [GUILD_BIN, *args],
            input=stdin,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("guild %s timed out after %ss", args[0] if args else "?", timeout)
        return None
    except OSError as exc:
        log.warning("guild invocation failed: %s", exc)
        return None

    if proc.returncode != 0:
        log.warning("guild %s exited %s: %s",
                    " ".join(args[:2]), proc.returncode,
                    (proc.stderr or "").strip()[-400:])
        return None

    return proc.stdout


def _write_cache(path: Path, text: str) -> None:
    """Best-effort cache write. A failed cache write must never surface."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError as exc:
        log.debug("could not write cache %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {"fixes_new_failure", "regression_risk", "verdict", "reasoning"}


def _parse_test_output(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    """Pull the verdict object and the session id out of `guild agent test`.

    The CLI prints a short banner (`✓ Session: <uuid>`) and then each agent
    message on a line prefixed with `< `. We scan every line rather than
    assuming a position, because banner text has changed between CLI releases
    and a positional parse would break silently on the next one.

    Returns (verdict, session_id); either may be None.
    """
    session_id: str | None = None
    verdict: dict[str, Any] | None = None

    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue

        if "Session:" in line:
            session_id = line.split("Session:", 1)[1].strip()
            continue

        candidate = line[2:].strip() if line.startswith("< ") else line
        if not candidate.startswith("{"):
            continue

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict):
            continue
        if "error" in parsed and not _REQUIRED_KEYS <= parsed.keys():
            log.warning("guild agent returned an error: %s", parsed["error"])
            continue
        if _REQUIRED_KEYS <= parsed.keys():
            verdict = parsed

    return verdict, session_id


def _normalise(verdict: dict[str, Any]) -> dict[str, Any]:
    """Coerce the verdict into the shape the rest of the system expects.

    Re-derives `verdict` from the evidence rather than trusting the field. The
    gate is already enforced inside the agent; doing it again here costs
    nothing and means a future prompt edit cannot quietly widen the gate. This
    mirrors `Validation.promotable` in core/schemas.py: fixes the new failure
    AND introduces no regression.
    """
    risks = verdict.get("regression_risk") or []
    if not isinstance(risks, list):
        risks = []

    clean_risks = [
        {
            "persona_id": str(r.get("persona_id", "")),
            "would_regress": bool(r.get("would_regress")),
            "reasoning": str(r.get("reasoning", "")),
        }
        for r in risks if isinstance(r, dict)
    ]

    fixes = bool(verdict.get("fixes_new_failure"))
    regressions = sum(1 for r in clean_risks if r["would_regress"])
    gated = "promote" if (fixes and regressions == 0) else "reject"

    return {
        "fixes_new_failure": fixes,
        "regression_risk": clean_risks,
        "verdict": gated,
        "reasoning": str(verdict.get("reasoning", "")),
        "regressions_predicted": regressions,
        "historical_cases_considered": len(clean_risks),
        "model_verdict": verdict.get("verdict"),
        "source": "guild",
        "agent_id": AGENT_ID,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_via_guild(candidate: dict[str, Any],
                       triggering: dict[str, Any],
                       history: list[dict[str, Any]],
                       timeout: float = 60.0) -> dict[str, Any] | None:
    """Ask the hosted Guild validator for a verdict on one candidate patch.

    `candidate`  — {operation, target, diff, reflection}
    `triggering` — {caller_utterance, agent_utterance, ground_truth, failure_type}
    `history`    — [{persona_id, caller_utterance, expected_behaviour}, ...]

    Returns a dict with `fixes_new_failure`, `regression_risk`, `verdict`,
    `reasoning`, plus `session_id` and bookkeeping — or **None on any failure**.
    Never raises. Callers should treat None as "Guild had nothing to say", not
    as a rejection.

    `timeout` is a wall clock on the whole round trip: session create, remote
    dispatch, inference, response. Measured at ~38s against the published
    version and ~67s against a locally-uploaded bundle, so 60s is a real
    default for the published path and too tight for the bundle path. This is
    slow enough that it must not sit inline in the generation loop — call it
    alongside the local Validator, not in front of it.
    """
    payload = {
        "candidate": {
            "operation": str(candidate.get("operation", "")),
            "target": str(candidate.get("target", "")),
            "diff": str(candidate.get("diff", "")),
            "reflection": str(candidate.get("reflection", "")),
        },
        "triggering_failure": {
            "caller_utterance": str(triggering.get("caller_utterance", "")),
            "agent_utterance": str(triggering.get("agent_utterance", "")),
            "ground_truth": str(triggering.get("ground_truth", "")),
            "failure_type": str(triggering.get("failure_type", "")),
        },
        "historical_cases": [
            {
                "persona_id": str(c.get("persona_id", "")),
                "caller_utterance": str(c.get("caller_utterance", "")),
                "expected_behaviour": str(c.get("expected_behaviour", "")),
            }
            for c in (history or []) if isinstance(c, dict)
        ],
    }

    args = ["--mode", "json", "agent", "test",
            "--workspace", WORKSPACE,
            "--timeout", str(int(timeout))]

    bundle = AGENT_DIR / "agent.js.gz"
    if AGENT_VERSION:
        # Invoke the published, immutable version. Also the fastest path: the
        # server build is already cached, so there is no per-call bundle
        # upload+validate (measured ~38s vs ~67s for the bundle path).
        args += ["--agent-version", AGENT_VERSION]
    elif bundle.exists():
        # Working-copy path, for iterating on the prompt before publishing.
        # Built by `npm run bundle` in the agent directory.
        args += ["--bundle", str(bundle)]

    started = time.time()
    stdout = _run(args, timeout=timeout, stdin=json.dumps(payload), cwd=AGENT_DIR)
    if stdout is None:
        return None

    verdict, session_id = _parse_test_output(stdout)
    if verdict is None:
        log.warning("guild validator returned no parseable verdict")
        return None

    result = _normalise(verdict)
    result["session_id"] = session_id
    result["agent_version"] = AGENT_VERSION or "working-copy"
    result["elapsed_s"] = round(time.time() - started, 2)

    _write_cache(VERDICT_CACHE, json.dumps(result, indent=2))
    return result


def fetch_trace(session_id: str,
                timeout: float = 30.0,
                events: str = "all",
                limit: int = 200,
                allow_fallback: bool = True) -> list[dict[str, Any]]:
    """Pull the typed execution trace for a Guild session.

    Returns a list of event dicts sorted by `created_at`, each carrying at
    least `type`, `id`, `created_at`. Interesting types: `llm_start`,
    `llm_done`, `agent_console`, `runtime_start`, `runtime_done`,
    `user_message`.

    On success the trace is cached to `state/guild_trace_latest.jsonl`. On
    failure this returns the last cached trace, then the checked-in sample, then
    `[]` — every returned event is real, but each is tagged with
    `_guild_source` so the dashboard can label live data as live and cached data
    as cached. Never raises.
    """
    if session_id:
        stdout = _run(
            ["--mode", "json", "session", "events", session_id,
             "--events", events, "--limit", str(limit)],
            timeout=timeout,
        )
        if stdout is not None:
            events_list = _parse_events(stdout)
            if events_list:
                _write_cache(
                    TRACE_CACHE,
                    "\n".join(json.dumps(e, separators=(",", ":"))
                              for e in events_list) + "\n",
                )
                return _tag(events_list, "live")

    if not allow_fallback:
        return []

    for path, label in ((TRACE_CACHE, "cached"), (TRACE_SAMPLE, "sample")):
        cached = _read_jsonl(path)
        if cached:
            log.info("guild trace unavailable; serving %s trace from %s",
                     label, path.name)
            return _tag(cached, label)

    return []


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    """`session events` emits one JSON document with `items` + `pagination`.

    Despite `--mode jsonl` being advertised, CLI 0.17.0 pretty-prints a single
    object for this command, so we parse the document rather than the lines.
    A line-oriented fallback is kept in case a later release starts honouring
    the flag.
    """
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError:
        items = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return sorted(items, key=lambda e: e.get("created_at", ""))

    if isinstance(doc, dict):
        items = doc.get("items")
    elif isinstance(doc, list):
        items = doc
    else:
        items = None

    if not isinstance(items, list):
        return []

    return sorted([e for e in items if isinstance(e, dict)],
                  key=lambda e: e.get("created_at", ""))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return sorted(out, key=lambda e: e.get("created_at", ""))


def _tag(events: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    for e in events:
        e["_guild_source"] = source
    return events


def summarise_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce a raw trace to the handful of numbers a dashboard tile wants.

    Kept here rather than in the dashboard so there is exactly one place that
    knows Guild's event vocabulary.
    """
    if not events:
        return {"available": False}

    by_type: dict[str, int] = {}
    for e in events:
        by_type[str(e.get("type"))] = by_type.get(str(e.get("type")), 0) + 1

    llm_start = next((e for e in events if e.get("type") == "llm_start"), None)
    llm_done = next((e for e in events if e.get("type") == "llm_done"), None)

    latency_ms = None
    if llm_start and llm_done:
        try:
            from datetime import datetime
            t0 = datetime.fromisoformat(llm_start["created_at"])
            t1 = datetime.fromisoformat(llm_done["created_at"])
            latency_ms = int((t1 - t0).total_seconds() * 1000)
        except (KeyError, ValueError, TypeError):
            latency_ms = None

    console = [
        {"level": e.get("level"), "message": e.get("content")}
        for e in events if e.get("type") == "agent_console"
    ]

    return {
        "available": True,
        "source": events[0].get("_guild_source", "unknown"),
        "event_count": len(events),
        "event_types": by_type,
        "started_at": events[0].get("created_at"),
        "ended_at": events[-1].get("created_at"),
        "llm_provider": (llm_start or {}).get("provider"),
        "llm_model": (llm_start or {}).get("model"),
        "llm_latency_ms": latency_ms,
        "console": console,
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    example = AGENT_DIR / "examples" / "request.json"
    req = json.loads(example.read_text())

    print("invoking hosted guild validator ...")
    out = validate_via_guild(req["candidate"], req["triggering_failure"],
                             req["historical_cases"], timeout=180)
    if out is None:
        print("guild unavailable — loop would continue on the local validator")
    else:
        print(json.dumps(out, indent=2)[:2000])
        trace = fetch_trace(out.get("session_id") or "")
        print(json.dumps(summarise_trace(trace), indent=2)[:2000])
