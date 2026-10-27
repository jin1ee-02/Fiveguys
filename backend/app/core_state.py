"""Core State: the shared structure of the conversation that both paths read.

Flow: message sent -> Core State refreshed from the recent messages (one call, after the reply is sent)
      -> Light (draft check, alternatives, reaction preview, message analysis) reads a compact view
      -> Heavy (verdict) reads the full Core State, rebuilding it first only when it is stale.
"""

import logging

from fastapi import HTTPException

from app.prompts import CORE_STATE_PROMPT
from app.provider import ModelOutputError
from app.schemas import CoreStateOutput

logger = logging.getLogger("uvicorn.error")
WINDOW = 10  # same window as Light and the verdict


def valid_indices(indices, count, limit=10):
    return len(indices) <= limit and len(set(indices)) == len(indices) and all(0 <= index < count for index in indices)


def build(provider, messages, relationship, previous=None, context=""):
    """One structured call: positions / issues / facts / background. A reply that breaks a rule is asked for once more."""
    payload = {"messages": messages, "relationship": relationship, "context": context, "previous_core_state": previous}
    # Early in a chat only one person may have spoken; the other's position may then stay empty.
    spoke = {m["speaker"] for m in messages}

    def structure():
        output = provider.structured(CORE_STATE_PROMPT, payload, CoreStateOutput, 900)
        texts = [output.position_a, output.position_b, output.background, *output.issues, *[fact.text for fact in output.facts]]
        if ("A" in spoke and not output.position_a.strip()) or ("B" in spoke and not output.position_b.strip()) or not output.issues or len(output.issues) > 4 or len(output.facts) > 6 or any(len(text) > 500 for text in texts) or any(not valid_indices(fact.evidence_indices, len(messages)) for fact in output.facts):
            raise ModelOutputError("Core State 구조화 결과가 유효하지 않습니다.")
        return output
    try:
        return structure()
    except ModelOutputError:
        return structure()


def light_view(core):
    """What Light gets: who wants what and what the dispute is about. Facts and the emotion trajectory stay with Heavy."""
    if not core:
        return None
    return {"issues": core["issues"], "position_a": core["position_a"], "position_b": core["position_b"], "background": core["background"]}


def refresh(store, room_id, provider):
    """Bring the room's Core State up to the current room version. Runs after a message reply has been sent;
    a failure only leaves the previous Core State in place (the verdict rebuilds a stale one)."""
    from app.store import room_lock
    try:
        with room_lock("core:" + room_id, wait=0):
            core, version = store.core_with_version(room_id)
            state = store.state(room_id, "A")
            if version == state["version"] or not state["messages"]:
                return
            messages = [{"speaker": m["speaker"], "text": m["text"]} for m in state["messages"][-WINDOW:]]
            output = build(provider, messages, state["relationship"], core)
            store.save_core(room_id, output.model_dump_json(), state["version"])
    except HTTPException:
        pass  # another refresh of this room is running; it or the next message catches up
    except Exception as error:  # background work: log the cause (never the chat text) and keep the old state
        logger.warning("Core State 갱신 실패 %s: %s", room_id, type(error).__name__)
