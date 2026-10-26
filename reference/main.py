"""Navi — voice-controlled browser agent.

    python main.py                 # push-to-talk (hold Right Ctrl)
    python main.py --mode vad      # hands-free, gated by transcript
    python main.py --mode vad --wake-word navi
    python main.py --mode text     # type commands, no mic
    python main.py --mic-test      # check your microphone levels
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from config import Config


def mic_test(seconds: int = 6) -> None:
    import numpy as np
    import sounddevice as sd
    print(sd.query_devices())
    print(f"\nDefault input: {sd.query_devices(kind='input')['name']}")
    print(f"Speak normally for {seconds}s. Bars should jump when you talk and stay low when you don't.\n")

    def cb(indata, frames, t, status):
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        print(f"\r{int(rms):>6} " + "#" * min(60, int(rms / 100)) + " " * 20, end="", flush=True)

    with sd.InputStream(samplerate=24000, channels=1, dtype="int16", blocksize=2400, callback=cb):
        sd.sleep(seconds * 1000)
    print("\n\nIf speech stays under ~200, raise your mic level in Windows Sound settings.")


async def amain(args) -> None:
    from browser import Browser
    from llm import LLM
    from research import ResearchManager
    from safety import ConfirmationGate
    from tools import ToolRegistry
    from voice import VoiceSession
    from websockets.exceptions import WebSocketException

    cfg = Config.load()
    if args.ptt_key:
        cfg.ptt_key = args.ptt_key
    gate = ConfirmationGate()
    browser = Browser(cfg, gate)
    await browser.start()
    llm = LLM(cfg)

    session_ref: dict = {}

    async def notify(text: str):
        s = session_ref.get("s")
        if s:
            await s.notify(text)

    research = ResearchManager(browser, llm, cfg.output_dir, notify)
    registry = ToolRegistry(browser, llm, research, cfg.output_dir)
    session = VoiceSession(cfg, registry, gate, mode=args.mode, headphones=args.headphones,
                           wake_word=args.wake_word)
    session_ref["s"] = session
    try:
        while True:
            try:
                await session.run()
                break  # /quit
            except (OSError, ConnectionError, WebSocketException) as e:
                print(f"Connection lost ({e}); reconnecting in 3s...")
                await asyncio.sleep(3)
    finally:
        await browser.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Voice-controlled browser agent")
    p.add_argument("--mode", choices=["ptt", "vad", "text"], default="ptt")
    p.add_argument("--headphones", action="store_true", help="vad mode: keep mic open while the agent talks")
    p.add_argument("--wake-word", default=None, help="vad mode: only act on utterances containing this word")
    p.add_argument("--ptt-key", default=None, help="push-to-talk key, e.g. ctrl_r, alt_r, f8, caps_lock")
    p.add_argument("--mic-test", action="store_true")
    args = p.parse_args()
    if args.mic_test:
        mic_test()
        return
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\nBye.")
        sys.exit(0)


if __name__ == "__main__":
    main()
