"""BOT側SQLiteの永続ミラー。凪APIが残高と武器データの正本です。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from weapon_bot.config import database_path


class WeaponMirror:
    """凪APIの応答をローカルSQLiteに保存し、再起動後も参照できるようにする。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or database_path()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    weapon TEXT,
                    weapon_level INTEGER NOT NULL DEFAULT 0,
                    attack INTEGER NOT NULL DEFAULT 0,
                    defense INTEGER NOT NULL DEFAULT 0,
                    accuracy INTEGER NOT NULL DEFAULT 0,
                    money INTEGER NOT NULL DEFAULT 0,
                    exp INTEGER NOT NULL DEFAULT 0,
                    boss_win INTEGER NOT NULL DEFAULT 0,
                    pvp_win INTEGER NOT NULL DEFAULT 0,
                    pvp_lose INTEGER NOT NULL DEFAULT 0,
                    skill_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save_forge(self, user_id: str, data: dict[str, Any]) -> None:
        """APIの最新状態を保存。強化失敗時もAPI応答をそのまま反映する。"""
        weapon_id = data.get("weaponId")
        weapon_name = data.get("weapon") or weapon_id
        level = int(data.get("level", 0))
        attack = int(data.get("attack", 0))
        defense = int(data.get("defense", 0))
        accuracy = int(data.get("accuracy", 0))
        money = int(data.get("money", 0))
        created_at = str(data.get("createdAt") or data.get("created_at") or "")
        updated_at = str(data.get("updatedAt") or data.get("updated_at") or created_at)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, weapon, weapon_level, attack, defense, accuracy,
                    money, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    weapon=excluded.weapon,
                    weapon_level=excluded.weapon_level,
                    attack=excluded.attack,
                    defense=excluded.defense,
                    accuracy=excluded.accuracy,
                    money=excluded.money,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    weapon_name,
                    level,
                    attack,
                    defense,
                    accuracy,
                    money,
                    created_at or "unknown",
                    updated_at or "unknown",
                ),
            )

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """管理者用のローカルミラーを取得する。通常コマンドは必ずAPIを参照する。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None
