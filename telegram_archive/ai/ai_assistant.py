"""Archive X local AI assistant.

Inference is performed by a local llama.cpp local compatible server on the
loopback interface. The model receives bounded application context and a small,
read-only tool registry; it never receives arbitrary SQL, shell, filesystem, or
Telegram capabilities.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import json
import os
import time
from typing import Any

from .. import config
from .ai_safety import redact_text
from .ai_tools import AIToolRegistry
from .local_ai_runtime import DEFAULT_MODEL_ID, LocalAIError, LocalAIRuntime

SYSTEM_INSTRUCTIONS = """You are Archive X Local AI, a private technical and library co-pilot running entirely on the user's machine.
Use only the supplied Archive X context and the explicitly available read-only tools.
Never invent library, job, diagnostic, metadata, or playback facts.
Never request or perform arbitrary SQL, shell commands, filesystem access, network access, Telegram writes, or destructive actions.
The published-media boundary is authoritative: do not claim an unpublished video is available to consumer pages.
Unknown Performer and Unknown Studio are valid protected classifications; never suggest silently overwriting protected manual overrides.
For mutations, explain the proposed change and direct the operator to the existing Archive X workflow. Long-running mutations must go through the normal JobManager and are not executed directly by the model.
Keep answers concise, concrete, and evidence-based. State uncertainty when local evidence is insufficient.
To explain how Archive X works, call search_knowledge first and answer from what it returns. To look for problems, use get_diagnostics_summary or search_logs.
When you find a real problem or an idea, call list_findings first, then record_finding once (never include keys, tokens or passwords). Do not repeat a rejected finding.
Your job is to DIAGNOSE, not to fix: find what is wrong and say where the evidence is, how often it happens and what to check next. Do not write code, patches, commands or settings changes, and do not claim to have fixed or changed anything.
To look for problems, call get_issue_digest first: it is a short ranked list made from the logs, so read it whole instead of searching logs line by line. Use search_logs or get_job only to follow up one issue from the digest.
Answer in short plain sentences. For each problem: what it is, the evidence (quote one log line), how often and when, the usual cause from the digest, and the one thing to check next. Say "not enough evidence" rather than guessing.
"""


# LocalAIError is the runtime's exception class (imported above). A local
# redefinition here used to shadow it, so routes catching this module's class
# missed runtime connection errors and returned HTTP 500 instead of 502.


class ArchiveXAI:
    """Embedded assistant: local runtime control, investigations, chat and tool calls."""
    def __init__(self, db, jobs=None, governor=None):
        self.db = db
        self.jobs = jobs
        self.governor = governor
        self.runtime = LocalAIRuntime(db)
        self.tools = AIToolRegistry(db, jobs=jobs, governor=governor)

    @property
    def model(self) -> str:
        return self.runtime.model_id

    def configured(self) -> bool:
        state = self.runtime.status()
        return bool(state["runtime"]["exists"] and state["model"]["exists"])

    def masked_status(self) -> dict[str, Any]:
        inv = self.db.ai_active_investigation()
        state = self.runtime.status()
        return {
            "configured": bool(state["ready"]),
            "local_only": True,
            "provider": "llama.cpp",
            "model": self.model,
            "conversation_id": str(inv.get("local_session_id") or ""),
            "investigation": inv,
            "runtime": state,
        }

    def configure(self, **kwargs) -> dict[str, Any]:
        self.runtime.configure(**kwargs)
        return self.masked_status()

    def start(self) -> dict[str, Any]:
        try:
            return self.runtime.start()
        except LocalAIError as exc:
            raise LocalAIError(str(exc)) from exc

    def stop(self) -> dict[str, Any]:
        return self.runtime.stop()

    def test(self) -> dict[str, Any]:
        try:
            result = self.runtime.test()
        except LocalAIError as exc:
            raise LocalAIError(str(exc)) from exc
        self.db.log("Local AI inference test completed", event="ai.inference.completed", details={"model": result.get("model"), "latency_ms": result.get("latency_ms"), "test": True})
        return result

    def active_investigation(self) -> dict[str, Any]:
        return self.db.ai_active_investigation()

    def _session_id(self, investigation_id: int | None = None) -> tuple[str, int]:
        inv = self.db.ai_get_investigation(investigation_id) if investigation_id else self.active_investigation()
        if not inv:
            inv = self.active_investigation()
        iid = int(inv["id"])
        session_id = str(inv.get("local_session_id") or "").strip()
        if not session_id:
            session_id = f"local-ai-{iid}"
            self.db.ai_update_investigation(iid, local_session_id=session_id)
        return session_id, iid

    def reset_conversation(self, investigation_id: int | None = None) -> dict[str, Any]:
        _, iid = self._session_id(investigation_id)
        self.db.ai_clear_chat_messages(iid)
        session_id = f"local-ai-{iid}-{int(time.time())}"
        self.db.ai_update_investigation(iid, local_session_id=session_id, stage="OBSERVE")
        return {"ok": True, "conversation_id": session_id, "investigation_id": iid}

    @staticmethod
    def _extract_message(response: dict[str, Any]) -> dict[str, Any]:
        try:
            message = response["choices"][0]["message"]
            return message if isinstance(message, dict) else {}
        except Exception:
            return {}

    def _bounded_context(self, context: str) -> str:
        """The snapshot has to fit in the model's window with room left for the question, the tools and the answer (about 3 characters a token)."""
        budget = max(3000, (self.runtime.context_size - 2600) * 3)
        return redact_text(context)[:min(42000, budget)]      # secrets are removed before the snapshot is cut to size

    def ensure_running(self, on_status=None) -> None:
        """Start the model if it is set up but not running, so asking a question is enough. Raises LocalAIError with plain words when it cannot."""
        state = self.runtime.status()
        if state["server"]["reachable"]:
            self.runtime.touch()
            return
        if not state["runtime"]["exists"] or not state["model"]["exists"]:
            raise LocalAIError("The assistant is not set up yet. Open AI Assistance and press Set up.")
        if on_status:
            on_status("Starting the local model…")
        self.runtime.start(on_wait=(lambda secs: on_status(f"Loading the model… {secs}s") if on_status and secs and secs % 5 == 0 else None))

    TOOL_LABELS = {
        "search_library": "Searching the library", "get_library_stats": "Reading library numbers", "get_jobs": "Looking at current jobs", "get_job": "Inspecting a job",
        "get_diagnostics_summary": "Checking diagnostics", "find_unresolved_metadata": "Finding metadata to review", "find_duplicates": "Looking for duplicates",
    }

    @staticmethod
    def _shrunk(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The same conversation with a much shorter Archive X snapshot and shorter tool results (a second try after the server said 400)."""
        out = []
        for i, m in enumerate(messages):
            m = dict(m)
            if i == 1 and m.get("role") == "system":
                m["content"] = str(m.get("content") or "")[:1500]
            elif m.get("role") == "tool":
                m["content"] = str(m.get("content") or "")[:4000]
            out.append(m)
        return out

    @staticmethod
    def explain_400(detail: str) -> str:
        """Plain words for a model server's HTTP 400, keeping a short piece of what it said."""
        low = detail.lower()
        body = detail.split(":", 1)[-1].strip()[:240]
        if "context" in low or "exceed" in low or "too long" in low or "n_ctx" in low:
            why = "The question and Archive X's context are longer than the model's window. Ask something shorter, or raise Context tokens under Models, Advanced."
        elif "template" in low or "jinja" in low or "parse" in low:
            why = "The model's chat template did not accept the message format."
        elif "tool" in low:
            why = "This model build does not accept tool calls while streaming."
        else:
            why = "The local model server rejected the request."
        return f"{why} (server said: {body})" if body else why

    def _round_chunks(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        """The streamed chunks of one model round. If the server answers HTTP 400 before sending anything, ask again in a plainer way:
        first with a shorter snapshot and without the optional template switch, then without tools. The reply of the server is logged."""
        attempts = (("as asked", False, True), ("shorter snapshot", True, True), ("no tools", True, False))
        for number, (name, shrink, with_tools) in enumerate(attempts, 1):
            started = False
            try:
                for chunk in self.runtime.chat_stream(self._shrunk(messages) if shrink else messages, tools=tools if with_tools else None, max_tokens=3000 if self.runtime.thinking else 1100, plain=shrink):
                    started = True
                    yield chunk
                return
            except LocalAIError as exc:
                if started or "HTTP 400" not in str(exc):
                    raise
                self.db.log(f"Local AI answered HTTP 400 ({name}): {str(exc)[:400]}", "WARN", event="ai.inference.http400", details={"attempt": number, "detail": str(exc)[:600]})
                if number == len(attempts):
                    raise LocalAIError(self.explain_400(str(exc))) from exc

    def chat_events(self, message: str, context: str = "", user_identity: str = "operator", investigation_id: int | None = None):
        """Run one chat turn and yield what happens as it happens: status lines, each tool as it starts and ends, the answer word by word, then done.

        Every event is a dict with a "t" key: status, tool, reasoning, delta, done or error. The turn is saved to the history only when it finishes.
        """
        text = str(message or "").strip()
        if not text:
            yield {"t": "error", "detail": "Message is empty"}
            return
        if len(text) > 20000:
            yield {"t": "error", "detail": "Message exceeds 20,000 characters"}
            return
        pending: list[dict[str, Any]] = []
        try:
            self.ensure_running(lambda line: pending.append({"t": "status", "phase": "starting", "text": line}))
        except LocalAIError as exc:
            yield from pending
            yield {"t": "error", "detail": str(exc)}
            return
        yield from pending
        session_id, iid = self._session_id(investigation_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "system", "content": "ARCHIVE X LOCAL CONTEXT:\n" + self._bounded_context(context)},
            {"role": "user", "content": text},
        ]
        started = time.monotonic()
        tool_calls_total = 0
        answer = ""
        model = self.model
        try:
            for _round in range(3):
                yield {"t": "status", "phase": "thinking", "text": "Thinking…"}
                content = ""
                calls: dict[int, dict[str, Any]] = {}
                for chunk in self._round_chunks(messages, self.tools.definitions()):
                    model = str(chunk.get("model") or model)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(tc.get("index") or 0), {"id": "", "name": "", "arguments": ""})
                        slot["id"] = str(tc.get("id") or slot["id"])
                        fn = tc.get("function") or {}
                        slot["name"] += str(fn.get("name") or "")
                        slot["arguments"] += str(fn.get("arguments") or "")
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        yield {"t": "reasoning", "text": str(reasoning)}
                    piece = delta.get("content")
                    if piece:
                        if not content:
                            yield {"t": "status", "phase": "writing", "text": "Writing the answer…"}
                        content += piece
                        if not calls:
                            yield {"t": "delta", "text": piece}
                if not calls:
                    answer = content.strip() or "The local model returned no visible text."
                    break
                tool_calls_total += len(calls)
                if tool_calls_total > 6:
                    answer = "The local AI tool budget was reached. Please narrow the request."
                    yield {"t": "delta", "text": answer}
                    break
                messages.append({"role": "assistant", "content": content or None, "tool_calls": [
                    {"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for i, c in sorted(calls.items())]})
                for i, c in sorted(calls.items()):
                    label = self.TOOL_LABELS.get(c["name"], "Using a read-only tool")
                    yield {"t": "tool", "name": c["name"], "label": label, "state": "start"}
                    ok = True
                    try:
                        args = json.loads(c["arguments"] or "{}")
                        result = self.tools.execute(c["name"], args)
                    except Exception as exc:
                        ok = False
                        result = {"error": f"Tool failed: {type(exc).__name__}: {exc}"}
                    messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "name": c["name"], "content": json.dumps(result, ensure_ascii=False, default=str)[:16000]})
                    yield {"t": "tool", "name": c["name"], "label": label, "state": "done" if ok else "error"}
            else:
                answer = "The local model did not finish within the tool-call limit."
                yield {"t": "delta", "text": answer}
        except LocalAIError as exc:
            yield {"t": "error", "detail": str(exc)}
            return
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        self.db.ai_add_chat_message(session_id, "user", text, "", iid)
        self.db.ai_add_chat_message(session_id, "assistant", answer, "", iid)
        self.db.ai_update_investigation(iid, stage="OBSERVE")
        self.db.log("Local AI inference completed", event="ai.inference.completed", details={"model": model, "latency_ms": latency_ms, "tool_calls": tool_calls_total, "investigation_id": iid, "streamed": True})
        yield {"t": "done", "text": answer, "model": model, "latency_ms": latency_ms, "tool_calls": tool_calls_total}

    def chat(self, message: str, context: str = "", user_identity: str = "operator", investigation_id: int | None = None) -> dict[str, Any]:
        """Run one chat turn, including tool calls, and persist the messages."""
        text = str(message or "").strip()
        if not text:
            raise ValueError("Message is empty")
        if len(text) > 20000:
            raise ValueError("Message exceeds 20,000 characters")
        session_id, iid = self._session_id(investigation_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "system", "content": "ARCHIVE X LOCAL CONTEXT:\n" + self._bounded_context(context)},
            {"role": "user", "content": text},
        ]
        started = time.monotonic()
        tool_calls_total = 0
        try:
            self.ensure_running()
            for _ in range(3):
                response = self.runtime.chat(messages, tools=self.tools.definitions(), max_tokens=1100)
                assistant = self._extract_message(response)
                messages.append(assistant)
                calls = assistant.get("tool_calls") or []
                if not calls:
                    answer = str(assistant.get("content") or "").strip() or "The local model returned no visible text."
                    model = str(response.get("model") or self.model)
                    break
                tool_calls_total += len(calls)
                if tool_calls_total > 6:
                    answer = "The local AI tool budget was reached. Please narrow the request."
                    model = str(response.get("model") or self.model)
                    break
                for call in calls:
                    fn = call.get("function") if isinstance(call, dict) else None
                    name = str((fn or {}).get("name") or "")
                    raw_args = (fn or {}).get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        result = self.tools.execute(name, args)
                    except Exception as exc:
                        result = {"error": f"Tool failed: {type(exc).__name__}: {exc}"}
                    messages.append({"role": "tool", "tool_call_id": str(call.get("id") or ""), "name": name, "content": json.dumps(result, ensure_ascii=False, default=str)[:16000]})
            else:
                answer = "The local model did not finish within the tool-call limit."
                model = self.model
        except LocalAIError as exc:
            raise LocalAIError(str(exc)) from exc
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        self.db.ai_add_chat_message(session_id, "user", text, "", iid)
        self.db.ai_add_chat_message(session_id, "assistant", answer, "", iid)
        self.db.ai_update_investigation(iid, stage="OBSERVE")
        self.db.log("Local AI inference completed", event="ai.inference.completed", details={"model": model, "latency_ms": latency_ms, "tool_calls": tool_calls_total, "investigation_id": iid})
        return {
            "conversation_id": session_id,
            "investigation_id": iid,
            "response_id": "",
            "model": model,
            "text": answer,
            "status": "completed",
            "evidence_request": {"kind": "NONE", "reason": "", "scope": "", "items": []},
            "local_only": True,
            "latency_ms": latency_ms,
            "tool_calls": tool_calls_total,
        }

    def send_evidence(self, *, evidence_prompt: str, context: str = "", screenshot_data_url: str = "", diagnostics_text: str = "", client_state: str = "", user_identity: str = "operator", investigation_id: int | None = None) -> dict[str, Any]:
        """Continue a local investigation using operator-approved text evidence.

        Screenshots are intentionally rejected because the default Qwen3 4B
        local model is text-only; this prevents accidental image exfiltration
        and avoids pretending a text model can inspect pixels.
        """
        if screenshot_data_url:
            raise LocalAIError("The configured local model is text-only; screenshot evidence is not supported")
        parts = [str(evidence_prompt or "Continue using the approved evidence.")]
        if context:
            parts.append("ARCHIVE X CONTEXT:\n" + self._bounded_context(context))
        if diagnostics_text:
            parts.append("OPERATOR-APPROVED DIAGNOSTICS:\n" + redact_text(diagnostics_text)[:80000])
        if client_state:
            parts.append("OPERATOR-APPROVED CLIENT STATE:\n" + redact_text(client_state)[:24000])
        return self.chat("\n\n".join(parts), "", user_identity, investigation_id)

    def close(self) -> None:
        self.runtime.close()


def project_context(db, *, telegram=None, jobs=None, governor=None, live: dict | None = None, page: str = "", selected: dict | None = None) -> str:
    parts: list[str] = []
    ctx_file = config.APP_DIR / "ARCHIVE_X_V6_CONTEXT.md"
    if ctx_file.is_file():
        try:
            parts.append("PROJECT CONTEXT:\n" + ctx_file.read_text(encoding="utf-8", errors="replace")[:10000])
        except Exception:
            _swallowed_note("ai_assistant.project_context")
            pass
    try:
        investigation = db.ai_active_investigation()
        status = {
            "version": config.APP_VERSION,
            "local_ai": True,
            "investigation": {"id": investigation.get("id"), "title": investigation.get("title"), "status": investigation.get("status"), "stage": investigation.get("stage")},
            "page": str(page or ""),
            "telegram": {"connected": bool(getattr(telegram, "connected", False)), "status": str(getattr(telegram, "status_text", ""))},
            "current_job": jobs.running_job() if jobs is not None else None,
            "governor": governor.status() if governor is not None else {},
            "stats": db.stats(),
            "observations": db.ai_list_observations(30),
        }
        parts.append("LOCAL APPLICATION SNAPSHOT:\n" + json.dumps(status, ensure_ascii=False, default=str)[:18000])
    except Exception as exc:
        parts.append(f"LOCAL APPLICATION SNAPSHOT unavailable: {type(exc).__name__}: {exc}")
    if selected:
        parts.append("SELECTED MEDIA/PAGE CONTEXT:\n" + json.dumps(selected, ensure_ascii=False, default=str)[:8000])
    if live:
        parts.append("LIVE TEST RESULT:\n" + json.dumps(live, ensure_ascii=False, default=str)[:22000])
    return "\n\n".join(parts)[:48000]
