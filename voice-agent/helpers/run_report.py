import json
import asyncio
import httpx
from helpers.generate_report import generate_report


def load_conversation_from_file(path: str = "logs/17e4884d-3541-44af-a954-e2176a11343d.jsonl"):
    turns = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                turns.append(json.loads(line))
    return turns


async def send_report_to_api(report: dict):
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(
                "http://localhost:8000/calls/report",
                json=report
            )
            print(f"[API] Report sent → {res.status_code}")
        except Exception as e:
            print(f"[API] Failed to send report: {e}")


async def run(path: str = "logs/17e4884d-3541-44af-a954-e2176a11343d.jsonl"):
    conversation_log = load_conversation_from_file(path)
    final_score = conversation_log[-1].get("score") if conversation_log else 0

    report = await generate_report(
        conversation_log=conversation_log,
        final_score=final_score
    )

    await send_report_to_api(report)


if __name__ == "__main__":
    asyncio.run(run())