# Navi — voice-controlled browser agent

Talk to your browser. Navi opens sites, searches, reads pages, clicks with a visible red cursor,
scrolls, fills forms, manages tabs, scrapes data to CSV/JSON, and runs multi-source deep research
in a background tab — all from voice commands, using OpenAI's Realtime API.

```
 You (mic) ──► GPT Realtime ──tool calls──► ToolRegistry ──► Browser (Playwright → Edge, visible)
   ▲              │   ▲                         │                 ├─ numbered element snapshot
   └── speaker ◄──┘   └──── page state ◄────────┘                 ├─ screenshots w/ numbered boxes
                                                                  ├─ cursor moves + clicks
                       ResearchManager (background tab) ──► text model ──► cited Markdown report
                       scrape.extract_data ──► text model ──► output/data/*.csv + .json
```

## Why it behaves better than a typical voice agent

| Problem | What Navi does |
|---|---|
| Responds to random sounds / TV / its own voice | **Push-to-talk by default**: audio only reaches the model while you hold the key. Hands-free mode is *gated*: it waits for the transcript, drops noise, fillers and phantom transcripts ("Thank you.", "Thanks for watching"), supports a wake word, and mutes the mic while Navi speaks. Rejected audio is deleted from the conversation so it can't confuse later turns. |
| Clicks the wrong thing / "can't find" buttons | The model never guesses. Each page becomes a numbered list of visible, un-covered interactive elements (including shadow DOM). Every action returns the fresh page state. `look` adds a screenshot with the same numbers drawn on it. |
| Old commands keep running after you change your mind | Speaking (or `/stop`) cancels the reply and abandons the in-flight tool chain. |
| You can't tell what went wrong | The console prints your transcript, every tool call, and its result. |
| Agent submits/buys/sends on its own | A **hard gate in code**: consequential clicks (submit, pay, send, delete, post, apply…) are refused until *you* say yes after Navi asks. The model setting `confirmed=true` alone isn't enough. |
| Typing secrets | Password, card, CVV, SSN and account fields are blocked — Navi asks you to type them. |
| Prompt injection from web pages | Page text is treated as data in the system prompt and in every text-model call. |

## Setup (Windows)

Requires Python 3.10+ and a microphone. Google Chrome is used by default (set `BROWSER_CHANNEL=msedge` for Edge), so no browser download is needed.

```powershell
cd voice_browser_agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then put your OPENAI_API_KEY in .env
python main.py --mic-test   # make sure the bars move when you talk
python main.py
```

To use Playwright's own Chromium instead of Edge, set `BROWSER_CHANNEL=` (empty) and run
`playwright install chromium`. For Chrome, set `BROWSER_CHANNEL=chrome`.

Navi uses its own browser profile (`.browser_profile/`), so sign in to sites once inside the Navi window
and the logins persist. Keep that folder private — it holds your cookies.

## Running

```powershell
python main.py                           # push-to-talk: hold Right Ctrl, speak, release
python main.py --ptt-key f8              # different key (ctrl_r, alt_r, f8, f9, caps_lock, a letter)
python main.py --mode vad                # hands-free (gated)
python main.py --mode vad --wake-word navi    # hands-free, only "Navi, ..." commands
python main.py --mode vad --headphones   # hands-free and you can talk over Navi
python main.py --mode text               # type instead of talk
```

Console commands in any mode: type a command, `/stop`, `/mute`, `/quit`.

## Things to say

- "Open YouTube and search for lo-fi study music, play the second result."
- "Go to Amazon, search wireless earbuds under 50 dollars, sort by rating."
- "What's on this page?" · "Read me the main article." · "Scroll down." · "Go back."
- "Move the cursor to the sign-in button." · "Click the third result." · "Open that in a new tab."
- "Fill this form: name Revanth, email …, size large, tick the newsletter box." → Navi fills, tells you, and asks before submitting.
- "Scrape all the job postings on this page with title, company, location and link."
- "Save the table on this page." · "Save all the links that mention pricing."
- "Do deep research on the best vector databases for RAG in 2026 and compare them." → runs in a background tab; Navi tells you when the report is ready in `output/research/`.

## Files

| File | Role |
|---|---|
| `main.py` | Entry point, CLI flags, mic test, reconnects |
| `voice.py` | Realtime websocket session, push-to-talk, gated VAD, audio in/out, tool loop |
| `tools.py` | System instructions, 23 tool schemas, dispatcher |
| `browser.py` | Playwright controller: perception, cursor, click/type/scroll/forms/tabs |
| `page_scripts.py` | JavaScript injected into pages (element snapshot, cursor, overlays, text) |
| `safety.py` | Confirmation gate for consequential clicks |
| `research.py` | Background deep research → cited report |
| `scrape.py` | AI extraction, table and link scraping → CSV/JSON |
| `llm.py` | Text model wrapper |

## Tuning

- **Push-to-talk ignores you** → run `--mic-test`. If speech stays under ~200, raise the mic level in
  Windows Sound settings or lower `self.min_level` in `voice.py`.
- **Hands-free still too eager** → add `--wake-word navi`, or extend `PHANTOMS` in `voice.py` with
  whatever junk transcripts you see as `skip` lines in the console.
- **Model choices** live in `.env` (`REALTIME_MODEL`, `TEXT_MODEL`, `TRANSCRIBE_MODEL`, `VOICE`).
- **Risky-button words** are in `safety.py` (`RISKY` / `SAFE`).

## Known limits

- Elements inside iframes aren't in the numbered list; Navi falls back to `look` + `click_at` (coordinates).
- CAPTCHAs and bot checks are left to you on purpose. Google may challenge automated searches; research uses
  DuckDuckGo then Bing.
- The confirmation gate covers clicks and pressing Enter inside a form. A site that submits through custom
  JavaScript on some other key or gesture could slip past it; risky-word lists are in `safety.py`.
- Respect each site's terms when scraping.
