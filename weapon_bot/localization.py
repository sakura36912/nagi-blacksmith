"""Discordスラッシュコマンドの日本語ローカライズ。"""

from discord import Locale, app_commands


def ja(english: str, japanese: str) -> app_commands.locale_str:
    """英語の標準名と日本語表示名を持つDiscord翻訳文字列を作る。"""
    return app_commands.locale_str(english, ja=japanese)


class JapaneseTranslator(app_commands.Translator):
    """locale_str に含めた日本語訳をDiscordへ登録する。"""

    async def translate(
        self,
        string: app_commands.locale_str,
        locale: Locale,
        context: app_commands.TranslationContextTypes,
    ) -> str | None:
        return string.extras.get(locale.value)
