"""
tutor_engine.py

This is the part of the submission that carries the educational reasoning.

Design decision: the model is NOT given a fixed lesson script. On every turn
it receives the full interaction history (what it taught, what it asked, what
the student answered) and must decide ONE of four moves before saying anything:

    slow_down   — same concept, smaller step, more scaffolding
    reframe     — same concept, different mental model / analogy
    jump_ahead  — concept is landing, raise difficulty or move on
    probe       — answer is ambiguous, ask a targeted follow-up before teaching more

The model must justify the move (diagnosis) before it teaches. That forces the
"why does this help a student learn" answer to live in the transcript itself,
not just in our README.

The single subject, on purpose, is Python functions: definition, parameters vs
arguments, return values, and scope — a small area with well-known, well-shaped
misconceptions (return vs print; mutating vs reassigning; default-argument
gotchas), which is exactly the kind of terrain where "reads how the student is
responding and changes course" has room to show up in one sitting.
"""

import json
import os
import re
import urllib.request
import urllib.error

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = os.environ.get("TUTOR_MODEL", "claude-sonnet-5")

XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = os.environ.get("TUTOR_MODEL_XAI", "grok-4-fast")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("TUTOR_MODEL_GROQ", "openai/gpt-oss-120b")


def _active_provider():
    forced = os.environ.get("PROVIDER", "").strip().lower()
    if forced in ("anthropic", "xai", "groq"):
        return forced
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("XAI_API_KEY"):
        return "xai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None

CONCEPTS = ["definition", "parameters", "return_values", "scope", "default_args"]

SYSTEM_PROMPT = f"""You are the reasoning engine behind an adaptive one-on-one
programming tutor. You teach exactly ONE topic in this session: Python functions
— what a function is, parameters vs arguments, return values (vs print), variable
scope, and (only once the basics are solid) the mutable-default-argument gotcha.

You do not follow a fixed slide order. On every turn you look at the FULL
transcript so far (your previous explanations, your previous questions, and
what the student actually answered) and you decide the single best next move:

- "slow_down": the student is close but shaky. Re-teach the SAME concept with a
  smaller, more concrete step (smaller numbers, one fewer moving part, a worked
  example line-by-line). Do not advance to a new concept.
- "reframe": the student's answer reveals a specific misconception (e.g.
  confusing return with print, thinking a function call permanently changes a
  variable, etc). Explain the SAME concept again but with a genuinely different
  mental model/analogy than you used last time — not just reworded.
- "jump_ahead": the student clearly has it. Move to the next concept in the
  sequence {CONCEPTS}, and/or raise the difficulty of the example.
- "probe": the student's answer is too short or ambiguous to diagnose (e.g.
  "yes", "I think so", a guess with no reasoning). Do not teach yet — ask one
  sharp follow-up question that would reveal which misconception, if any, is
  present. Set "explanation" to an empty string on this move.

Rules:
- Stay on ONE concept per teaching beat until it is either understood or you
  explicitly move via jump_ahead. Never introduce two new ideas in one turn.
- Every question must be answerable from what you just explained — no pop quiz
  questions on things you have not taught yet.
- Alternate concrete code examples with plain-English explanation. Every
  "explanation" should include a short Python code snippet in a fenced block.
- Keep "explanation" under ~120 words and "question" under ~40 words.
- mastery_estimate is your running belief about the student's grasp of each of
  {CONCEPTS}, each a number from 0.0 to 1.0. Carry forward and adjust the
  previous estimates you can infer from the transcript; do not reset unrelated
  concepts to 0.
- Set "done": true only once mastery_estimate for every concept in {CONCEPTS}
  is at or above 0.7, or you judge the student has plateaued after several
  attempts on the same concept (in which case explain that honestly instead of
  padding the session). When done is true, "question" should be empty and
  "explanation" should be a short closing note.
- On the very first turn (empty transcript) there is nothing to diagnose:
  set "diagnosis" to "start", "action" to "slow_down", "concept_tag" to
  "definition", and teach the smallest possible first step.

Respond with ONLY a single JSON object. No markdown fences, no commentary
before or after. The JSON object must have exactly these keys:

{{
  "diagnosis": string,          // one sentence: your read of the student's last answer, or "start"
  "action": "slow_down" | "reframe" | "jump_ahead" | "probe",
  "concept_tag": one of {CONCEPTS},
  "explanation": string,        // teaching content for this turn, may include a ```python fenced block, "" if action is "probe"
  "question": string,           // the question you're asking now, "" if done is true
  "question_type": "mcq" | "short_answer",
  "options": array of strings or null,  // required (2-4 items) if question_type is "mcq", else null
  "mastery_estimate": {{"definition": number, "parameters": number, "return_values": number, "scope": number, "default_args": number}},
  "done": boolean
}}
"""


class TutorEngineError(Exception):
    pass


def _serialize_history(history):
    """Turn the client-side history array into a plain-text transcript the
    model can reason over. Each entry already has the shape we returned to the
    client on a previous turn, plus the student's answer once they respond."""
    if not history:
        return "(no turns yet — this is the first turn)"

    lines = []
    for i, turn in enumerate(history, start=1):
        lines.append(f"--- Turn {i} ---")
        lines.append(f"tutor diagnosis: {turn.get('diagnosis', '')}")
        lines.append(f"tutor action: {turn.get('action', '')} (concept: {turn.get('concept_tag', '')})")
        if turn.get("explanation"):
            lines.append(f"tutor explanation:\n{turn['explanation']}")
        if turn.get("question"):
            lines.append(f"tutor question: {turn['question']}")
            if turn.get("options"):
                lines.append(f"options: {turn['options']}")
        if turn.get("student_answer") is not None:
            lines.append(f"student answered: {turn['student_answer']}")
    return "\n".join(lines)


def _extract_json(text):
    """The model is instructed to return raw JSON, but strip fences defensively
    in case it wraps the object anyway."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(json)?", "", cleaned.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip())
    cleaned = cleaned.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise TutorEngineError(f"Could not find a JSON object in model output: {text[:200]}")
    return json.loads(match.group(0))


def _validate(turn_data):
    required_keys = {
        "diagnosis", "action", "concept_tag", "explanation", "question",
        "question_type", "options", "mastery_estimate", "done",
    }
    missing = required_keys - set(turn_data.keys())
    if missing:
        raise TutorEngineError(f"Model response missing keys: {missing}")
    if turn_data["action"] not in {"slow_down", "reframe", "jump_ahead", "probe"}:
        raise TutorEngineError(f"Invalid action: {turn_data['action']}")
    if turn_data["concept_tag"] not in CONCEPTS:
        raise TutorEngineError(f"Invalid concept_tag: {turn_data['concept_tag']}")
    return turn_data


def _call_anthropic(user_message):
    api_key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_message}],
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "User-Agent": "Mozilla/5.0 (compatible; adaptive-tutor/1.0)",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise TutorEngineError(f"Anthropic API error {e.code}: {detail[:300]}")
    except urllib.error.URLError as e:
        raise TutorEngineError(f"Could not reach Anthropic API: {e}")

    text_blocks = [b["text"] for b in raw.get("content", []) if b.get("type") == "text"]
    if not text_blocks:
        raise TutorEngineError(f"No text content in model response: {raw}")
    return "\n".join(text_blocks)


def _call_xai(user_message):
    api_key = os.environ["XAI_API_KEY"]
    body = json.dumps({
        "model": XAI_MODEL,
        "temperature": 0.3,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        XAI_API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (compatible; adaptive-tutor/1.0)",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise TutorEngineError(f"xAI API error {e.code}: {detail[:300]}")
    except urllib.error.URLError as e:
        raise TutorEngineError(f"Could not reach xAI API: {e}")

    choices = raw.get("choices", [])
    if not choices or "message" not in choices[0]:
        raise TutorEngineError(f"No content in xAI response: {raw}")
    return choices[0]["message"]["content"]


def _call_groq(user_message):
    api_key = os.environ["GROQ_API_KEY"]
    body = json.dumps({
        "model": GROQ_MODEL,
        "temperature": 0.3,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        GROQ_API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (compatible; adaptive-tutor/1.0)",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise TutorEngineError(f"Groq API error {e.code}: {detail[:300]}")
    except urllib.error.URLError as e:
        raise TutorEngineError(f"Could not reach Groq API: {e}")

    choices = raw.get("choices", [])
    if not choices or "message" not in choices[0]:
        raise TutorEngineError(f"No content in Groq response: {raw}")
    return choices[0]["message"]["content"]


def get_next_turn(history, student_answer):
    provider = _active_provider()
    if provider is None:
        raise TutorEngineError(
            "No API key set. Set GROQ_API_KEY (Groq), XAI_API_KEY (xAI/Grok), "
            "or ANTHROPIC_API_KEY (Claude)."
        )

  
    history = list(history)
    if history and student_answer is not None:
        history[-1] = {**history[-1], "student_answer": student_answer}

    transcript = _serialize_history(history)
    user_message = (
        "TRANSCRIPT SO FAR:\n\n" + transcript +
        "\n\nDecide the next move and respond with the JSON object described in your instructions."
    )

    if provider == "groq":
        raw_text = _call_groq(user_message)
    elif provider == "xai":
        raw_text = _call_xai(user_message)
    else:
        raw_text = _call_anthropic(user_message)

    turn_data = _extract_json(raw_text)
    return _validate(turn_data)
