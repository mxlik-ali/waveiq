# from dotenv import load_dotenv
# import asyncio
# import re
# from collections import defaultdict

# from livekit import agents
# from livekit.agents import (
#     AgentSession,
#     Agent,
#     RoomInputOptions,
#     TurnHandlingOptions,
#     llm,
#     MetricsCollectedEvent,
# )
# from livekit.plugins import noise_cancellation, silero
# from livekit.plugins.turn_detector.multilingual import MultilingualModel
# from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics
# from livekit.agents.llm import ChatMessage

# from testprompt import BASE_PERSONALITY
# from phase_prompt import PHASE_PROMPTS

# load_dotenv(".env.local")


# SIGNAL_DELTAS = {
#     "positive":    +10,
#     "neutral":      +5,
#     "objection":    +5,
#     "disengaging": -10,
#     "hard_no":     -20,
#     "end_call":      0,
# }

# # How many seconds of silence after TTS finishes before we nudge the user
# INACTIVITY_TIMEOUT = 3.0


# class Assistant(Agent):

#     def __init__(self):
#         self.current_phase = "hook"
#         self.previous_phase = None

#         self.prompt_mode = "hook"
#         self.return_to_prompt = None

#         self.current_signal = None
#         self.score = 0

#         # value phase tracking
#         # tracks which benefit we are on (0=fee, 1=brokerage, 2=daily payout)
#         self.value_step = 0
#         # stores the last benefit we were discussing before a disengage
#         self.last_value_benefit = None

#         self.consecutive_objections = 0
#         self.disengaging_attempts = 0
#         self.hard_no_attempts = 0

#         self._session_ref = None

#         # inactivity timer handle — cancelled if user speaks
#         self._inactivity_timer: asyncio.Task | None = None

#         super().__init__(instructions=self._build_instructions())

#     # -------------------------
#     # Instructions
#     # -------------------------

#     def _build_instructions(self) -> str:
#         prompt = BASE_PERSONALITY
#         prompt += f"\n\n---\nCurrent Phase: {self.current_phase.upper()}"

#         # inject value_step context so the LLM knows which benefit to introduce
#         if self.prompt_mode == "value":
#             benefit_labels = [
#                 "zero joining fee",
#                 "up to 100% brokerage sharing",
#                 "daily payouts",
#             ]
#             step = min(self.value_step, len(benefit_labels) - 1)
#             prompt += f"\nCurrent Value Step: {step + 1} of {len(benefit_labels)} — focus on: {benefit_labels[step]}"

#         # inject last benefit context for reengagement
#         if self.prompt_mode == "value_reengagement" and self.last_value_benefit:
#             prompt += f"\nThe benefit the user disengaged on: {self.last_value_benefit}"
#             prompt += "\nUse what the user shared earlier in the conversation to make the re-engagement specific."

#         prompt += f"\n\n---\n{PHASE_PROMPTS[self.prompt_mode]}"
#         return prompt

#     # -------------------------
#     # Phase control
#     # -------------------------

#     def _go_to(self, phase: str):
#         self.previous_phase = self.current_phase
#         self.current_phase = phase
#         self.prompt_mode = phase
#         print(f"[PHASE] {self.previous_phase} → {self.current_phase}")

#     # -------------------------
#     # Signal handling
#     # -------------------------

#     def _handle_signal(self):

#         if self.current_signal == "end_call":
#             print("[END] user requested disconnect")
#             self._go_to("end")
#             return

#         if self.current_signal == "hard_no":
#             self.hard_no_attempts += 1
#             if self.hard_no_attempts >= 2:
#                 print("[END] hard_no after recovery attempt → ending")
#                 self._go_to("end")
#             else:
#                 print("[PROMPT] first hard_no → recovery attempt")
#                 self.return_to_prompt = self.prompt_mode
#                 self.prompt_mode = "hard_no_recovery"
#             return

#         if self.current_signal == "objection":
#             self.consecutive_objections += 1
#             self.hard_no_attempts = 0
#             if self.consecutive_objections >= 2 and self.prompt_mode != "objection":
#                 self.return_to_prompt = self.prompt_mode
#                 self.prompt_mode = "objection"
#                 print(f"[PROMPT] persistent objection → objection mode (return to {self.return_to_prompt})")
#             return

#         if self.current_signal == "disengaging":
#             self.consecutive_objections = 0
#             self.hard_no_attempts = 0
#             self.disengaging_attempts += 1

#             if self.disengaging_attempts >= 5:
#                 print("[END] disengaging 5x → ending")
#                 self._go_to("end")
#                 return

#             if self.current_phase == "value":
#                 benefit_labels = [
#                     "zero joining fee",
#                     "up to 100% brokerage sharing",
#                     "daily payouts",
#                 ]
#                 step = min(self.value_step, len(benefit_labels) - 1)
#                 self.last_value_benefit = benefit_labels[step]
#                 self.return_to_prompt = "value"
#                 self.prompt_mode = "value_reengagement"
#                 # current_phase stays value — no need to drop to engage
#                 print(f"[PROMPT] disengaging in value → value_reengagement (benefit: {self.last_value_benefit})")
#             else:
#                 if self.prompt_mode != "objection":
#                     self.return_to_prompt = self.prompt_mode
#                     self.prompt_mode = "objection"
#                     print("[PROMPT] disengaging → objection re-engagement")
#             return

#         if self.current_signal in ("positive", "neutral"):
#             self.consecutive_objections = 0
#             self.disengaging_attempts = 0
#             self.hard_no_attempts = 0

#             # reengagement resolved → just snap prompt back, phase never left value
#             if self.prompt_mode == "value_reengagement":
#                 self.prompt_mode = "value"
#                 self.return_to_prompt = None
#                 print(f"[PROMPT] reengagement resolved → back to value at step {self.value_step}")
#                 return

#             if self.prompt_mode in ("objection", "hard_no_recovery") and self.return_to_prompt:
#                 self.prompt_mode = self.return_to_prompt
#                 self.return_to_prompt = None
#                 print(f"[PROMPT] resolved → back to {self.prompt_mode}")

#     # -------------------------
#     # Phase advance
#     # -------------------------

#     def _advance_phase(self):
#         if self.current_phase == "end":
#             return

#         if self.current_phase == "hook":
#             self._go_to("engage")

#         elif self.current_phase == "engage":
#             if self.score >= 55 and self.prompt_mode != "objection":
#                 self._go_to("value")
#                 # do not reset value_step — if returning from reengagement
#                 # we pick up where we left off

#         elif self.current_phase == "value":
#             # advance value_step when user gives a real positive/neutral answer
#             if self.current_signal in ("positive", "neutral") and self.prompt_mode == "value":
#                 self.value_step = min(self.value_step + 1, 2)
#                 print(f"[VALUE STEP] → {self.value_step}")

#             # drop back to engage if score falls
#             if self.score < 40 and self.prompt_mode not in ("objection", "value_reengagement"):
#                 print("[PHASE] value score dropped → engage")
#                 self.previous_phase = self.current_phase
#                 self.current_phase = "engage"
#                 self.prompt_mode = "engage"

#     # -------------------------
#     # Inactivity timer
#     # -------------------------

#     def _cancel_inactivity_timer(self):
#         if self._inactivity_timer and not self._inactivity_timer.done():
#             self._inactivity_timer.cancel()
#             self._inactivity_timer = None

#     def _start_inactivity_timer(self):
#         self._cancel_inactivity_timer()
#         self._inactivity_timer = asyncio.create_task(self._inactivity_nudge())

#     async def _inactivity_nudge(self):
#         await asyncio.sleep(INACTIVITY_TIMEOUT)
#         print("[INACTIVITY] user silent → triggering nudge")

#         if self._session_ref and self.current_phase != "end":
#             # build a context-aware nudge instruction
#             if self.current_phase == "value":
#                 benefit_labels = [
#                     "zero joining fee",
#                     "up to 100% brokerage sharing",
#                     "daily payouts",
#                 ]
#                 benefit = benefit_labels[min(self.value_step, 2)]
#                 nudge = (
#                     f"The user has gone silent. You were just discussing '{benefit}'. "
#                     f"Re-engage them with a short, specific follow-up question tied to that benefit. "
#                     f"Do NOT say 'still there?' or anything generic."
#                 )
#             else:
#                 nudge = (
#                     "The user has gone silent. Re-engage naturally with a short, "
#                     "curious question that continues where you left off."
#                 )

#             await self._session_ref.generate_reply(instructions=nudge)

#     # -------------------------
#     # LLM node
#     # -------------------------

#     async def llm_node(self, chat_ctx, tools, model_settings=None):
#         # user spoke — cancel any pending inactivity timer
#         self._cancel_inactivity_timer()

#         metadata_buffer = ""
#         metadata_mode = False
#         spoken_text = ""

#         async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
#             if not isinstance(chunk, llm.ChatChunk) or not chunk.delta:
#                 yield chunk
#                 continue

#             content = getattr(chunk.delta, "content", None)
#             if not content:
#                 yield chunk
#                 continue

#             if metadata_mode:
#                 metadata_buffer += content
#                 continue

#             tag_start = content.find("<")
#             if tag_start != -1:
#                 spoken_part = content[:tag_start]
#                 if spoken_part.strip():
#                     spoken_text += spoken_part
#                     chunk.delta.content = spoken_part
#                     yield chunk
#                 metadata_mode = True
#                 metadata_buffer += content[tag_start:]
#             else:
#                 spoken_text += content
#                 yield chunk

#         print("\n===== RAW LLM OUTPUT =====")
#         print(spoken_text + metadata_buffer)
#         print("==========================\n")

#         self._extract_tags(metadata_buffer)
#         self._apply_score()
#         self._handle_signal()
#         self._advance_phase()

#         if self.current_phase != "end":
#             await self.update_instructions(self._build_instructions())

#         print(f"[STATE] phase={self.current_phase} | prompt={self.prompt_mode} | score={self.score} | signal={self.current_signal} | value_step={self.value_step}")

#         if self.current_phase == "end":
#             self._cancel_inactivity_timer()
#             await self._handle_end()
#             return
        
#         if self.prompt_mode == "hard_no_recovery" and self.hard_no_attempts == 1:
#             if self._session_ref:
#                 await self._session_ref.generate_reply(
#                     instructions=PHASE_PROMPTS["hard_no_recovery"]
#                 )

#         # TTS just finished streaming — start inactivity timer
#         self._start_inactivity_timer()

#     # -------------------------
#     # Tag extraction
#     # -------------------------

#     def _extract_tags(self, text: str):
#         def extract(tag):
#             match = re.search(f"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
#             return match.group(1).strip() if match else None

#         signal = extract("signal")

#         self.current_signal = signal if signal else "neutral"
#         print(f"[SIGNAL] {self.current_signal}")

#     # -------------------------
#     # Score
#     # -------------------------

#     def _apply_score(self):
#         delta = SIGNAL_DELTAS.get(self.current_signal, 0)
#         self.score = max(0, min(100, self.score + delta))
#         print(f"[SCORE] {self.score}")

#     # -------------------------
#     # End
#     # -------------------------

#     async def _handle_end(self):
#         print("\n========== CALL ENDED ==========")
#         print(f"Final score  : {self.score}")
#         print(f"Final phase  : {self.current_phase}")
#         print(f"Previous     : {self.previous_phase}")
#         print(f"Value step   : {self.value_step}")
#         print("TODO: build post-call JSON here and send to backend")
#         print("================================\n")
#         if self._session_ref:
#             await self._session_ref.aclose()


# # -------------------------
# # Entrypoint
# # -------------------------

# async def entrypoint(ctx: agents.JobContext):

#     session = AgentSession(
#         stt="elevenlabs/scribe_v2_realtime",
#         llm="deepseek-ai/deepseek-v3.1",
#         tts="elevenlabs/eleven_turbo_v2_5:cgSgspJ2msm6clMCkdW9",
#         vad=silero.VAD.load(),
#         turn_handling=TurnHandlingOptions(
#             turn_detector=MultilingualModel(),
#             interruption={"mode": "vad"},
#         ),
#         preemptive_generation=False,
#     )

#     assistant = Assistant()
#     assistant._session_ref = session

#     await session.start(
#         room=ctx.room,
#         agent=assistant,
#         room_input_options=RoomInputOptions(
#             noise_cancellation=noise_cancellation.BVC(),
#         ),
#     )

#     turn_metrics: dict[str, dict[str, float]] = defaultdict(dict)

#     @session.on("metrics_collected")
#     def on_metrics_collected(ev: MetricsCollectedEvent):
#         m = ev.metrics
#         sid = getattr(m, "speech_id", None)
#         if not sid:
#             return
#         if isinstance(m, EOUMetrics):
#             turn_metrics[sid]["eou_delay"] = m.end_of_utterance_delay
#         elif isinstance(m, LLMMetrics):
#             turn_metrics[sid]["llm_ttft"] = m.ttft
#             turn_metrics[sid]["llm_total"] = m.duration
#         elif isinstance(m, TTSMetrics):
#             turn_metrics[sid]["tts_ttfb"] = m.ttfb
#             turn_metrics[sid]["tts_total"] = m.duration

#     @session.on("conversation_item_added")
#     def on_conversation_item_added(ev):
#         if not isinstance(ev.item, ChatMessage):
#             return
#         if ev.item.role != "assistant":
#             return
#         m = ev.item.metrics
#         if not m:
#             return
#         e2e = m.get("e2e_latency")
#         if e2e is not None:
#             print("\n======== LATENCY REPORT ========")
#             print(f"E2E Latency: {e2e:.3f}s")
#             speech_id = getattr(ev.item, "speech_id", None)
#             parts = turn_metrics.get(speech_id, {})
#             for k, v in parts.items():
#                 print(f"{k}: {v:.3f}s")
#             print("================================\n")

#     await session.generate_reply(
#         instructions=assistant._build_instructions()
#     )


# if __name__ == "__main__":
#     agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
# #  from dotenv import load_dotenv
# # import asyncio
# # import re
# # from collections import defaultdict

# # from livekit import agents
# # from livekit.agents import (
# #     AgentSession,
# #     Agent,
# #     RoomInputOptions,
# #     TurnHandlingOptions,
# #     llm,
# #     MetricsCollectedEvent,
# # )
# # from livekit.plugins import noise_cancellation, silero
# # from livekit.plugins.turn_detector.multilingual import MultilingualModel
# # from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics
# # from livekit.agents.llm import ChatMessage

# # from testprompt import BASE_PERSONALITY
# # from phase_prompt import PHASE_PROMPTS

# # load_dotenv(".env.local")


# # SIGNAL_DELTAS = {
# #     "positive":    +10,
# #     "neutral":      +5,
# #     "objection":    +5,
# #     "disengaging": -10,
# #     "hard_no":     -20,
# #     "end_call":      0,
# # }


# # class Assistant(Agent):

# #     def __init__(self):
# #         self.current_phase = "hook"
# #         self.previous_phase = None

# #         self.prompt_mode = "hook"
# #         self.return_to_prompt = None

# #         self.current_signal = None
# #         self.score = 0

# #         self.consecutive_objections = 0
# #         self.disengaging_attempts = 0
# #         self.hard_no_attempts = 0      # how many times hard_no received

# #         self._session_ref = None

# #         super().__init__(instructions=self._build_instructions())

# #     # -------------------------
# #     # Instructions
# #     # -------------------------

# #     def _build_instructions(self) -> str:
# #         prompt = BASE_PERSONALITY
# #         prompt += f"\n\n---\nCurrent Phase: {self.current_phase.upper()}"
# #         prompt += f"\n\n---\n{PHASE_PROMPTS[self.prompt_mode]}"
# #         return prompt

# #     # -------------------------
# #     # Phase control
# #     # -------------------------

# #     def _go_to(self, phase: str):
# #         self.previous_phase = self.current_phase
# #         self.current_phase = phase
# #         self.prompt_mode = phase
# #         print(f"[PHASE] {self.previous_phase} → {self.current_phase}")

# #     def _handle_signal(self):

# #         # --- end_call: user explicitly said disconnect ---
# #         # no recovery attempt, just end cleanly
# #         if self.current_signal == "end_call":
# #             print("[END] user requested disconnect")
# #             self._go_to("end")
# #             return

# #         # --- hard_no: strong refusal ---
# #         # first hard_no → one recovery attempt via hard_no prompt
# #         # second hard_no → end
# #         if self.current_signal == "hard_no":
# #             self.hard_no_attempts += 1
# #             if self.hard_no_attempts >= 2:
# #                 print("[END] hard_no after recovery attempt → ending")
# #                 self._go_to("end")
# #             else:
# #                 print("[PROMPT] first hard_no → recovery attempt")
# #                 self.return_to_prompt = self.prompt_mode
# #                 self.prompt_mode = "hard_no_recovery"
# #             return

# #         # --- objection: persistent = 2 turns in a row ---
# #         if self.current_signal == "objection":
# #             self.consecutive_objections += 1
# #             self.hard_no_attempts = 0
# #             if self.consecutive_objections >= 2 and self.prompt_mode != "objection":
# #                 self.return_to_prompt = self.prompt_mode
# #                 self.prompt_mode = "objection"
# #                 print(f"[PROMPT] persistent objection → objection mode (return to {self.return_to_prompt})")
# #             return

# #         # --- disengaging: one re-engagement attempt ---
# #         if self.current_signal == "disengaging":
# #             self.consecutive_objections = 0
# #             self.hard_no_attempts = 0
# #             self.disengaging_attempts += 1
# #             if self.disengaging_attempts >= 2:
# #                 print("[END] disengaging 2x → ending")
# #                 self._go_to("end")
# #             else:
# #                 if self.prompt_mode != "objection":
# #                     self.return_to_prompt = self.prompt_mode
# #                     self.prompt_mode = "objection"
# #                     print("[PROMPT] disengaging → objection re-engagement")
# #             return

# #         # --- positive or neutral: reset counters, return from recovery ---
# #         if self.current_signal in ("positive", "neutral"):
# #             self.consecutive_objections = 0
# #             self.disengaging_attempts = 0
# #             self.hard_no_attempts = 0
# #             if self.prompt_mode in ("objection", "hard_no_recovery") and self.return_to_prompt:
# #                 self.prompt_mode = self.return_to_prompt
# #                 self.return_to_prompt = None
# #                 print(f"[PROMPT] resolved → back to {self.prompt_mode}")

# #     def _advance_phase(self):
# #         if self.current_phase == "end":
# #             return

# #         if self.current_phase == "hook":
# #             if self.current_phase != "end":
# #                 self._go_to("engage")

# #         elif self.current_phase == "engage":
# #             if self.score >= 55 and self.prompt_mode != "objection":
# #                 self._go_to("value")

# #         elif self.current_phase == "value":
# #             if self.score < 40 and self.prompt_mode != "objection":
# #                 self._go_to("engage")

# #     # -------------------------
# #     # LLM node
# #     # -------------------------

# #     async def llm_node(self, chat_ctx, tools, model_settings=None):
# #         metadata_buffer = ""
# #         metadata_mode = False
# #         spoken_text = ""

# #         async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
# #             if not isinstance(chunk, llm.ChatChunk) or not chunk.delta:
# #                 yield chunk
# #                 continue

# #             content = getattr(chunk.delta, "content", None)
# #             if not content:
# #                 yield chunk
# #                 continue

# #             if metadata_mode:
# #                 metadata_buffer += content
# #                 continue

# #             tag_start = content.find("<")
# #             if tag_start != -1:
# #                 spoken_part = content[:tag_start]
# #                 if spoken_part.strip():
# #                     spoken_text += spoken_part
# #                     chunk.delta.content = spoken_part
# #                     yield chunk
# #                 metadata_mode = True
# #                 metadata_buffer += content[tag_start:]
# #             else:
# #                 spoken_text += content
# #                 yield chunk

# #         print("\n===== RAW LLM OUTPUT =====")
# #         print(spoken_text + metadata_buffer)
# #         print("==========================\n")

# #         self._extract_tags(metadata_buffer)
# #         self._apply_score()
# #         self._handle_signal()
# #         self._advance_phase()

# #         if self.current_phase != "end":
# #             await self.update_instructions(self._build_instructions())

# #         print(f"[STATE] phase={self.current_phase} | prompt={self.prompt_mode} | score={self.score} | signal={self.current_signal}")

# #         if self.current_phase == "end":
# #             await self._handle_end()

# #     # -------------------------
# #     # Tag extraction
# #     # -------------------------

# #     def _extract_tags(self, text: str):
# #         def extract(tag):
# #             match = re.search(f"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
# #             return match.group(1).strip() if match else None

# #         signal = extract("signal")
# #         score = extract("score_delta")

# #         self.current_signal = signal if signal else "neutral"
# #         print(f"[SIGNAL] {self.current_signal}")

# #         try:
# #             self._pending_score_delta = int(score) if score else None
# #         except ValueError:
# #             self._pending_score_delta = None

# #     # -------------------------
# #     # Score
# #     # -------------------------

# #     def _apply_score(self):
# #         delta = (
# #             self._pending_score_delta
# #             if self._pending_score_delta is not None
# #             else SIGNAL_DELTAS.get(self.current_signal, 0)
# #         )
# #         self.score = max(0, min(100, self.score + delta))
# #         print(f"[SCORE] {self.score}")

# #     # -------------------------
# #     # End
# #     # -------------------------

# #     async def _handle_end(self):
# #         print("\n========== CALL ENDED ==========")
# #         print(f"Final score : {self.score}")
# #         print(f"Final phase : {self.current_phase}")
# #         print(f"Previous    : {self.previous_phase}")
# #         print("TODO: build post-call JSON here and send to backend")
# #         print("================================\n")
# #         if self._session_ref:
# #             await self._session_ref.aclose()


# # # -------------------------
# # # Entrypoint
# # # -------------------------

# # async def entrypoint(ctx: agents.JobContext):

# #     session = AgentSession(
# #         stt="elevenlabs/scribe_v2_realtime",
# #         llm="deepseek-ai/deepseek-v3.1",
# #         tts="elevenlabs/eleven_turbo_v2_5:cgSgspJ2msm6clMCkdW9",
# #         vad=silero.VAD.load(),
# #         turn_handling=TurnHandlingOptions(
# #             turn_detector=MultilingualModel(),
# #             interruption={"mode": "vad"},
# #         ),
# #         preemptive_generation=False,
# #     )

# #     assistant = Assistant()
# #     assistant._session_ref = session

# #     await session.start(
# #         room=ctx.room,
# #         agent=assistant,
# #         room_input_options=RoomInputOptions(
# #             noise_cancellation=noise_cancellation.BVC(),
# #         ),
# #     )

# #     turn_metrics: dict[str, dict[str, float]] = defaultdict(dict)

# #     @session.on("metrics_collected")
# #     def on_metrics_collected(ev: MetricsCollectedEvent):
# #         m = ev.metrics
# #         sid = getattr(m, "speech_id", None)
# #         if not sid:
# #             return
# #         if isinstance(m, EOUMetrics):
# #             turn_metrics[sid]["eou_delay"] = m.end_of_utterance_delay
# #         elif isinstance(m, LLMMetrics):
# #             turn_metrics[sid]["llm_ttft"] = m.ttft
# #             turn_metrics[sid]["llm_total"] = m.duration
# #         elif isinstance(m, TTSMetrics):
# #             turn_metrics[sid]["tts_ttfb"] = m.ttfb
# #             turn_metrics[sid]["tts_total"] = m.duration

# #     @session.on("conversation_item_added")
# #     def on_conversation_item_added(ev):
# #         if not isinstance(ev.item, ChatMessage):
# #             return
# #         if ev.item.role != "assistant":
# #             return
# #         m = ev.item.metrics
# #         if not m:
# #             return
# #         e2e = m.get("e2e_latency")
# #         if e2e is not None:
# #             print("\n======== LATENCY REPORT ========")
# #             print(f"E2E Latency: {e2e:.3f}s")
# #             speech_id = getattr(ev.item, "speech_id", None)
# #             parts = turn_metrics.get(speech_id, {})
# #             for k, v in parts.items():
# #                 print(f"{k}: {v:.3f}s")
# #             print("================================\n")

# #     await session.generate_reply(
# #         instructions=assistant._build_instructions()
# #     )


# # if __name__ == "__main__":
# #     agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))