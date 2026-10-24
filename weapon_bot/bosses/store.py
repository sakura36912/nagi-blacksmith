"""ボスイベントと戦闘状態をSQLiteに永続化する。"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weapon_bot.config import PARTY_MAX_SIZE, PARTY_MIN_SIZE, PLAYER_MAX_HP, database_path


class BossStoreError(Exception):
    """ボスデータ操作の基底例外。"""


class EventAlreadyActive(BossStoreError):
    pass


class EventUnavailable(BossStoreError):
    pass


class AlreadyHunting(BossStoreError):
    pass


class NotHunting(BossStoreError):
    pass


class PendingAction(BossStoreError):
    pass


class PlayerDown(BossStoreError):
    pass


class PartyFull(BossStoreError):
    pass


class BossStore:
    """同じpirate.db内のボスイベント・参加者・アクションを管理する。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or database_path()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS boss_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    boss_id TEXT NOT NULL,
                    boss_name TEXT NOT NULL,
                    difficulty TEXT NOT NULL,
                    boss_attack INTEGER NOT NULL,
                    boss_defense INTEGER NOT NULL,
                    reward INTEGER NOT NULL,
                    icon TEXT NOT NULL,
                    description TEXT NOT NULL,
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    phases_json TEXT NOT NULL DEFAULT '[]',
                    solo_hp INTEGER NOT NULL,
                    party_hp INTEGER NOT NULL,
                    party_window_seconds INTEGER NOT NULL,
                    special_event TEXT,
                    started_at REAL NOT NULL,
                    ends_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'ended', 'expired'))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_active_boss_event_per_guild
                ON boss_events(guild_id) WHERE status = 'active';

                CREATE TABLE IF NOT EXISTS boss_runs (
                    id TEXT PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES boss_events(id),
                    mode TEXT NOT NULL CHECK (mode IN ('solo', 'party')),
                    status TEXT NOT NULL CHECK (status IN (
                        'recruiting', 'active', 'success', 'failed', 'cancelled'
                    )),
                    max_hp INTEGER NOT NULL,
                    current_hp INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    ended_at REAL,
                    recruitment_ends_at REAL
                );
                CREATE INDEX IF NOT EXISTS boss_runs_by_event
                ON boss_runs(event_id, status, mode);

                CREATE TABLE IF NOT EXISTS boss_participants (
                    run_id TEXT NOT NULL REFERENCES boss_runs(id),
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    damage_total INTEGER NOT NULL DEFAULT 0,
                    player_hp INTEGER NOT NULL DEFAULT 100,
                    joined_at REAL NOT NULL,
                    PRIMARY KEY (run_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS boss_participants_by_user
                ON boss_participants(user_id);

                CREATE TABLE IF NOT EXISTS active_boss_memberships (
                    user_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES boss_runs(id),
                    joined_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS boss_actions (
                    interaction_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES boss_runs(id),
                    action_type TEXT NOT NULL CHECK (action_type IN ('attack', 'heal')),
                    cost INTEGER NOT NULL,
                    damage INTEGER NOT NULL DEFAULT 0,
                    is_critical INTEGER NOT NULL DEFAULT 0,
                    counter_damage INTEGER NOT NULL DEFAULT 0,
                    heal_amount INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK (status IN (
                        'pending', 'charged', 'applied', 'failed'
                    )),
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_unsettled_boss_action_per_user
                ON boss_actions(user_id) WHERE status IN ('pending', 'charged');
                CREATE INDEX IF NOT EXISTS boss_actions_pending
                ON boss_actions(status, created_at);

                CREATE TABLE IF NOT EXISTS boss_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL REFERENCES boss_events(id),
                    run_id TEXT NOT NULL REFERENCES boss_runs(id),
                    user_id TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount >= 0),
                    status TEXT NOT NULL CHECK (status IN ('pending', 'paid')),
                    created_at REAL NOT NULL,
                    paid_at REAL,
                    UNIQUE (run_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS boss_rewards_pending
                ON boss_rewards(status, created_at);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def create_event(
        self,
        *,
        guild_id: str,
        channel_id: str,
        boss: dict[str, Any],
        solo_hp: int,
        party_hp: int,
        duration_seconds: int,
        party_window_seconds: int,
        special_event: str | None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now or time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id FROM boss_events WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            ).fetchone()
            if active:
                raise EventAlreadyActive("このサーバーでは既にボスイベントが開催中です。")
            cursor = connection.execute(
                """
                INSERT INTO boss_events (
                    guild_id, channel_id, boss_id, boss_name, difficulty,
                    boss_attack, boss_defense, reward, icon, description,
                    skills_json, phases_json, solo_hp, party_hp,
                    party_window_seconds, special_event, started_at, ends_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    guild_id,
                    channel_id,
                    boss["boss_id"],
                    boss["name"],
                    boss["difficulty"],
                    boss["attack"],
                    boss["defense"],
                    boss["reward"],
                    boss["icon"],
                    boss["description"],
                    json.dumps(boss.get("skills", []), ensure_ascii=False),
                    json.dumps(boss.get("phases", []), ensure_ascii=False),
                    solo_hp,
                    party_hp,
                    party_window_seconds,
                    special_event,
                    now,
                    now + duration_seconds,
                ),
            )
            event_id = int(cursor.lastrowid)
        return self.get_event(event_id) or {}

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute("SELECT * FROM boss_events WHERE id = ?", (event_id,)).fetchone()
            )

    def get_active_event(self, guild_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM boss_events WHERE guild_id = ? AND status = 'active'",
                    (guild_id,),
                ).fetchone()
            )

    def create_solo_run(
        self, guild_id: str, user_id: str, display_name: str, now: float | None = None
    ) -> dict[str, Any]:
        now = now or time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = connection.execute(
                "SELECT * FROM boss_events WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            ).fetchone()
            if not event or event["ends_at"] <= now:
                raise EventUnavailable("現在参加できるボスイベントはありません。")
            if connection.execute(
                "SELECT 1 FROM active_boss_memberships WHERE user_id = ?", (user_id,)
            ).fetchone():
                raise AlreadyHunting("あなたは既に別の討伐に参加しています。")
            run_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO boss_runs (
                    id, event_id, mode, status, max_hp, current_hp,
                    created_at, started_at, recruitment_ends_at
                ) VALUES (?, ?, 'solo', 'active', ?, ?, ?, ?, NULL)
                """,
                (run_id, event["id"], event["solo_hp"], event["solo_hp"], now, now),
            )
            connection.execute(
                """
                INSERT INTO boss_participants
                (run_id, user_id, display_name, player_hp, joined_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, user_id, display_name, PLAYER_MAX_HP, now),
            )
            connection.execute(
                "INSERT INTO active_boss_memberships (user_id, guild_id, run_id, joined_at) VALUES (?, ?, ?, ?)",
                (user_id, guild_id, run_id, now),
            )
        return self.get_run(run_id) or {}

    def join_party(
        self,
        guild_id: str,
        user_id: str,
        display_name: str,
        now: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        now = now or time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = connection.execute(
                "SELECT * FROM boss_events WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            ).fetchone()
            if not event or event["ends_at"] <= now:
                raise EventUnavailable("現在参加できるボスイベントはありません。")
            if connection.execute(
                "SELECT 1 FROM active_boss_memberships WHERE user_id = ?", (user_id,)
            ).fetchone():
                raise AlreadyHunting("あなたは既に別の討伐に参加しています。")

            run = connection.execute(
                """
                SELECT * FROM boss_runs
                WHERE event_id = ? AND mode = 'party' AND status = 'recruiting'
                  AND recruitment_ends_at > ?
                ORDER BY created_at LIMIT 1
                """,
                (event["id"], now),
            ).fetchone()
            if not run:
                run_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO boss_runs (
                        id, event_id, mode, status, max_hp, current_hp,
                        created_at, recruitment_ends_at
                    ) VALUES (?, ?, 'party', 'recruiting', ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        event["id"],
                        event["party_hp"],
                        event["party_hp"],
                        now,
                        min(now + event["party_window_seconds"], event["ends_at"]),
                    ),
                )
                run = connection.execute(
                    "SELECT * FROM boss_runs WHERE id = ?", (run_id,)
                ).fetchone()

            count = connection.execute(
                "SELECT COUNT(*) FROM boss_participants WHERE run_id = ?", (run["id"],)
            ).fetchone()[0]
            if count >= PARTY_MAX_SIZE:
                raise PartyFull("このパーティは満員です。")
            connection.execute(
                """
                INSERT INTO boss_participants
                (run_id, user_id, display_name, player_hp, joined_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run["id"], user_id, display_name, PLAYER_MAX_HP, now),
            )
            connection.execute(
                "INSERT INTO active_boss_memberships (user_id, guild_id, run_id, joined_at) VALUES (?, ?, ?, ?)",
                (user_id, guild_id, run["id"], now),
            )
            count += 1
            started = count >= PARTY_MAX_SIZE
            if started:
                connection.execute(
                    """
                    UPDATE boss_runs SET status = 'active', started_at = ?,
                        recruitment_ends_at = NULL WHERE id = ?
                    """,
                    (now, run["id"]),
                )
            run_id = str(run["id"])
        return self.get_run(run_id) or {}, started

    def resolve_party_recruitment(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        results: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recruiting = connection.execute(
                """
                SELECT r.*, e.ends_at AS event_ends_at
                FROM boss_runs r JOIN boss_events e ON e.id = r.event_id
                WHERE r.mode = 'party' AND r.status = 'recruiting'
                  AND recruitment_ends_at <= ?
                """,
                (now,),
            ).fetchall()
            for run in recruiting:
                participants = connection.execute(
                    "SELECT user_id, display_name FROM boss_participants WHERE run_id = ?",
                    (run["id"],),
                ).fetchall()
                members = [dict(row) for row in participants]
                if len(members) >= PARTY_MIN_SIZE and run["event_ends_at"] > now:
                    connection.execute(
                        """
                        UPDATE boss_runs SET status = 'active', started_at = ?,
                            recruitment_ends_at = NULL WHERE id = ?
                        """,
                        (now, run["id"]),
                    )
                    status = "started"
                else:
                    connection.execute(
                        "UPDATE boss_runs SET status = 'cancelled', ended_at = ? WHERE id = ?",
                        (now, run["id"]),
                    )
                    connection.execute(
                        "DELETE FROM active_boss_memberships WHERE run_id = ?", (run["id"],)
                    )
                    status = "cancelled"
                results.append({"run_id": run["id"], "event_id": run["event_id"], "status": status, "members": members})
        return results

    def get_user_run(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, e.boss_name, e.icon, e.boss_attack, e.boss_defense,
                       e.reward, e.ends_at AS event_ends_at, e.status AS event_status,
                       p.user_id, p.display_name, p.player_hp, p.damage_total
                FROM active_boss_memberships m
                JOIN boss_runs r ON r.id = m.run_id
                JOIN boss_events e ON e.id = r.event_id
                JOIN boss_participants p ON p.run_id = r.id AND p.user_id = m.user_id
                WHERE m.user_id = ?
                """,
                (user_id,),
            ).fetchone()
            return self._row(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                """
                SELECT r.*, e.guild_id, e.boss_name, e.icon, e.boss_attack,
                       e.boss_defense, e.reward, e.ends_at AS event_ends_at,
                       e.status AS event_status
                FROM boss_runs r JOIN boss_events e ON e.id = r.event_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
            result = self._row(run)
            if result:
                result["members"] = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT user_id, display_name, damage_total, player_hp
                        FROM boss_participants WHERE run_id = ? ORDER BY joined_at
                        """,
                        (run_id,),
                    ).fetchall()
                ]
            return result

    def get_event_summary(self, guild_id: str) -> dict[str, Any] | None:
        event = self.get_active_event(guild_id)
        if not event:
            return None
        with self._connect() as connection:
            runs = connection.execute(
                """
                SELECT r.id, r.mode, r.status, r.current_hp, r.max_hp,
                       r.recruitment_ends_at, COUNT(p.user_id) AS participant_count
                FROM boss_runs r
                LEFT JOIN boss_participants p ON p.run_id = r.id
                WHERE r.event_id = ? AND r.status IN ('recruiting', 'active')
                GROUP BY r.id ORDER BY r.created_at
                """,
                (event["id"],),
            ).fetchall()
        event["runs"] = [dict(row) for row in runs]
        event["solo_participant_count"] = sum(
            run["participant_count"] for run in event["runs"] if run["mode"] == "solo"
        )
        event["party_participant_count"] = sum(
            run["participant_count"] for run in event["runs"] if run["mode"] == "party"
        )
        event["solo_hp_remaining"] = sum(
            run["current_hp"] for run in event["runs"] if run["mode"] == "solo"
        )
        event["solo_hp_total"] = sum(
            run["max_hp"] for run in event["runs"] if run["mode"] == "solo"
        )
        event["party_hp_remaining"] = sum(
            run["current_hp"] for run in event["runs"] if run["mode"] == "party"
        )
        event["party_hp_total"] = sum(
            run["max_hp"] for run in event["runs"] if run["mode"] == "party"
        )
        return event

    def create_action(
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
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now or time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT r.id, r.status, r.event_id, e.status AS event_status,
                       e.ends_at, m.guild_id, p.player_hp
                FROM active_boss_memberships m
                JOIN boss_runs r ON r.id = m.run_id
                JOIN boss_events e ON e.id = r.event_id
                JOIN boss_participants p ON p.run_id = r.id AND p.user_id = m.user_id
                WHERE m.user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row or row["status"] != "active" or row["event_status"] != "active" or row["ends_at"] <= now:
                raise NotHunting("攻撃・回復できる討伐に参加していません。")
            pending = connection.execute(
                "SELECT interaction_id FROM boss_actions WHERE user_id = ? AND status IN ('pending', 'charged')",
                (user_id,),
            ).fetchone()
            if pending:
                raise PendingAction("前回の通貨処理を確認中です。再試行してください。")
            if action_type == "attack" and row["player_hp"] <= 0:
                raise PlayerDown("HPが0です。/回復 でHPを回復してください。")
            connection.execute(
                """
                INSERT INTO boss_actions (
                    interaction_id, user_id, run_id, action_type, cost,
                    damage, is_critical, counter_damage, heal_amount,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    interaction_id,
                    user_id,
                    row["id"],
                    action_type,
                    cost,
                    damage,
                    int(is_critical),
                    counter_damage,
                    heal_amount,
                    now,
                    now,
                ),
            )
        return self.get_action(interaction_id) or {}

    def get_action(self, interaction_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM boss_actions WHERE interaction_id = ?", (interaction_id,)
                ).fetchone()
            )

    def get_pending_action_for_user(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    """
                    SELECT * FROM boss_actions WHERE user_id = ?
                      AND status IN ('pending', 'charged') ORDER BY created_at LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
            )

    def get_pending_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM boss_actions WHERE status IN ('pending', 'charged')
                    ORDER BY created_at LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            ]

    def mark_action_charged(self, interaction_id: str, balance: int) -> None:
        now = time.time()
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE boss_actions SET status = 'charged', updated_at = ? WHERE interaction_id = ? AND status = 'pending'",
                (now, interaction_id),
            )
            action = connection.execute(
                "SELECT user_id FROM boss_actions WHERE interaction_id = ?", (interaction_id,)
            ).fetchone()
            if action:
                connection.execute(
                    "UPDATE users SET money = ?, updated_at = ? WHERE user_id = ?",
                    (balance, updated_at, action["user_id"]),
                )

    def mark_action_failed(self, interaction_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE boss_actions SET status = 'failed', updated_at = ? WHERE interaction_id = ? AND status = 'pending'",
                (time.time(), interaction_id),
            )

    def apply_action(self, interaction_id: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            action = connection.execute(
                "SELECT * FROM boss_actions WHERE interaction_id = ?", (interaction_id,)
            ).fetchone()
            if not action:
                raise BossStoreError("アクション記録が見つかりません。")
            if action["status"] == "applied":
                result = json.loads(action["result_json"] or "{}")
                result["newly_applied"] = False
                return result
            if action["status"] != "charged":
                raise PendingAction("通貨処理の完了を確認できません。")
            run = connection.execute(
                """
                SELECT r.*, e.reward, e.boss_name
                FROM boss_runs r JOIN boss_events e ON e.id = r.event_id
                WHERE r.id = ?
                """,
                (action["run_id"],),
            ).fetchone()
            participant = connection.execute(
                "SELECT * FROM boss_participants WHERE run_id = ? AND user_id = ?",
                (action["run_id"], action["user_id"]),
            ).fetchone()
            if not run or not participant:
                raise BossStoreError("討伐参加データを確認できません。")

            result: dict[str, Any] = {
                "action_type": action["action_type"],
                "cost": action["cost"],
                "run_id": action["run_id"],
                "event_id": run["event_id"],
                "mode": run["mode"],
                "newly_applied": True,
                "killed": False,
            }
            if action["action_type"] == "attack":
                if run["status"] != "active":
                    raise EventUnavailable("この討伐はすでに終了しています。")
                applied_damage = min(action["damage"], run["current_hp"])
                current_hp = max(0, run["current_hp"] - applied_damage)
                connection.execute(
                    "UPDATE boss_runs SET current_hp = ? WHERE id = ?",
                    (current_hp, run["id"]),
                )
                connection.execute(
                    """
                    UPDATE boss_participants SET damage_total = damage_total + ?
                    WHERE run_id = ? AND user_id = ?
                    """,
                    (applied_damage, run["id"], action["user_id"]),
                )
                killed = current_hp <= 0
                counter_damage = 0
                if killed:
                    connection.execute(
                        "UPDATE boss_runs SET status = 'success', ended_at = ? WHERE id = ?",
                        (now, run["id"]),
                    )
                    members = connection.execute(
                        """
                        SELECT user_id FROM boss_participants
                        WHERE run_id = ? ORDER BY user_id
                        """,
                        (run["id"],),
                    ).fetchall()
                    reward_each = run["reward"] // max(1, len(members))
                    remainder = run["reward"] % max(1, len(members))
                    for index, member in enumerate(members):
                        amount = reward_each + (1 if index < remainder else 0)
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO boss_rewards
                            (event_id, run_id, user_id, amount, status, created_at)
                            VALUES (?, ?, ?, ?, 'pending', ?)
                            """,
                            (run["event_id"], run["id"], member["user_id"], amount, now),
                        )
                    connection.execute(
                        "DELETE FROM active_boss_memberships WHERE run_id = ?", (run["id"],)
                    )
                else:
                    counter_damage = min(action["counter_damage"], participant["player_hp"])
                    player_hp = max(0, participant["player_hp"] - counter_damage)
                    connection.execute(
                        """
                        UPDATE boss_participants SET player_hp = ?
                        WHERE run_id = ? AND user_id = ?
                        """,
                        (player_hp, run["id"], action["user_id"]),
                    )
                result.update(
                    {
                        "damage": applied_damage,
                        "hp_remaining": current_hp,
                        "max_hp": run["max_hp"],
                        "critical": bool(action["is_critical"]),
                        "counter_damage": counter_damage,
                        "player_hp": max(0, participant["player_hp"] - counter_damage),
                        "killed": killed,
                    }
                )
            else:
                healed = min(action["heal_amount"], PLAYER_MAX_HP - participant["player_hp"])
                player_hp = participant["player_hp"] + max(0, healed)
                connection.execute(
                    "UPDATE boss_participants SET player_hp = ? WHERE run_id = ? AND user_id = ?",
                    (player_hp, run["id"], action["user_id"]),
                )
                result.update({"healed": max(0, healed), "player_hp": player_hp})

            connection.execute(
                """
                UPDATE boss_actions SET status = 'applied', result_json = ?, updated_at = ?
                WHERE interaction_id = ?
                """,
                (json.dumps(result, ensure_ascii=False), now, interaction_id),
            )
            return result

    def get_pending_rewards(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM boss_rewards WHERE status = 'pending' ORDER BY id LIMIT ?",
                    (limit,),
                ).fetchall()
            ]

    def mark_reward_paid(self, reward_id: int, balance: int) -> None:
        now = time.time()
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reward = connection.execute(
                "SELECT user_id FROM boss_rewards WHERE id = ?", (reward_id,)
            ).fetchone()
            if not reward:
                return
            connection.execute(
                "UPDATE boss_rewards SET status = 'paid', paid_at = ? WHERE id = ?",
                (now, reward_id),
            )
            connection.execute(
                "UPDATE users SET money = ?, updated_at = ? WHERE user_id = ?",
                (balance, updated_at, reward["user_id"]),
            )

    def get_run_result(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                """
                SELECT r.*, e.boss_name, e.icon, e.reward, e.started_at AS event_started_at
                FROM boss_runs r JOIN boss_events e ON e.id = r.event_id WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
            if not run:
                return None
            result = dict(run)
            members = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT user_id, display_name, damage_total
                    FROM boss_participants WHERE run_id = ?
                    ORDER BY damage_total DESC, joined_at
                    """,
                    (run_id,),
                ).fetchall()
            ]
            result["members"] = members
            result["total_damage"] = sum(member["damage_total"] for member in members)
            result["mvp"] = members[0] if members else None
            result["payouts"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT user_id, amount, status FROM boss_rewards WHERE run_id = ? ORDER BY user_id",
                    (run_id,),
                ).fetchall()
            ]
            result["duration_seconds"] = max(
                0, int((result.get("ended_at") or time.time()) - (result.get("started_at") or time.time()))
            )
            return result

    def resolve_expired_events(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        results: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            events = connection.execute(
                "SELECT * FROM boss_events WHERE status = 'active' AND ends_at <= ?", (now,)
            ).fetchall()
            for event in events:
                pending = connection.execute(
                    """
                    SELECT COUNT(*) FROM boss_actions a
                    JOIN boss_runs r ON r.id = a.run_id
                    WHERE r.event_id = ? AND a.status IN ('pending', 'charged')
                    """,
                    (event["id"],),
                ).fetchone()[0]
                if pending:
                    continue
                connection.execute(
                    "UPDATE boss_events SET status = 'expired' WHERE id = ?", (event["id"],)
                )
                connection.execute(
                    """
                    UPDATE boss_runs SET status = CASE
                        WHEN status = 'recruiting' THEN 'cancelled'
                        WHEN status = 'active' THEN 'failed'
                        ELSE status END,
                        ended_at = ?
                    WHERE event_id = ? AND status IN ('recruiting', 'active')
                    """,
                    (now, event["id"]),
                )
                connection.execute(
                    "DELETE FROM active_boss_memberships WHERE guild_id = ?",
                    (event["guild_id"],),
                )
                results.append(dict(event))
        return results

    def force_end_event(self, guild_id: str, now: float | None = None) -> dict[str, Any] | None:
        now = now or time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = connection.execute(
                "SELECT * FROM boss_events WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            ).fetchone()
            if not event:
                return None
            pending = connection.execute(
                """
                SELECT COUNT(*) FROM boss_actions a
                JOIN boss_runs r ON r.id = a.run_id
                WHERE r.event_id = ? AND a.status IN ('pending', 'charged')
                """,
                (event["id"],),
            ).fetchone()[0]
            if pending:
                raise PendingAction("通貨処理を確認中です。数秒後にもう一度お試しください。")
            connection.execute(
                "UPDATE boss_events SET status = 'ended' WHERE id = ?", (event["id"],)
            )
            connection.execute(
                """
                UPDATE boss_runs SET status = CASE
                    WHEN status = 'recruiting' THEN 'cancelled'
                    WHEN status = 'active' THEN 'failed'
                    ELSE status END,
                    ended_at = ?
                WHERE event_id = ? AND status IN ('recruiting', 'active')
                """,
                (now, event["id"]),
            )
            connection.execute(
                "DELETE FROM active_boss_memberships WHERE guild_id = ?",
                (guild_id,),
            )
        return dict(event)

    def resolve_party_timeouts(self, now: float | None = None) -> list[dict[str, Any]]:
        return self.resolve_party_recruitment(now)
