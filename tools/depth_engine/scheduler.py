"""Which desktops get the GPU next. Only a desktop with a new, different picture is ever considered, so a still desktop
costs nothing; among the others the most overdue goes first, and the focused desktop may run at a higher rate."""
from __future__ import annotations


class Scheduler:
    def __init__(self, focused_hz: float = 60.0, other_hz: float = 30.0, batch: int = 4, slack: float = 0.004):
        self.focused_period = 1.0 / focused_hz
        self.other_period = 1.0 / other_hz
        self.batch = batch
        self.slack = slack  # Seconds early a desktop may join a batch that is going anyway.
        self._last: dict[int, float] = {}

    def pick(self, now: float, ready: list[int], focused: set[int]) -> list[int]:
        """Up to `batch` of the ready channels that are due, most overdue first. If any is due, those about to be due
        come along: a desktop that just missed a batch would otherwise wait a whole extra period."""
        lateness = {}
        for ch in ready:
            period = self.focused_period if ch in focused else self.other_period
            last = self._last.get(ch)
            lateness[ch] = float("inf") if last is None else (now - last) - period
        if not lateness or max(lateness.values()) < 0:
            return []
        due = sorted((-late, ch) for ch, late in lateness.items() if late >= -self.slack)
        return [ch for _, ch in due[: self.batch]]

    def ran(self, channels: list[int], now: float) -> None:
        for ch in channels:
            self._last[ch] = now

    def forget(self, ch: int) -> None:
        self._last.pop(ch, None)
