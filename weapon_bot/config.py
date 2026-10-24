"""BOT設定。凪API側にも同じ強化価格・成功率・成長値を設定してください。"""

from dataclasses import dataclass
from typing import Any
import os


@dataclass(frozen=True)
class Weapon:
    """武器の公開情報と、API側で使用する内部初期ステータス。"""

    weapon_id: str
    name: str
    skill: str
    skill_description: str
    attack: int
    defense: int
    accuracy: int
    attack_per_level: int
    defense_per_level: int
    accuracy_per_level: int


WEAPONS: dict[str, Weapon] = {
    "greatsword": Weapon("greatsword", "大剣", "斬鉄撃", "重い一撃で敵を打ち砕く。", 10, 3, 75, 3, 1, 1),
    "sword": Weapon("sword", "片手剣", "連閃", "隙を逃さず素早く斬り返す。", 8, 5, 88, 2, 2, 1),
    "spear": Weapon("spear", "槍", "貫通突き", "間合いの外から一直線に突く。", 9, 3, 84, 2, 1, 2),
    "bow": Weapon("bow", "弓", "弱点狙撃", "敵の急所を正確に射抜く。", 8, 2, 92, 2, 1, 1),
    "dual-blades": Weapon("dual-blades", "双剣", "疾風連斬", "身軽な連続攻撃で畳みかける。", 7, 3, 96, 2, 1, 1),
    "staff": Weapon("staff", "杖", "魔力解放", "内なる魔力を解き放つ。", 7, 6, 82, 2, 2, 2),
}


@dataclass(frozen=True)
class BossDefinition:
    """設定ファイルで編集・追加できるボスの基本情報。"""

    boss_id: str
    name: str
    solo_hp: int
    party_hp: int
    difficulty: str
    attack: int
    defense: int
    reward: int
    icon: str
    description: str
    skills: tuple[str, ...] = ()
    phases: tuple[dict[str, Any], ...] = ()


BOSSES: dict[str, BossDefinition] = {
    "kraken": BossDefinition(
        "kraken", "クラーケン", 300, 900, "通常", 30, 2, 600, "🐙",
        "深海から現れた巨大な海魔。", ("触手攻撃",),
    ),
    "megalodon": BossDefinition(
        "megalodon", "メガロドン", 500, 1_500, "強敵", 45, 4, 1_500, "🦈",
        "獲物を逃さない古代の巨大鮫。", ("急襲",),
    ),
    "leviathan": BossDefinition(
        "leviathan", "リヴァイアサン", 900, 2_800, "伝説", 60, 7, 3_500, "🐋",
        "海を支配する伝説の巨獣。", ("大津波",),
        ({"hp_below_percent": 50, "behavior": "enraged"},),
    ),
}

# 値は初期例です。実際のサーバーロール名に合わせて設定ファイルを編集できます。
ROLE_DAMAGE_BONUSES: dict[str, float] = {
    "船長": 1.20,
    "副船長": 1.10,
    "戦闘員": 1.10,
    "本船員": 1.00,
}
WEAPON_LEVEL_DAMAGE_BONUS = 0.08
WEAPON_LEVEL_DEFENSE_BONUS = 0.05
DAMAGE_RANDOM_MIN = 0.80
DAMAGE_RANDOM_MAX = 1.20
BOSS_DAMAGE_RANDOM_MIN = 0.90
BOSS_DAMAGE_RANDOM_MAX = 1.10
CRITICAL_CHANCE = 0.10
CRITICAL_MULTIPLIER = 1.50

# 戦闘通貨とプレイヤーHP。凪APIのwallet transactionにも同じ料金を設定します。
BOSS_ATTACK_COST = 100
BOSS_HEAL_COST = 250
BOSS_HEAL_AMOUNT = 30
PLAYER_MAX_HP = 100
DEFAULT_BOSS_DURATION_MINUTES = 60
DEFAULT_PARTY_WINDOW_MINUTES = 5
PARTY_MAX_SIZE = 4
PARTY_MIN_SIZE = 2

# キーは強化前のレベル。Lv5以降は4,000ずつ増額します。
UPGRADE_COSTS: dict[int, int] = {1: 1_000, 2: 2_500, 3: 5_000, 4: 8_000}
UPGRADE_COST_AFTER_LEVEL_5 = 12_000
UPGRADE_COST_INCREMENT = 4_000

# 強化前Lv1は100%。以降5ポイントずつ低下し、最低30%。
SUCCESS_START_RATE = 100
SUCCESS_RATE_DROP_PER_LEVEL = 5
SUCCESS_RATE_FLOOR = 30

SUCCESS_MESSAGES = (
    "強化成功！",
    "武器がさらに輝きを増した！",
    "鍛冶屋「いい出来だ！」",
)
FAILURE_MESSAGES = (
    "強化に失敗した…",
    "あと少しで成功だった…",
    "鍛冶屋「今日は調子が悪いな」",
)


def upgrade_cost(level: int) -> int:
    """現在レベルから次のレベルへ上げる費用を返す。"""
    if level in UPGRADE_COSTS:
        return UPGRADE_COSTS[level]
    return UPGRADE_COST_AFTER_LEVEL_5 + UPGRADE_COST_INCREMENT * max(0, level - 5)


def success_rate(level: int) -> int:
    """現在レベルからの強化成功率を返す。"""
    return max(SUCCESS_RATE_FLOOR, SUCCESS_START_RATE - (level - 1) * SUCCESS_RATE_DROP_PER_LEVEL)


def required_role_name() -> str:
    return os.getenv("CREW_ROLE_NAME", "本船員").strip() or "本船員"


def database_path() -> str:
    return os.getenv("WEAPON_DB_PATH", "data/pirate.db")
