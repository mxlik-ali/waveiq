# 🎙️ WaveIQ — AI Voice Sales Agent

An AI-powered outbound voice agent for Rupeezy partner acquisition, built on LiveKit Agents. The agent conducts real-time multilingual (Hindi/English/Hinglish) sales calls, tracks lead signals, and auto-generates structured post-call reports saved to Supabase.

---

## 🗂️ Project Structure

```
WaveIQ/
└── voice-agent/
    ├── main.py                  # Agent entrypoint (LiveKit)
    ├── pyproject.toml           # Project dependencies
    ├── .env.local               # API keys (you create this)
    ├── prompts/
    │   ├── prompt.py            # Base agent personality
    │   ├── phase_prompt.py      # Per-phase instructions (hook → close)
    │   └── report_prompt.py     # LLM prompt for report generation
    ├── helpers/
    │   ├── generate_report.py   # Gemini-powered call report generator
    │   ├── write_json.py        # JSONL conversation logger
    │   └── run_report.py        # Manual report re-run from saved logs
    └── send_report.py           # FastAPI server to save reports to Supabase
```

---

## 📋 Prerequisites

- **macOS / Linux / Windows** (WSL recommended on Windows)
- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- **[LiveKit Cloud](https://cloud.livekit.io)** account
- **[Google Gemini API](https://aistudio.google.com/app/apikey)** key
- **[Supabase](https://supabase.com)** project (with `call_reports` table)
- **[ElevenLabs](https://elevenlabs.io)** API key (for STT + TTS)
- **[DeepSeek](https://platform.deepseek.com)** API key (for LLM)

---

## 🚀 Setup Guide

### Step 1 — Create a LiveKit Cloud Project

1. Go to **[livekit.io](https://livekit.io)** and sign up / log in
2. Click **New Project** and give it a name (e.g. `waveiq`)
3. Note your project's **API Key**, **API Secret**, and **WebSocket URL** — you'll need these later

---

### Step 2 — Install `uv`

`uv` is the package manager used for this project.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify installation:

```bash
uv --version
```

---

### Step 3 — Install the LiveKit CLI

The CLI is used to link your local project to your LiveKit Cloud account.

```bash
# macOS (Homebrew)
brew install livekit-cli

# Linux / macOS (curl)
curl -sSL https://get.livekit.io/cli | bash

# Windows (winget)
winget install LiveKit.LiveKitCLI
```

---

### Step 4 — Authenticate the CLI with Your Cloud Account

```bash
lk cloud auth
```

This will open a browser window. Log in and **grant access to your project**. The CLI will link to the project you created in Step 1.

---

### Step 5 — Clone & Enter the Project

```bash
cd WaveIQ/voice-agent
```

---

### Step 6 — Install All Dependencies

Run the following in one go:

```bash
uv add \
  "livekit-agents[silero,turn-detector]~=1.5" \
  "livekit-plugins-noise-cancellation~=0.2" \
  "python-dotenv" \
  "google-genai>=1.75.0" \
  "google-generativeai>=0.8.6" \
  "supabase>=2.30.0" \
  "fastapi>=0.136.1" \
  "uvicorn>=0.46.0" \
  "httpx>=0.28.1" \
  "asyncpg>=0.31.0" \
  "psycopg2-binary>=2.9.12" \
  "hf-transfer>=0.1.9"
```

---

### Step 7 — Pull LiveKit Environment Variables

This command auto-populates your `.env.local` with the LiveKit keys tied to your authenticated project:

```bash
lk add env -w
```

This writes `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` into `.env.local`. Confirm the file was created:

```bash
cat .env.local
```

---

### Step 8 — Get Your Google Gemini API Key

The report generator uses **Gemini 2.5 Flash** to analyze call conversations.

1. Go to **[Google AI Studio](https://aistudio.google.com/app/apikey)**
2. Sign in with your Google account
3. Click **Create API Key** → select or create a Google Cloud project
4. Copy the key

Add it to `.env.local`:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

---

### Step 9 — Set Up Supabase

The agent saves post-call reports to a Supabase table.

1. Go to **[supabase.com](https://supabase.com)** and create a new project
2. In your project dashboard, go to **Settings → API**
3. Copy:
   - **Project URL** (e.g. `https://xxxx.supabase.co`)
   - **Service Role Key** (under *Project API Keys* — use `service_role`, not `anon`)

4. In the **SQL Editor**, create the reports table:

```sql
create table call_reports (
  id uuid primary key default gen_random_uuid(),
  call_id text,
  lead_id text,
  data jsonb,
  created_at timestamptz default now()
);
```

Add to `.env.local`:

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your_service_role_key_here
```

---

### Step 10 — Add ElevenLabs & DeepSeek Keys

The agent uses ElevenLabs for real-time STT/TTS and DeepSeek for the conversational LLM.

- **ElevenLabs** → [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys)
- **DeepSeek** → [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)

Add both to `.env.local`:

```env
ELEVEN_API_KEY=your_elevenlabs_key_here
DEEPSEEK_API_KEY=your_deepseek_key_here
```

---

### Step 11 — Final `.env.local` Reference

Your completed `.env.local` should look like this:

```env
# LiveKit (auto-filled by `lk add env -w`)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Google Gemini (report generation)
GEMINI_API_KEY=your_gemini_api_key

# Supabase (report storage)
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your_service_role_key

# ElevenLabs (STT + TTS)
ELEVEN_API_KEY=your_elevenlabs_api_key

# DeepSeek (LLM)
DEEPSEEK_API_KEY=your_deepseek_api_key
```

> ⚠️ Never commit `.env.local` to git. Add it to `.gitignore`.

---

## ▶️ Running the Agent

### Start the report API server (in one terminal):

```bash
uvicorn send_report:app --reload --port 8000
```

### Start the voice agent (in another terminal):

```bash
uv run python main.py dev
```

The agent will connect to your LiveKit room and begin handling inbound connections.

---

## 🔁 Re-running a Report from a Saved Log

If a call completed but the report wasn't generated, you can replay it:

```bash
uv run python helpers/run_report.py
```

Update the `path` variable inside `run_report.py` to point to the correct `.jsonl` file in `/logs`.

---

## 🧠 How It Works

| Component | Role |
|---|---|
| **LiveKit Agents** | Real-time voice session management |
| **ElevenLabs Scribe** | Multilingual speech-to-text |
| **DeepSeek v3.1** | Conversational LLM for the agent |
| **ElevenLabs TTS** | Natural voice output |
| **Silero VAD** | Voice activity detection |
| **Multilingual Turn Detector** | Handles Hindi/English/Hinglish turn-taking |
| **Gemini 2.5 Flash** | Post-call report analysis |
| **Supabase** | Report storage (JSONB) |

### Call Phase Flow

```
hook → engage → value (3 steps) → close
         ↕            ↕
      objection   value_reengagement
         ↕
    hard_no_recovery → end
```

The agent scores leads in real time (0–100) based on signal tags returned by the LLM. Phase transitions are driven by score thresholds and signal patterns.

---

## 📊 Post-Call Report Fields

Each report includes:

- **Lead profile** — name, city, partner type, language
- **Benefits covered** — which of the 4 Rupeezy benefits were pitched
- **Objections** — type, lead quote, whether resolved
- **Qualification** — score (0–100), Hot/Warm/Cold classification, readiness signals
- **Outcome** — result, CTA given/accepted, callback time
- **RM Handoff brief** — summary, unresolved objections, do-not-repitch list
- **Post-call summary** — 2–3 sentence manager overview

---

## 📁 Logs

Every call is logged turn-by-turn to `logs/<call_id>.jsonl`. Each line is a JSON object:

```json
{"role": "user", "text": "haan batao"}
{"role": "assistant", "text": "Nice...", "signal": "positive", "score": 25, "phase": "engage", "value_step": 0}
```
