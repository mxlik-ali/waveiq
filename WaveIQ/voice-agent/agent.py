import re
import json
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, TurnHandlingOptions, llm
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from conversation_state import ConversationState

load_dotenv(".env.local")

# Signal → score delta map mirrors what PHASE_TRANSITIONS tells the LLM
SIGNAL_DELTAS = {
    "positive":     +15,
    "neutral":        0,
    "objection":     +5,   # stayed on call despite objection
    "disengaging":  -10,
    "hard_no":      -40,
}

# What the LLM tag values map to in state transitions
VALID_PHASES = {"hook", "discovery", "pain", "pitch", "objection", "close", "end"}

VALID_SIGNALS = {"positive", "neutral", "objection", "disengaging", "hard_no"}


class Assistant(Agent):

    def __init__(self, state: ConversationState):
        self.state = state
        super().__init__(instructions=state.build_instructions())

        # buffers to accumulate tag content across chunks
        self._buffer = ""
        self._phase_tag = ""
        self._signal_tag = ""
        self._score_tag = ""
        self._info_tag = ""


    async def llm_node(self, chat_ctx, tools, model_settings=None):
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if not (isinstance(chunk, llm.ChatChunk) and chunk.delta):
                yield chunk
                continue

            content = getattr(chunk.delta, "content", None)
            if not content:
                yield chunk
                continue

            # accumulate into buffer for tag extraction
            self._buffer += content

            # extract and strip all tags, get clean speech text
            clean_text = self._extract_tags(self._buffer)

            # only emit what hasn't been emitted yet
            # we emit the clean portion that's safe (no open tags)
            safe_text = self._safe_to_emit(clean_text)

            if safe_text:
                chunk.delta.content = safe_text
                self._buffer = self._buffer[len(safe_text):]
                yield chunk
            # if nothing safe yet, hold the chunk (don't yield)

        # flush any remaining buffer after stream ends
        if self._buffer.strip():
            final = self._strip_all_tags(self._buffer)
            if final.strip():
                chunk.delta.content = final
                yield chunk
            self._buffer = ""

        # apply all extracted state updates after full response
        self._apply_state_updates()

        # rebuild instructions for next turn with updated state
        self.instructions = self.state.build_instructions()

    # -------------------------
    # Tag extraction
    # -------------------------

    def _extract_tags(self, text: str) -> str:
        # phase tag
        phase_match = re.search(r"<phase>(.*?)</phase>", text, re.DOTALL)
        if phase_match:
            self._phase_tag = phase_match.group(1).strip().lower()

        # signal tag
        signal_match = re.search(r"<signal>(.*?)</signal>", text, re.DOTALL)
        if signal_match:
            self._signal_tag = signal_match.group(1).strip().lower()

        # score_delta tag
        score_match = re.search(r"<score_delta>(.*?)</score_delta>", text, re.DOTALL)
        if score_match:
            self._score_tag = score_match.group(1).strip()

        # info tag — discovery data as JSON
        info_match = re.search(r"<info>(.*?)</info>", text, re.DOTALL)
        if info_match:
            self._info_tag = info_match.group(1).strip()

        return self._strip_all_tags(text)

    def _strip_all_tags(self, text: str) -> str:
        # remove complete tags
        text = re.sub(r"<phase>.*?</phase>", "", text, flags=re.DOTALL)
        text = re.sub(r"<signal>.*?</signal>", "", text, flags=re.DOTALL)
        text = re.sub(r"<score_delta>.*?</score_delta>", "", text, flags=re.DOTALL)
        text = re.sub(r"<info>.*?</info>", "", text, flags=re.DOTALL)
        # remove any incomplete open tags that haven't closed yet
        text = re.sub(r"<(phase|signal|score_delta|info)[^>]*>.*$", "", text, flags=re.DOTALL)
        return text

    def _safe_to_emit(self, text: str) -> str:
        # don't emit if an open tag is present but not yet closed
        open_tags = ["<phase>", "<signal>", "<score_delta>", "<info>"]
        for tag in open_tags:
            if tag in text:
                # hold everything from the open tag onward
                idx = text.find(tag)
                return text[:idx]
        return text

    # -------------------------
    # State updates after turn
    # -------------------------

    def _apply_state_updates(self):
        # 1. score delta
        if self._score_tag:
            try:
                delta = int(self._score_tag)
                self.state.update_score(delta)
            except ValueError:
                pass

        # 2. signal
        signal = self._signal_tag if self._signal_tag in VALID_SIGNALS else "neutral"
        self.state.last_signal = signal

        # if LLM didn't give score_delta, use signal-based delta as fallback
        if not self._score_tag and signal in SIGNAL_DELTAS:
            self.state.update_score(SIGNAL_DELTAS[signal])

        # 3. discovery info update
        if self._info_tag:
            try:
                info = json.loads(self._info_tag)
                if "partner_type" in info and info["partner_type"]:
                    self.state.partner_type = info["partner_type"]
                if "broker_status" in info and info["broker_status"]:
                    self.state.broker_status = info["broker_status"]
                if "network_size" in info and info["network_size"]:
                    self.state.network_size = info["network_size"]
                if "pain_surfaced" in info and info["pain_surfaced"]:
                    self.state.pain_surfaced = info["pain_surfaced"]
                if "language" in info and info["language"]:
                    self.state.switch_language(info["language"])
                if "objection_type" in info and info["objection_type"]:
                    lead_text = info.get("objection_lead_text", "")
                    if not self.state.already_handled(info["objection_type"]):
                        self.state.interrupt_for_objection(
                            info["objection_type"], lead_text
                        )
                if "objection_resolved" in info:
                    self.state.resolve_objection(info["objection_resolved"])
                if "cta_given" in info:
                    self.state.cta_given = info["cta_given"]
                if "cta_accepted" in info:
                    self.state.cta_accepted = info["cta_accepted"]
            except json.JSONDecodeError:
                pass

        # 4. phase transition — last so all data is updated first
        if self._phase_tag and self._phase_tag in VALID_PHASES:
            new_phase = self._phase_tag

            if new_phase == "objection" and self._phase_tag != self.state.phase:
                # objection interrupt already handled via info tag above
                pass
            elif new_phase != self.state.phase:
                # advance benefit counter when moving away from pitch
                if self.state.phase == "pitch" and new_phase == "pitch":
                    self.state.advance_benefit()
                else:
                    self.state.transition_to(new_phase)

            if new_phase == "end":
                self._handle_end()

        # log agent turn
        self.state.log_turn(
            speaker="agent",
            text="[agent response this turn]",  # replace with actual text if captured
            signal=signal
        )

        # reset buffers for next turn
        self._phase_tag = ""
        self._signal_tag = ""
        self._score_tag = ""
        self._info_tag = ""

        print(f"[STATE] {self.state.summary()}")

    def _handle_end(self):
        print("[CALL ENDED] Generating post-call JSON...")
        result = self.state.to_post_call_json()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        # TODO: pass to post-call LLM to fill post_call_summary and rm_handoff


# -------------------------
# Entrypoint
# -------------------------

async def entrypoint(ctx: agents.JobContext):
    state = ConversationState(lead_id="lead_001")

    session = AgentSession(
        stt="elevenlabs/scribe_v2_realtime",
        llm="google/gemini-3-flash-preview",
        tts="elevenlabs/eleven_flash_v2_5:cgSgspJ2msm6clMCkdW9",
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(turn_detector=MultilingualModel()),
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(state=state),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await session.generate_reply(
        instructions=state.build_instructions()
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))