"""本船員向けの武器選択・確認・強化コマンド。"""

from __future__ import annotations

import random
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from weapon_bot.config import (
    FAILURE_MESSAGES,
    SUCCESS_MESSAGES,
    WEAPONS,
    required_role_name,
)
from weapon_bot.database import WeaponMirror
from weapon_bot.localization import ja
from weapon_bot.nagi_api import NagiApi, NagiApiError

logger = logging.getLogger("weapon-bot.forge")


def _is_crew(member: discord.Member) -> bool:
    return any(role.name == required_role_name() for role in member.roles)


def _check_crew(interaction: discord.Interaction) -> bool:
    if isinstance(interaction.user, discord.Member) and _is_crew(interaction.user):
        return True
    return False


def _error_text(error: NagiApiError) -> str:
    messages = {
        "user_not_found": "❌ 凪Botにユーザー登録がありません。先に凪Botを利用してください。",
        "weapon_not_found": "まだ武器を所持していません。先に /武器選択 を実行してください。",
        "weapon_already_owned": "❌ あなたは既に武器を所持しています。\n武器は1人につき1つまでです。",
        "insufficient_funds": "❌ 所持金が不足しています。",
        "upgrade_price_not_configured": "❌ 強化設定が未完了です。管理者にお問い合わせください。",
        "unauthorized": "❌ 凪Bot APIの認証に失敗しました。管理者にお問い合わせください。",
        "nagi_not_configured": "❌ 凪Bot APIの接続設定が未完了です。管理者にお問い合わせください。",
        "invalid_api_url": "❌ 凪Bot APIの接続設定が正しくありません。管理者にお問い合わせください。",
    }
    return messages.get(
        error.code,
        error.message if error.status < 500 else "❌ 凪Bot APIに接続できません。時間をおいて再度お試しください。",
    )


class ForgeCog(commands.Cog):
    """鍛冶屋の利用者向けコマンド群。"""

    def __init__(self, bot: commands.Bot, api: NagiApi, mirror: WeaponMirror) -> None:
        self.bot = bot
        self.api = api
        self.mirror = mirror

    async def _deny_without_crew(self, interaction: discord.Interaction) -> bool:
        if _check_crew(interaction):
            return False
        await interaction.response.send_message(
            "❌ あなたはまだ鍛冶屋を利用できません。\n"
            "「本船員」ロールを取得すると武器を選択できます。",
            ephemeral=True,
        )
        return True

    async def _api_reply(
        self,
        interaction: discord.Interaction,
        operation: Any,
    ) -> dict[str, Any] | None:
        try:
            data = await operation
            try:
                self.mirror.save_forge(str(interaction.user.id), data)
            except Exception:
                # 正本APIの処理結果は成功しているため、ミラー失敗で結果を隠さない。
                logger.exception("SQLiteミラーの保存に失敗しました")
            return data
        except NagiApiError as error:
            await interaction.followup.send(_error_text(error), ephemeral=True)
            return None
        except Exception:
            await interaction.followup.send(
                "❌ 処理中にエラーが発生しました。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return None

    @app_commands.command(
        name=ja("weapon_select", "武器選択"),
        description=ja("Select one weapon at the forge", "鍛冶屋で武器を1つ選びます"),
    )
    @app_commands.guild_only()
    @app_commands.rename(weapon=ja("weapon", "武器"))
    @app_commands.choices(
        weapon=[
            app_commands.Choice(name=weapon.name, value=weapon.weapon_id)
            for weapon in WEAPONS.values()
        ]
    )
    async def select_weapon(
        self,
        interaction: discord.Interaction,
        weapon: app_commands.Choice[str],
    ) -> None:
        if await self._deny_without_crew(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await self._api_reply(
            interaction,
            self.api.select_weapon(
                str(interaction.user.id),
                weapon.value,
                str(interaction.id),
            ),
        )
        if data is None:
            return
        await interaction.followup.send(
            "⚔️ 武器を選択しました！\n"
            f"武器：{data.get('weapon', weapon.name)}\n"
            f"Lv：{data.get('level', 1)}\n"
            "ボス戦・PvPで使用できるようになりました。",
            ephemeral=True,
        )

    @app_commands.command(
        name=ja("weapon", "武器"),
        description=ja("Check your weapon", "武器の状態を確認します"),
    )
    @app_commands.guild_only()
    async def show_weapon(self, interaction: discord.Interaction) -> None:
        if await self._deny_without_crew(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await self._api_reply(interaction, self.api.get_forge(str(interaction.user.id)))
        if data is None:
            return

        weapon = WEAPONS.get(str(data.get("weaponId", "")))
        if not weapon:
            await interaction.followup.send(
                "❌ 武器データを読み込めませんでした。管理者にお問い合わせください。",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title=f"⚔️ {weapon.name}", color=discord.Color.dark_gold())
        embed.add_field(name="武器レベル", value=f"Lv：{data.get('level', 1)}", inline=True)
        embed.add_field(
            name="固有スキル",
            value=f"{weapon.skill}\n{weapon.skill_description}",
            inline=False,
        )
        cost = data.get("nextUpgradeCost")
        rate = data.get("nextSuccessRate")
        embed.add_field(
            name="次回強化",
            value=(
                f"費用：${int(cost):,}\n成功率：{int(rate)}%"
                if cost is not None and rate is not None
                else "強化設定を確認できません。"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name=ja("upgrade", "強化"),
        description=ja("Upgrade your weapon with currency", "所持金を使って武器を強化します"),
    )
    @app_commands.guild_only()
    async def upgrade_weapon(self, interaction: discord.Interaction) -> None:
        if await self._deny_without_crew(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await self._api_reply(
            interaction,
            self.api.upgrade(str(interaction.user.id), str(interaction.id)),
        )
        if data is None:
            return
        succeeded = bool(data.get("success"))
        message = random.choice(SUCCESS_MESSAGES if succeeded else FAILURE_MESSAGES)
        level_text = f"武器Lv：{data.get('previousLevel', data.get('level', 1))} → {data.get('level', 1)}"
        await interaction.followup.send(
            f"{message}\n{level_text}\n"
            f"消費：${int(data.get('cost', 0)):,}　"
            f"残高：${int(data.get('money', 0)):,}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot, api: NagiApi, mirror: WeaponMirror) -> None:
    await bot.add_cog(ForgeCog(bot, api, mirror))
