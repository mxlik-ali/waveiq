import json
import os

def log_turn(call_id: str, role: str, text: str, score=None, phase=None, signal=None, value_step=None):
    os.makedirs("logs", exist_ok=True)
    
    if role == "user":
        entry = {
            "role": "user",
            "text": text
        }
    else:
        entry = {
            "role": "assistant",
            "text": text,
            "signal": signal,
            "score": score,
            "phase": phase,
            "value_step": value_step
        }

    with open(f"logs/{call_id}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")