import json
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from google import genai

from prompts.report_prompt import REPORT_PROMPT

# ═══════════════════════════════════════════════════════════
# FALLBACK — uncomment both blocks below if you want to retry
# report generation from a saved .jsonl file instead of live
# conversation_log passed from the call.
#
# Step 1: uncomment load_conversation_from_file()
# Step 2: uncomment the two lines inside generate_report()
# ═══════════════════════════════════════════════════════════


load_dotenv(".env.local")
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def load_conversation_from_file(path: str = "logs/conversation.jsonl"):
    turns = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                turns.append(json.loads(line))
    return turns



def _build_score_timeline(conversation_log: list) -> list:
    """
    Builds a score progression array from the conversation log.
    Only assistant turns carry score — user turns are skipped.

    Returns a list of dicts like:
    [
        { "turn": 2, "speaker": "assistant", "text_preview": "Namaste...", "score": 5,  "signal": "neutral", "phase": "hook" },
        { "turn": 4, "speaker": "assistant", "text_preview": "Great! So...", "score": 15, "signal": "positive", "phase": "engage" },
    ]
    """
    timeline = []
    for i, turn in enumerate(conversation_log):
        if turn.get("role") == "assistant" and turn.get("score") is not None:
            timeline.append({
                "turn": i + 1,
                "speaker": "agent",
                "text_preview": turn.get("text", "")[:60] + "..." if len(turn.get("text", "")) > 60 else turn.get("text", ""),
                "score": turn.get("score"),
                "signal": turn.get("signal"),
                "phase": turn.get("phase"),
            })
    return timeline


def _format_conversation_for_prompt(conversation_log: list) -> str:
    """
    Formats the conversation log into a readable string for the LLM prompt.
    Assistant turns include signal/score/phase metadata as context.
    """
    lines = []
    for i, turn in enumerate(conversation_log):
        role = turn.get("role", "unknown")
        text = turn.get("text", "")

        if role == "assistant":
            signal = turn.get("signal", "")
            score = turn.get("score", "")
            phase = turn.get("phase", "")
            line = f"Turn {i+1} [AGENT]: {text}"
            if signal:
                line += f"  // signal={signal} | score={score} | phase={phase}"
        else:
            line = f"Turn {i+1} [LEAD]: {text}"

        lines.append(line)
    return "\n".join(lines)


async def generate_report(
    conversation_log: list = None,
    call_id: str = None,
    lead_id: str = None,
    timestamp_start: str = None,
    timestamp_end: str = None,
    duration_seconds: int = None,
    final_score: int = None,
    final_phase: str = None,
    value_step: int = None,
) -> dict:


    conversation_text = _format_conversation_for_prompt(conversation_log)
    score_timeline = _build_score_timeline(conversation_log)

    prompt = REPORT_PROMPT.format(
        system_score=final_score or "N/A",
        conversation=conversation_text,
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    raw = response.text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    llm_data = json.loads(raw)

    report = {
        "call_id": call_id or str(uuid.uuid4()),
        "lead_id": lead_id or None,
        "timestamp_start": timestamp_start or None,
        "timestamp_end": timestamp_end or datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds or None,
        "system_score": final_score or None,
        "final_phase": final_phase or None,
        "value_step_reached": value_step or None,

        # LLM-analyzed fields
        **llm_data,

        # Full conversation turns for frontend display
        "conversation": [
            {
                "turn": i + 1,
                "speaker": "agent" if t.get("role") == "assistant" else "lead",
                "text": t.get("text"),
            }
            for i, t in enumerate(conversation_log)
        ],

        # Score progression across the call
        "score_timeline": score_timeline,
    }

    os.makedirs("reports", exist_ok=True)
    out_path = f"reports/{report['call_id']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"[REPORT] Saved → {out_path}")
    return report

import asyncio

if __name__ == "__main__":
    conversation_log = load_conversation_from_file("logs/conversation.jsonl")
    final_score = conversation_log[-1].get("score") if conversation_log else 0

    asyncio.run(generate_report(
        conversation_log=conversation_log,
        final_score=final_score
    ))