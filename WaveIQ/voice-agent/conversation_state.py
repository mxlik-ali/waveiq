import uuid
from dataclasses import dataclass, field
from datetime import datetime
from prompt import BASE_PERSONALITY, PHASE_PROMPTS, PHASE_TRANSITIONS


@dataclass
class ConversationState:

    # --- Identity ---
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    lead_id: str = ""
    timestamp_start: str = field(default_factory=lambda: datetime.now().isoformat())

    # --- Phase control ---
    phase: str = "hook"
    previous_phase: str = "hook"
    return_to_phase: str = None

    # --- Scoring ---
    score: int = 50
    turn_count: int = 0
    last_signal: str = "neutral"

    # --- Language ---
    language: str = "hi"
    language_switched: bool = False
    language_switched_to: str = None

    # --- Discovery ---
    partner_type: str = None
    broker_status: str = None
    network_size: str = None

    # --- Pain ---
    pain_surfaced: str = None

    # --- Pitch ---
    BENEFITS: tuple = field(default=(
        "zero_joining_fee",
        "hundred_percent_brokerage",
        "daily_payouts"
    ), init=False, repr=False)
    benefit_index: int = 0
    benefits_delivered: list = field(default_factory=list)

    # --- Objections ---
    objections_handled: list = field(default_factory=list)

    # --- Outcome ---
    cta_given: str = None
    cta_accepted: bool = None

    # --- Conversation log ---
    conversation: list = field(default_factory=list)

    # -------------------------
    # Phase-specific context
    # -------------------------

    def _phase_context(self) -> str:
        if self.phase == "hook":
            return ""

        if self.phase == "discovery":
            missing = [
                k for k, v in {
                    "what they do": self.partner_type,
                    "broker status": self.broker_status,
                    "network size": self.network_size
                }.items() if v is None
            ]
            known = [
                f"{k}: {v}" for k, v in {
                    "partner_type": self.partner_type,
                    "broker_status": self.broker_status,
                    "network_size": self.network_size
                }.items() if v is not None
            ]
            context = ""
            if known:
                context += f"Already know: {', '.join(known)}\n"
            if missing:
                context += f"Still need to find out: {', '.join(missing)}"
            return context

        if self.phase == "pain":
            return (
                f"They told you: partner_type={self.partner_type}, "
                f"broker_status={self.broker_status}. "
                f"Use this to surface the gap."
            )

        if self.phase == "pitch":
            if self.benefit_index < len(self.BENEFITS):
                return f"Deliver this benefit now: {self.BENEFITS[self.benefit_index]}"
            return "All benefits delivered. Move to close."

        if self.phase == "objection":
            if not self.objections_handled:
                return ""
            current = self.objections_handled[-1]
            already = [o["type"] for o in self.objections_handled[:-1]]
            context = (
                f"Objection raised: {current['type']}\n"
                f"Their exact words: {current['lead_text']}\n"
            )
            if already:
                context += f"Already handled (do not repeat): {', '.join(already)}"
            return context

        if self.phase == "close":
            return (
                f"Lead score: {self.score} → {self.classification()}\n"
                f"Benefits they heard: {', '.join(self.benefits_delivered)}\n"
                f"Unresolved objections: {self.unresolved_objections() or 'none'}"
            )

        return ""

    # -------------------------
    # Prompt builder
    # -------------------------

    def build_instructions(self) -> str:
        context = self._phase_context()

        prompt = (
            BASE_PERSONALITY
            + f"\n\n---"
            + f"\nPhase: {self.phase.upper()}"
        )

        if context:
            prompt += f"\nContext:\n{context}"

        prompt += (
            f"\n\n---"
            + f"\n{PHASE_PROMPTS[self.phase]}"
            + f"\n\n---"
            + PHASE_TRANSITIONS
        )

        return prompt

    # -------------------------
    # Score
    # -------------------------

    def update_score(self, delta: int):
        self.score = max(0, min(100, self.score + delta))

    def classification(self) -> str:
        if self.score >= 70:
            return "Hot"
        elif self.score >= 35:
            return "Warm"
        return "Cold"

    # -------------------------
    # Phase transitions
    # -------------------------

    def transition_to(self, new_phase: str):
        self.previous_phase = self.phase
        self.phase = new_phase

    def interrupt_for_objection(self, objection_type: str, lead_text: str):
        self.return_to_phase = self.phase
        self.previous_phase = self.phase
        self.phase = "objection"
        self.objections_handled.append({
            "type": objection_type,
            "lead_text": lead_text,
            "resolved": False
        })

    def resolve_objection(self, resolved: bool):
        if self.objections_handled:
            self.objections_handled[-1]["resolved"] = resolved
        self.phase = self.return_to_phase or self.previous_phase
        self.return_to_phase = None

    def already_handled(self, objection_type: str) -> bool:
        return any(o["type"] == objection_type for o in self.objections_handled)

    def unresolved_objections(self) -> list:
        return [o["type"] for o in self.objections_handled if not o["resolved"]]

    # -------------------------
    # Pitch
    # -------------------------

    def advance_benefit(self):
        if self.benefit_index < len(self.BENEFITS):
            self.benefits_delivered.append(self.BENEFITS[self.benefit_index])
            self.benefit_index += 1
        if self.benefit_index >= len(self.BENEFITS):
            self.transition_to("close")

    # -------------------------
    # Language
    # -------------------------

    def switch_language(self, new_language: str):
        if new_language != self.language:
            self.language_switched = True
            self.language_switched_to = new_language
            self.language = new_language

    # -------------------------
    # Conversation log
    # -------------------------

    def log_turn(self, speaker: str, text: str, signal: str = None):
        self.turn_count += 1
        self.conversation.append({
            "turn": self.turn_count,
            "speaker": speaker,
            "text": text,
            "phase": self.phase,
            "signal": signal,
            "score_after": self.score
        })

    # -------------------------
    # Summary
    # -------------------------

    def summary(self) -> dict:
        return {
            "phase": self.phase,
            "score": self.score,
            "classification": self.classification(),
            "turns": self.turn_count,
            "last_signal": self.last_signal
        }

    # -------------------------
    # Post call JSON
    # -------------------------

    def to_post_call_json(self) -> dict:
        return {
            "call_id": self.call_id,
            "lead_id": self.lead_id,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": datetime.now().isoformat(),
            "duration_seconds": self._duration_seconds(),
            "phase_reached": self.phase,
            "language": {
                "used": self.language,
                "switched_mid_call": self.language_switched,
                "switched_to": self.language_switched_to
            },
            "phases": {
                "discovery": {
                    "partner_type": self.partner_type,
                    "broker_status": self.broker_status,
                    "network_size": self.network_size
                },
                "pain": {
                    "pain_surfaced": self.pain_surfaced
                },
                "pitch": {
                    "benefits_delivered": self.benefits_delivered,
                    "all_benefits_covered": self.benefit_index >= len(self.BENEFITS)
                }
            },
            "objections": self.objections_handled,
            "unresolved_objections": self.unresolved_objections(),
            "qualification": {
                "score": self.score,
                "classification": self.classification()
            },
            "outcome": {
                "cta_given": self.cta_given,
                "cta_accepted": self.cta_accepted
            },
            "conversation": self.conversation,
            "post_call_summary": None,
            "rm_handoff": None
        }

    def _duration_seconds(self) -> int:
        try:
            start = datetime.fromisoformat(self.timestamp_start)
            return int((datetime.now() - start).total_seconds())
        except Exception:
            return 0