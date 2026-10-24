"""凪APIの通貨取引とSQLite戦闘データを冪等に連携する。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from weapon_bot.bosses.store import BossStore
from weapon_bot.database import WeaponMirror
from weapon_bot.nagi_api import NagiApi, NagiApiError


logger = logging.getLogger("weapon-bot.bosses")


class BossService:
    def __init__(self, api: NagiApi, mirror: WeaponMirror, store: BossStore) -> None:
        self.api = api
        self.mirror = mirror
        self.store = store
        self._action_lock = asyncio.Lock()
        self._reward_lock = asyncio.Lock()

    async def get_forge(self, user_id: str) -> dict[str, Any]:
        data = await self.api.get_forge(user_id)
        try:
            self.mirror.save_forge(user_id, data)
        except Exception:
            logger.exception("ボス挑戦前のSQLite武器ミラー保存に失敗しました")
        return data

    async def perform_action(
        self,
        *,
        interaction_id: str,
        user_id: str,
        action_type: str,
        cost: int,
        damage: int = 0,
        is_critical: bool = False,
        counter_damage: int = 0,
        heal_amount: int = 0,
    ) -> dict[str, Any]:
        """ローカルに意図を先に記録し、同じIDで凪APIへ課金を依頼する。"""
        async with self._action_lock:
            same_interaction = self.store.get_action(interaction_id)
            if same_interaction:
                return await self._resume_action_locked(interaction_id)
            pending = self.store.get_pending_action_for_user(user_id)
            if pending:
                result = await self._resume_action_locked(pending["interaction_id"])
                result["recovered_previous_action"] = True
                return result
            action = self.store.create_action(
                interaction_id=interaction_id,
                user_id=user_id,
                action_type=action_type,
                cost=cost,
                damage=damage,
                is_critical=is_critical,
                counter_damage=counter_damage,
                heal_amount=heal_amount,
            )
            return await self._resume_action_locked(action["interaction_id"])

    async def resume_pending_actions(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action in self.store.get_pending_actions():
            try:
                async with self._action_lock:
                    results.append(await self._resume_action_locked(action["interaction_id"]))
            except NagiApiError as error:
                if error.code != "insufficient_funds":
                    logger.warning("保留中のボス通貨処理を再試行できません: %s", error.code)
            except Exception:
                logger.exception("保留中のボスアクションの再処理に失敗しました")
        return results

    async def _resume_action_locked(self, interaction_id: str) -> dict[str, Any]:
        action = self.store.get_action(interaction_id)
        if not action:
            raise RuntimeError("ボスアクションが見つかりません。")
        if action["status"] == "applied":
            return self.store.apply_action(interaction_id)
        if action["status"] == "failed":
            raise NagiApiError(409, "insufficient_funds", "所持金が不足しています。")

        if action["status"] == "pending":
            kind = "boss_attack" if action["action_type"] == "attack" else "boss_heal"
            try:
                wallet = await self.api.wallet_transaction(
                    action["user_id"],
                    kind=kind,
                    amount=action["cost"],
                    reference_id=action["interaction_id"],
                    idempotency_key=f"boss-action:{action['interaction_id']}",
                )
            except NagiApiError as error:
                if error.code == "insufficient_funds":
                    self.store.mark_action_failed(interaction_id)
                raise
            balance = wallet.get("money")
            if type(balance) is not int:
                raise NagiApiError(502, "invalid_wallet_response", "凪Botの残高応答が不正です。")
            self.store.mark_action_charged(interaction_id, balance)

        return self.store.apply_action(interaction_id)

    async def pay_pending_rewards(self) -> None:
        async with self._reward_lock:
            for reward in self.store.get_pending_rewards():
                try:
                    result = await self.api.wallet_transaction(
                        reward["user_id"],
                        kind="boss_reward",
                        amount=reward["amount"],
                        reference_id=f"boss-run:{reward['run_id']}",
                        idempotency_key=f"boss-reward:{reward['id']}",
                    )
                    balance = result.get("money")
                    if type(balance) is not int:
                        raise NagiApiError(
                            502, "invalid_wallet_response", "凪Botの残高応答が不正です。"
                        )
                    self.store.mark_reward_paid(reward["id"], balance)
                except NagiApiError as error:
                    logger.warning("ボス報酬の支給を保留します: %s", error.code)
                except Exception:
                    logger.exception("ボス報酬の支給に失敗しました")
