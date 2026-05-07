from dotenv import load_dotenv
import asyncio
import httpx
import re
from collections import defaultdict
import json
from livekit import agents
from livekit.agents import (
    AgentSession,
    Agent,
    RoomInputOptions,
    TurnHandlingOptions,
    llm,
    MetricsCollectedEvent,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics
from livekit.agents.llm import ChatMessage

from prompts.prompt import BASE_PERSONALITY
from prompts.phase_prompt import PHASE_PROMPTS
from helpers.write_json import log_turn
from helpers.generate_report import generate_report
import uuid
from datetime import datetime, timezone

load_dotenv(".env.local")


SIGNAL_DELTAS = {
    "positive":    +10,
    "neutral":      +5,
    "objection":    +5,
    "disengaging": -10,
    "hard_no":     -20,
    "end_call":      0,
    "close":         0,
}


class Assistant(Agent):

    def __init__(self):
        self.call_id = str(uuid.uuid4())
        self.timestamp_start = datetime.now(timezone.utc).isoformat()
        self.current_phase = "hook"
        self.previous_phase = None

        self.prompt_mode = "hook"
        self.return_to_prompt = None

        self.current_signal = None
        self.score = 0

        # value phase tracking
        self.value_step = 0
        self.last_value_benefit = None

        self.consecutive_objections = 0
        self.disengaging_attempts = 0
        self.hard_no_attempts = 0

        self._session_ref = None
        self.conversation_log = []

        super().__init__(instructions=self._build_instructions())

    # -------------------------
    # Instructions
    # -------------------------

    
    def _build_instructions(self) -> str:
        prompt = BASE_PERSONALITY

        overlay_modes = {"objection", "value_reengagement", "hard_no_recovery"}

        if self.prompt_mode not in overlay_modes and self.prompt_mode != self.current_phase:
            prompt += f"\n\n---\nCurrent Phase: {self.current_phase.upper()}"

        if self.prompt_mode == "value":
            benefit_labels = ["zero joining fee", "up to 100% brokerage sharing", "daily payouts"]
            step = min(self.value_step, len(benefit_labels) - 1)
            prompt += f"\nCurrent Value Step: {step + 1} of {len(benefit_labels)} — focus on: {benefit_labels[step]}"

        if self.prompt_mode == "value_reengagement" and self.last_value_benefit:
            prompt += f"\nThe benefit the user disengaged on: {self.last_value_benefit}"
            prompt += "\nUse what the user shared earlier in the conversation to make the re-engagement specific."

        prompt += f"\n\n---\n{PHASE_PROMPTS[self.prompt_mode]}"
        return prompt

    # -------------------------
    # Phase control
    # -------------------------

    def _go_to(self, phase: str):
        self.previous_phase = self.current_phase
        self.current_phase = phase
        self.prompt_mode = phase
        print(f"[PHASE] {self.previous_phase} → {self.current_phase}")

    # -------------------------
    # Signal handling
    # -------------------------

    def _handle_signal(self):

        if self.current_signal == "end":
            print("[END] user requested disconnect")
            self._go_to("end")
            return

        if self.current_signal == "hard_no":
            self.hard_no_attempts += 1
            if self.hard_no_attempts >= 2:
                print("[END] hard_no after recovery attempt → ending")
                self._go_to("end")
            else:
                print("[PROMPT] first hard_no → recovery attempt")
                self.return_to_prompt = self.prompt_mode
                self.prompt_mode = "hard_no_recovery"
            return

        if self.current_signal == "objection":
            self.consecutive_objections += 1
            self.hard_no_attempts = 0
            if self.consecutive_objections >= 2 and self.prompt_mode != "objection":
                self.return_to_prompt = self.prompt_mode
                self.prompt_mode = "objection"
                print(f"[PROMPT] persistent objection → objection mode (return to {self.return_to_prompt})")
            return

        if self.current_signal == "disengaging":
            self.consecutive_objections = 0
            self.hard_no_attempts = 0
            self.disengaging_attempts += 1

            if self.disengaging_attempts >= 5:
                print("[END] disengaging 5x → ending")
                self._go_to("end")
                return

            if self.current_phase == "value":
                benefit_labels = [
                    "zero joining fee",
                    "up to 100% brokerage sharing",
                    "daily payouts",
                ]
                step = min(self.value_step, len(benefit_labels) - 1)
                self.last_value_benefit = benefit_labels[step]
                self.return_to_prompt = "value"
                self.prompt_mode = "value_reengagement"
                print(f"[PROMPT] disengaging in value → value_reengagement (benefit: {self.last_value_benefit})")
            else:
                if self.prompt_mode != "objection":
                    self.return_to_prompt = self.prompt_mode
                    self.prompt_mode = "objection"
                    print("[PROMPT] disengaging → objection re-engagement")
            return

        if self.current_signal in ("positive", "neutral"):
            self.consecutive_objections = 0
            self.disengaging_attempts = 0
            self.hard_no_attempts = 0

            if self.prompt_mode == "value_reengagement":
                self.prompt_mode = "value"
                self.return_to_prompt = None
                print(f"[PROMPT] reengagement resolved → back to value at step {self.value_step}")
                return

            if self.prompt_mode in ("objection", "hard_no_recovery") and self.return_to_prompt:
                self.prompt_mode = self.return_to_prompt
                self.return_to_prompt = None
                print(f"[PROMPT] resolved → back to {self.prompt_mode}")

    # -------------------------
    # Phase advance
    # -------------------------

    def _advance_phase(self):
        if self.current_phase == "end":
            return

        if self.current_phase == "hook":
            self._go_to("engage")

        elif self.current_phase == "engage":
            if self.score >= 40 and self.prompt_mode != "objection":
                self._go_to("value")

        elif self.current_phase == "value":
            if self.current_signal in ("positive", "neutral") and self.prompt_mode == "value":
                self.value_step = min(self.value_step + 1, 2)
                print(f"[VALUE STEP] → {self.value_step}")

            # only allow close after all 3 benefits delivered
            if self.value_step >= 3 and self.score >= 60:
                print("[PHASE] value complete → close")
                self._go_to("close")
                return

            if self.score < 40 and self.prompt_mode not in ("objection", "value_reengagement"):
                print("[PHASE] value score dropped → engage")
                self.previous_phase = self.current_phase
                self.current_phase = "engage"
                self.prompt_mode = "engage"

    # -------------------------
    # LLM node
    # -------------------------

    async def llm_node(self, chat_ctx, tools, model_settings=None):
        metadata_buffer = ""
        metadata_mode = False
        spoken_text = ""

        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if not isinstance(chunk, llm.ChatChunk) or not chunk.delta:
                yield chunk
                continue

            content = getattr(chunk.delta, "content", None)
            if not content:
                yield chunk
                continue

            if metadata_mode:
                metadata_buffer += content
                continue

            tag_start = content.find("<")
            if tag_start != -1:
                spoken_part = content[:tag_start]
                if spoken_part.strip():
                    spoken_text += spoken_part
                    chunk.delta.content = spoken_part
                    yield chunk
                metadata_mode = True
                metadata_buffer += content[tag_start:]
            else:
                spoken_text += content
                yield chunk

        print("\n===== RAW LLM OUTPUT =====")
        print(spoken_text + metadata_buffer)
        print("==========================\n")

        self._extract_tags(metadata_buffer)
        self._apply_score()
        self._handle_signal()
        self._advance_phase()

        if spoken_text.strip():
            self.conversation_log.append({
                "role": "assistant",
                "text": spoken_text.strip(),
                "signal": self.current_signal,
                "score": self.score,
                "phase": self.current_phase,
                "value_step": self.value_step
            })
        
            log_turn(self.call_id, "assistant", spoken_text.strip(), self.score, self.current_phase, self.current_signal, self.value_step)

        if self.current_phase != "end":
            await self.update_instructions(self._build_instructions())

        print(f"[STATE] phase={self.current_phase} | prompt={self.prompt_mode} | score={self.score} | signal={self.current_signal} | value_step={self.value_step}")

        if self.current_phase == "end":
            await self._handle_end()
            return



    # -------------------------
    # Tag extraction
    # -------------------------

    def _extract_tags(self, text: str):
        def extract(tag):
            match = re.search(f"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
            return match.group(1).strip() if match else None

        signal = extract("signal")
        self.current_signal = signal if signal else "neutral"
        print(f"[SIGNAL] {self.current_signal}")

    # -------------------------
    # Score
    # -------------------------

    def _apply_score(self):
        delta = SIGNAL_DELTAS.get(self.current_signal, 0)
        self.score = max(0, min(100, self.score + delta))
        print(f"[SCORE] {self.score}")

    # -------------------------
    # End
    # -------------------------

    async def _handle_end(self):
        timestamp_end = datetime.now(timezone.utc).isoformat()
        
        print("\n========== CALL ENDED ==========")
        print(f"Final score  : {self.score}")
        print(f"Final phase  : {self.current_phase}")
        print(f"Previous     : {self.previous_phase}")
        print(f"Value step   : {self.value_step}")


        report = await generate_report(
            conversation_log=self.conversation_log,
            call_id=self.call_id,
            timestamp_start=self.timestamp_start,
            timestamp_end=timestamp_end,
            final_score=self.score,
            final_phase=self.current_phase,
            value_step=self.value_step,
        )

        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(
                    "http://localhost:8000/calls/report",
                    json=report
                )
                print(f"[API] Report sent → {res.status_code}")
            except Exception as e:
                print(f"[API] Failed to send report: {e}")

        # call_json = {
        #     "final_score": self.score,
        #     "final_phase": self.current_phase,
        #     "value_step_reached": self.value_step,
        #     "conversation": self.conversation_log
        # }
        # print("\n========== CALL JSON ==========")
        # print(json.dumps(call_json, indent=2, ensure_ascii=False))
        # print("================================\n")

        if self._session_ref:
            await self._session_ref.aclose()


# -------------------------
# Entrypoint
# -------------------------

async def entrypoint(ctx: agents.JobContext):

    session = AgentSession(
        stt="elevenlabs/scribe_v2_realtime",
        llm="deepseek-ai/deepseek-v3.1",
        tts="elevenlabs/eleven_flash_v2_5:iP95p4xoKVk53GoZ742B",
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detector=MultilingualModel(),
            interruption={"mode": "vad"},
        ),
        preemptive_generation=False,
    )

    assistant = Assistant()
    assistant._session_ref = session

    await session.start(
        room=ctx.room,
        agent=assistant,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    turn_metrics: dict[str, dict[str, float]] = defaultdict(dict)

        
    @session.on("conversation_item_added")
    def on_conversation_item_added(ev):
        if not isinstance(ev.item, ChatMessage):
            return

        if ev.item.role == "user":
            content = ev.item.content
            if isinstance(content, list):
                text = " ".join(c for c in content if isinstance(c, str))
            else:
                text = str(content) if content else ""
            if text.strip():
                assistant.conversation_log.append({
                    "role": "user",
                    "text": text.strip()
                })
                log_turn(assistant.call_id, "user", text.strip())

        if ev.item.role == "assistant":
            m = ev.item.metrics
            if not m:
                return
            e2e = m.get("e2e_latency")
            if e2e is not None:
                print("\n======== LATENCY REPORT ========")
                print(f"E2E Latency: {e2e:.3f}s")
                speech_id = getattr(ev.item, "speech_id", None)
                parts = turn_metrics.get(speech_id, {})
                for k, v in parts.items():
                    print(f"{k}: {v:.3f}s")
                print("================================\n")

    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        m = ev.metrics
        sid = getattr(m, "speech_id", None)
        if not sid:
            return
        if isinstance(m, EOUMetrics):
            turn_metrics[sid]["eou_delay"] = m.end_of_utterance_delay
        elif isinstance(m, LLMMetrics):
            turn_metrics[sid]["llm_ttft"] = m.ttft
            turn_metrics[sid]["llm_total"] = m.duration
        elif isinstance(m, TTSMetrics):
            turn_metrics[sid]["tts_ttfb"] = m.ttfb
            turn_metrics[sid]["tts_total"] = m.duration


    await session.generate_reply(
        instructions=assistant._build_instructions()
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))