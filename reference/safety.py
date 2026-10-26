"""Hard confirmation gate for consequential clicks.

The model cannot bypass this by itself: a gated action only goes through when
(1) the gate was raised for that exact element, and (2) the *user* said
the single word "confirm" after it was raised. The model's own `confirmed=true`
is necessary but not sufficient.
"""
from __future__ import annotations

import asyncio
import re
import time

RISKY = re.compile(
    r"\b(pay|payment|purchase|buy|order|checkout|check out|submit|send|delete|remove|confirm|transfer|"
    r"sign up|register|post|publish|apply|book|reserve|subscribe|unsubscribe|donate|withdraw|cancel (my )?"
    r"(account|subscription|order)|tweet|reply|save changes)\b",
    re.I,
)
SAFE = re.compile(r"\b(search|find|go|filter|sort|next|previous|prev|more|load|show|view|sign in|log ?in|continue reading)\b", re.I)
# Approval is ONLY the whole utterance "confirm" (case and surrounding punctuation ignored).
# "yes", "okay", "go ahead", "sure", "do it" and "confirm" inside a longer sentence do not approve.
APPROVAL_WORD = "confirm"


def is_approval(text: str) -> bool:
    return (text or "").strip().strip(" \t\r\n.,!?;:\"'").lower() == APPROVAL_WORD


# A held action must be approved within this many seconds of the read-back.
CONFIRM_TTL = 30.0

NEGATE = re.compile(r"\b(no|nope|don'?t|do not|stop|cancel|wait|hold on|never ?mind)\b", re.I)


def is_risky(info: dict | None) -> bool:
    if not info:
        return False
    label = info.get("label", "") or ""
    if RISKY.search(label):
        return True
    return bool(info.get("isSubmit")) and not SAFE.search(label)


class ConfirmationGate:
    def __init__(self) -> None:
        self.pending: dict[str, float] = {}
        self._last_affirm = 0.0
        self._user_event = asyncio.Event()

    def note_user(self, text: str) -> None:
        """Call with every user utterance / typed line."""
        now = time.monotonic()
        if is_approval(text):
            self._last_affirm = now
        else:
            # Any other utterance cancels an unused "confirm" so it cannot carry over.
            self._last_affirm = 0.0
            # ...and so does the held action: after any other command it must be read back again.
            self.pending.clear()
        self._user_event.set()

    async def check(self, key: str, info: dict | None, confirmed: bool) -> str | None:
        """Return None if allowed, else a message for the model."""
        if not is_risky(info):
            return None
        issued = self.pending.get(key)
        if issued is not None and time.monotonic() - issued > CONFIRM_TTL:
            self.pending.pop(key, None)  # expired: must be read back again
            issued = None
        if confirmed and issued is not None:
            # The user's transcript can arrive a moment after the model reacts to the audio.
            deadline = time.monotonic() + 4.0
            while self._last_affirm <= issued and time.monotonic() < deadline:
                self._user_event.clear()
                try:
                    await asyncio.wait_for(self._user_event.wait(), deadline - time.monotonic())
                except asyncio.TimeoutError:
                    break
            now = time.monotonic()
            if self._last_affirm > issued and now - self._last_affirm <= CONFIRM_TTL and now - issued <= CONFIRM_TTL:
                # One "confirm" approves exactly one action: consume it and drop other held actions.
                self._last_affirm = 0.0
                self.pending.clear()
                return None
        self.pending[key] = time.monotonic()
        what = (info or {}).get("label") or "this button"
        return (
            f"CONFIRMATION_REQUIRED: clicking '{what}' looks consequential (submit/send/pay/delete/etc). "
            "Tell the user exactly what will happen and ask them to say the single word 'confirm'. "
            "Only 'confirm' approves: 'yes', 'okay' and 'go ahead' do not. After they say 'confirm', "
            "call this same tool again with the same target and confirmed=true. If they say anything else, don't click."
        )
