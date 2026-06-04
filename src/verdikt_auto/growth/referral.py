"""Referral system — tracks and rewards referrals."""

import logging
import secrets
from typing import Optional

logger = logging.getLogger(__name__)


class ReferralSystem:
    """Manages referral links and rewards."""

    def __init__(self) -> None:
        self._codes: dict[str, str] = {}
        self._referrals: dict[str, list[str]] = {}

    def generate_code(self, user_id: str) -> str:
        code = secrets.token_hex(4)
        self._codes[code] = user_id
        self._referrals.setdefault(user_id, [])
        logger.info("Referral code generated for %s: %s", user_id, code)
        return code

    def apply_referral(self, code: str, new_user_id: str) -> Optional[str]:
        referrer = self._codes.get(code)
        if referrer is None:
            logger.warning("Invalid referral code: %s", code)
            return None
        if new_user_id in self._referrals.get(referrer, []):
            logger.warning("Duplicate referral from %s", new_user_id)
            return None
        self._referrals.setdefault(referrer, []).append(new_user_id)
        logger.info("Referral applied: %s referred %s", referrer, new_user_id)
        return referrer

    def get_referral_count(self, user_id: str) -> int:
        return len(self._referrals.get(user_id, []))

    def get_referral_bonus(self, count: int) -> str:
        if count >= 10:
            return "🎁 Premium-доступ на месяц"
        if count >= 5:
            return "🎁 Эксклюзивный контент"
        if count >= 3:
            return "🎁 Стикер-пак"
        return ""
