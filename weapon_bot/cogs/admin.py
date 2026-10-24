"""管理者専用の武器変更・リセット・内部データ編集コマンド。"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from weapon_bot.config import WEAPONS
from weapon_bot.database import WeaponMirror
from weapon_bot.localization import ja
from weapon_bot.nagi_api import NagiApi, NagiApiError
from weapon_bot.cogs.forge import _error_text

logger = logging.getLogger("weapon-bot.admin")


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: NagiApi, mirror: WeaponMirror) -> None:
        self.bot = bot
        self.api = api
        self.mirror = mirror

    async def _save_result(
        self,
        interaction: discord.Interaction,
        user_id: str,
        operation,
    ) -> dict | None:
        try:
            data = await operation
            try:
                self.mirror.save_forge(user_id, data)
            except Exception:
                logger.exception("管理者操作後のSQLiteミラー保存に失敗しました")
            return data
        except NagiApiError as error:
            await interaction.followup.send(_error_text(error), ephemeral=True)
        except Exception:
            await interaction.followup.send(
                "❌ 管理者操作に失敗しました。凪Bot APIの状態を確認してください。",
                ephemeral=True,
            )
        return None

    @app_commands.command(
        name=ja("weapon_admin_change", "武器変更"),
        description=ja("Change a member's weapon (admin only)", "メンバーの武器を変更します（管理者専用）"),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.rename(
        member=ja("member", "対象メンバー"),
        weapon=ja("weapon", "武器"),
    )
    @app_commands.choices(
        weapon=[
            app_commands.Choice(name=weapon.name, value=weapon.weapon_id)
            for weapon in WEAPONS.values()
        ]
    )
    async def change_weapon(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        weapon: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await self._save_result(
            interaction,
            str(member.id),
            self.api.admin_change_weapon(
                str(member.id), weapon.value, str(interaction.id)
            ),
        )
        if data is not None:
            await interaction.followup.send(
                f"✅ {member.mention} の武器を {data.get('weapon', weapon.name)} に変更しました。",
                ephemeral=True,
            )

    @app_commands.command(
        name=ja("weapon_admin_reset", "武器リセット"),
        description=ja("Reset a member's weapon data (admin only)", "メンバーの武器データをリセットします（管理者専用）"),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.rename(member=ja("member", "対象メンバー"))
    async def reset_weapon(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await self.api.admin_reset_weapon(str(member.id), str(interaction.id))
            try:
                self.mirror.save_forge(str(member.id), data)
            except Exception:
                logger.exception("武器リセット後のSQLiteミラー保存に失敗しました")
            await interaction.followup.send(
                f"✅ {member.mention} の武器データをリセットしました。",
                ephemeral=True,
            )
        except NagiApiError as error:
            await interaction.followup.send(_error_text(error), ephemeral=True)
        except Exception:
            await interaction.followup.send(
                "❌ リセットに失敗しました。凪Bot APIの状態を確認してください。",
                ephemeral=True,
            )

    @app_commands.command(
        name=ja("weapon_admin_edit", "武器データ編集"),
        description=ja("Edit weapon internals or currency (admin only)", "武器の内部データ・所持金を編集します（管理者専用）"),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.rename(
        member=ja("member", "対象メンバー"),
        level=ja("level", "武器レベル"),
        attack=ja("attack", "攻撃力"),
        defense=ja("defense", "防御力"),
        accuracy=ja("accuracy", "命中率"),
        money=ja("money", "所持金"),
    )
    async def edit_weapon_data(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        level: app_commands.Range[int, 1, 999] | None = None,
        attack: app_commands.Range[int, 0, 999999] | None = None,
        defense: app_commands.Range[int, 0, 999999] | None = None,
        accuracy: app_commands.Range[int, 0, 100] | None = None,
        money: app_commands.Range[int, 0, 999999999] | None = None,
    ) -> None:
        fields = {
            key: value
            for key, value in {
                "level": level,
                "attack": attack,
                "defense": defense,
                "accuracy": accuracy,
                "money": money,
            }.items()
            if value is not None
        }
        if not fields:
            await interaction.response.send_message(
                "編集する項目を1つ以上指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await self._save_result(
            interaction,
            str(member.id),
            self.api.admin_edit_user(
                str(member.id), fields, str(interaction.id)
            ),
        )
        if data is not None:
            await interaction.followup.send(
                f"✅ {member.mention} の武器データを更新しました。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot, api: NagiApi, mirror: WeaponMirror) -> None:
    await bot.add_cog(AdminCog(bot, api, mirror))
