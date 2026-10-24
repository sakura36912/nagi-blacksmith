"""Bot起動・Cog登録・スラッシュコマンド同期。"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from weapon_bot.cogs.admin import setup as setup_admin
from weapon_bot.cogs.boss import setup as setup_boss
from weapon_bot.cogs.forge import setup as setup_forge
from weapon_bot.bosses.store import BossStore
from weapon_bot.database import WeaponMirror
from weapon_bot.localization import JapaneseTranslator
from weapon_bot.nagi_api import NagiApi


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("weapon-bot")


class WeaponBot(commands.Bot):
    def __init__(self, api: NagiApi, mirror: WeaponMirror, boss_store: BossStore) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.api = api
        self.mirror = mirror
        self.boss_store = boss_store

    async def setup_hook(self) -> None:
        await setup_forge(self, self.api, self.mirror)
        await setup_admin(self, self.api, self.mirror)
        await setup_boss(self, self.api, self.mirror, self.boss_store)
        await self.tree.set_translator(JapaneseTranslator())
        await self.tree.sync()
        logger.info("スラッシュコマンドを同期しました。")

    async def on_ready(self) -> None:
        if self.user:
            logger.info("Discord鍛冶屋Botが起動しました: %s", self.user)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "❌ このコマンドは管理者のみ実行できます。"
        else:
            logger.error(
                "スラッシュコマンドでエラーが発生しました",
                exc_info=(type(error), error, error.__traceback__),
            )
            message = "❌ 処理中にエラーが発生しました。時間をおいて再度お試しください。"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN が設定されていません。")

    mirror = WeaponMirror()
    mirror.initialize()
    boss_store = BossStore()
    boss_store.initialize()
    api = NagiApi()
    bot = WeaponBot(api, mirror, boss_store)
    try:
        bot.run(token, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Botを停止しました。")


if __name__ == "__main__":
    main()
