"""内部ダメージ計算。攻撃力などの詳細な内訳は公開しない。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

from weapon_bot.config import (
    CRITICAL_CHANCE,
    CRITICAL_MULTIPLIER,
    BOSS_DAMAGE_RANDOM_MAX,
    BOSS_DAMAGE_RANDOM_MIN,
    DAMAGE_RANDOM_MAX,
    DAMAGE_RANDOM_MIN,
    ROLE_DAMAGE_BONUSES,
    WEAPONS,
    WEAPON_LEVEL_DAMAGE_BONUS,
    WEAPON_LEVEL_DEFENSE_BONUS,
)


@dataclass(frozen=True)
class DamageResult:
    damage: int
    critical: bool


def calculate_damage(
    weapon_id: str,
    weapon_level: int,
    role_names: Iterable[str],
    boss_defense: int,
    rng: random.Random | random.SystemRandom | None = None,
) -> DamageResult:
    """武器・Lv・ロール・乱数・会心・ボス防御から実ダメージを算出する。"""
    weapon = WEAPONS.get(weapon_id)
    if not weapon:
        raise ValueError("未登録の武器IDです。")
    if weapon_level < 1:
        raise ValueError("武器レベルは1以上である必要があります。")

    rng = rng or random.SystemRandom()
    role_bonus = max(
        (ROLE_DAMAGE_BONUSES.get(role_name, 1.0) for role_name in role_names),
        default=1.0,
    )
    level_multiplier = 1 + max(0, weapon_level - 1) * WEAPON_LEVEL_DAMAGE_BONUS
    random_multiplier = rng.uniform(DAMAGE_RANDOM_MIN, DAMAGE_RANDOM_MAX)
    critical = rng.random() < CRITICAL_CHANCE
    critical_multiplier = CRITICAL_MULTIPLIER if critical else 1.0
    rolled_attack = (
        weapon.attack * level_multiplier * role_bonus * random_multiplier * critical_multiplier
    )
    damage = max(1, round(rolled_attack - max(0, boss_defense)))
    return DamageResult(damage=damage, critical=critical)


def calculate_boss_counter_damage(
    boss_attack: int,
    weapon_defense: int,
    weapon_level: int,
    rng: random.Random | random.SystemRandom | None = None,
) -> int:
    """ボスの反撃を武器の内部防御値で軽減して算出する。"""
    rng = rng or random.SystemRandom()
    level_bonus = max(0, weapon_level - 1) * WEAPON_LEVEL_DEFENSE_BONUS
    damage = boss_attack * rng.uniform(BOSS_DAMAGE_RANDOM_MIN, BOSS_DAMAGE_RANDOM_MAX)
    return max(1, round(damage - max(0, weapon_defense + level_bonus)))
