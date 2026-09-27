"""Account locks: one browser operation per site account at a time (spec 4.5 / 15.5)."""

from __future__ import annotations

import threading


class AccountLocks:
    def __init__(self) -> None:
        self._held: dict[int, str] = {}
        self._mutex = threading.Lock()

    def try_acquire(self, account_id: int, holder: str) -> bool:
        with self._mutex:
            if account_id in self._held:
                return False
            self._held[account_id] = holder
            return True

    def release(self, account_id: int) -> None:
        with self._mutex:
            self._held.pop(account_id, None)

    def is_locked(self, account_id: int) -> bool:
        with self._mutex:
            return account_id in self._held

    def holder(self, account_id: int) -> str | None:
        with self._mutex:
            return self._held.get(account_id)


account_locks = AccountLocks()
