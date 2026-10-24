"""ボスイベント管理・参加・戦闘のスラッシュコマンド。"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from weapon_bot.bosses.combat import calculate_boss_counter_damage, calculate_damage
from weapon_bot.bosses.service import BossService
from weapon_bot.bosses.store import (
    AlreadyHunting,
    BossStore,
    EventAlreadyActive,
    EventUnavailable,
    NotHunting,
    PartyFull,
    PendingAction,
    PlayerDown,
)
from weapon_bot.config import (
    BOSSES,
    BOSS_ATTACK_COST,
    BOSS_HEAL_AMOUNT,
    BOSS_HEAL_COST,
    DEFAULT_BOSS_DURATION_MINUTES,
    DEFAULT_PARTY_WINDOW_MINUTES,
    PARTY_MAX_SIZE,
    PARTY_MIN_SIZE,
    PLAYER_MAX_HP,
)
from weapon_bot.localization import ja
from weapon_bot.nagi_api import NagiApiError
from weapon_bot.cogs.forge import _error_text


logger = logging.getLogger("weapon-bot.boss-cog")


class BossCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        api,
        mirror,
        store: BossStore,
    ) -> None:
        self.bot = bot
        self.api = api
        self.mirror = mirror
        self.store = store
        self.service = BossService(api, mirror, store)

    async def cog_load(self) -> None:
        self.maintenance.start()

    async def cog_unload(self) -> None:
        self.maintenance.cancel()

    @tasks.loop(seconds=15)
    async def maintenance(self) -> None:
        """Bot再起動後の保留課金・報酬、受付時間、イベント制限時間を処理する。"""
        try:
            outcomes = await self.service.resume_pending_actions()
            for outcome in outcomes:
                if outcome.get("killed") and outcome.get("newly_applied"):
                    await self._announce_victory(str(outcome["run_id"]))

            for party in self.store.resolve_party_recruitment():
                run = self.store.get_run(party["run_id"])
                if not run:
                    continue
                if party["status"] == "started":
                    members = " ".join(f"<@{member['user_id']}>" for member in party["members"])
                    await self._send_to_event(
                        party["event_id"],
                        f"⚔️ パーティ討伐開始！ {members}\n"
                        f"{run['icon']} {run['boss_name']}　HP：{run['current_hp']:,}",
                    )
                else:
                    member = party["members"][0] if party["members"] else None
                    mention = f"<@{member['user_id']}>" if member else "参加者"
                    await self._send_to_event(
                        party["event_id"],
                        f"パーティは{PARTY_MIN_SIZE}人に届かなかったため募集を中止しました。{mention} は再参加できます。",
                    )

            for event in self.store.resolve_expired_events():
                await self._send_to_event(
                    event["id"],
                    f"⏰ {event['icon']} {event['boss_name']} のイベント時間が終了しました。"
                    "残りHPがある討伐は失敗です。",
                )
            await self.service.pay_pending_rewards()
        except Exception:
            logger.exception("ボスイベントの定期処理に失敗しました")

    @maintenance.before_loop
    async def before_maintenance(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_to_event(self, event_id: int, content: str) -> None:
        event = self.store.get_event(event_id)
        if not event:
            return
        channel = self.bot.get_channel(int(event["channel_id"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(event["channel_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("ボスイベントの通知先チャンネルを取得できません")
                return
        if hasattr(channel, "send"):
            try:
                await channel.send(content)
            except discord.HTTPException:
                logger.exception("ボスイベント通知を送信できません")

    @staticmethod
    def _duration_text(seconds: int) -> str:
        minutes, remainder = divmod(max(0, seconds), 60)
        if minutes:
            return f"{minutes}分{remainder}秒"
        return f"{remainder}秒"

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, NagiApiError):
            return _error_text(error)
        if isinstance(error, (AlreadyHunting, NotHunting, EventUnavailable, EventAlreadyActive, PartyFull, PendingAction, PlayerDown)):
            return f"❌ {error}"
        return "❌ 処理中にエラーが発生しました。時間をおいて再度お試しください。"

    async def _respond_error(self, interaction: discord.Interaction, error: Exception) -> None:
        content = self._error_message(error)
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    @app_commands.command(
        name=ja("boss_start", "ボス開催"),
        description=ja("Start a boss event", "ボス討伐イベントを開催します"),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(
        boss=[
            app_commands.Choice(name=f"{boss.icon} {boss.name}", value=boss_id)
            for boss_id, boss in BOSSES.items()
        ]
    )
    @app_commands.rename(
        boss=ja("boss", "ボス"),
        solo_hp=ja("solo_hp", "ソロ体力"),
        party_hp=ja("party_hp", "パーティ体力"),
        duration_minutes=ja("duration_minutes", "制限時間分"),
        party_window_minutes=ja("party_window_minutes", "受付時間分"),
        special_event=ja("special_event", "特殊イベント"),
    )
    async def start_event(
        self,
        interaction: discord.Interaction,
        boss: app_commands.Choice[str],
        solo_hp: app_commands.Range[int, 1, 500_000] | None = None,
        party_hp: app_commands.Range[int, 1, 2_000_000] | None = None,
        duration_minutes: app_commands.Range[int, 1, 240] = DEFAULT_BOSS_DURATION_MINUTES,
        party_window_minutes: app_commands.Range[int, 1, 60] = DEFAULT_PARTY_WINDOW_MINUTES,
        special_event: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if special_event and len(special_event) > 100:
            await interaction.followup.send("特殊イベントは100文字以内で入力してください。", ephemeral=True)
            return
        definition = BOSSES[boss.value]
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.followup.send("❌ このチャンネルには募集メッセージを送信できません。", ephemeral=True)
            return
        try:
            event = self.store.create_event(
                guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id),
                boss=asdict(definition),
                solo_hp=solo_hp or definition.solo_hp,
                party_hp=party_hp or definition.party_hp,
                duration_seconds=duration_minutes * 60,
                party_window_seconds=party_window_minutes * 60,
                special_event=special_event.strip() if special_event else None,
            )
            embed = discord.Embed(
                title=f"{definition.icon} ボス討伐イベント開催！",
                description=(
                    f"**{definition.name}** — {definition.description}\n"
                    f"難易度：{definition.difficulty}\n"
                    f"ソロ用HP：{event['solo_hp']:,}　/　パーティ用HP：{event['party_hp']:,}\n"
                    f"制限時間：{duration_minutes}分　/　パーティ受付：最大{party_window_minutes}分\n"
                    f"討伐報酬：${definition.reward:,}\n\n"
                    "武器所持者は `/参加` からソロまたはパーティを選んでください。"
                ),
                color=discord.Color.dark_red(),
            )
            if event["special_event"]:
                embed.add_field(name="特殊イベント", value=event["special_event"], inline=False)
            await channel.send(embed=embed)
            await interaction.followup.send("✅ ボスイベントを開始しました。", ephemeral=True)
        except EventAlreadyActive as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except discord.HTTPException:
            try:
                self.store.force_end_event(str(interaction.guild_id))
            except Exception:
                logger.exception("募集メッセージ失敗後のイベント終了に失敗しました")
            await interaction.followup.send("❌ 募集メッセージを送信できなかったため、イベントを中止しました。", ephemeral=True)
        except Exception:
            logger.exception("ボスイベントを開始できません")
            await interaction.followup.send("❌ イベントを開始できませんでした。設定を確認してください。", ephemeral=True)

    @app_commands.command(
        name=ja("boss_end", "ボス終了"),
        description=ja("Force end the current boss event", "開催中のボスイベントを強制終了します"),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def end_event(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            event = self.store.force_end_event(str(interaction.guild_id))
            if not event:
                await interaction.followup.send("開催中のボスイベントはありません。", ephemeral=True)
                return
            await self._send_to_event(
                event["id"],
                f"🛑 管理者により {event['icon']} {event['boss_name']} のイベントが終了しました。",
            )
            await interaction.followup.send("✅ ボスイベントを終了しました。", ephemeral=True)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(
        name=ja("boss_info", "ボス情報"),
        description=ja("Show the current boss event status", "開催中のボスイベント情報を表示します"),
    )
    @app_commands.guild_only()
    async def event_info(self, interaction: discord.Interaction) -> None:
        summary = self.store.get_event_summary(str(interaction.guild_id))
        if not summary:
            await interaction.response.send_message("現在開催中のボスイベントはありません。", ephemeral=True)
            return
        remaining = int(summary["ends_at"] - time.time())
        embed = discord.Embed(
            title=f"{summary['icon']} {summary['boss_name']}",
            description=(
                f"制限時間残り：{self._duration_text(remaining)}\n"
                f"ソロ挑戦人数：{summary['solo_participant_count']}人\n"
                f"パーティ参加人数：{summary['party_participant_count']}人"
            ),
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="ソロHP（進行中の合計）",
            value=f"{summary['solo_hp_remaining']:,} / {summary['solo_hp_total']:,}",
            inline=True,
        )
        embed.add_field(
            name="パーティHP（進行中の合計）",
            value=f"{summary['party_hp_remaining']:,} / {summary['party_hp_total']:,}",
            inline=True,
        )
        if summary["special_event"]:
            embed.add_field(name="特殊イベント", value=summary["special_event"], inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name=ja("boss_join", "参加"),
        description=ja("Join the current boss event solo or as a party", "開催中のボス討伐に参加します"),
    )
    @app_commands.guild_only()
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="ソロ", value="solo"),
            app_commands.Choice(name="パーティ", value="party"),
        ]
    )
    @app_commands.rename(mode=ja("mode", "モード"))
    async def join_event(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            forge = await self.service.get_forge(str(interaction.user.id))
            if not forge.get("weaponId"):
                await interaction.followup.send("❌ ボス討伐には武器が必要です。先に /武器選択 を実行してください。", ephemeral=True)
                return
            name = interaction.user.display_name
            if mode.value == "solo":
                run = self.store.create_solo_run(
                    str(interaction.guild_id), str(interaction.user.id), name
                )
                await interaction.followup.send(
                    f"⚔️ {run['icon']} {run['boss_name']} へソロで挑戦開始！\n"
                    f"ボスHP：{run['current_hp']:,}\n"
                    f"自分のHP：{PLAYER_MAX_HP}",
                    ephemeral=True,
                )
                return

            run, started = self.store.join_party(
                str(interaction.guild_id), str(interaction.user.id), name
            )
            count = len(run.get("members", []))
            if started:
                mentions = " ".join(f"<@{member['user_id']}>" for member in run["members"])
                await interaction.followup.send(
                    f"⚔️ パーティが{PARTY_MAX_SIZE}人に達したため討伐開始！ {mentions}\n"
                    f"{run['icon']} {run['boss_name']}　HP：{run['current_hp']:,}",
                    ephemeral=False,
                )
            else:
                await interaction.followup.send(
                    f"パーティ受付中です（{count}/{PARTY_MAX_SIZE}人）。"
                    f"{PARTY_MIN_SIZE}人以上で受付終了時に開始します。",
                    ephemeral=True,
                )
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(
        name=ja("boss_attack", "攻撃"),
        description=ja("Attack the boss", "参加中のボスを攻撃します"),
    )
    @app_commands.guild_only()
    async def attack(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            run = self.store.get_user_run(str(interaction.user.id))
            if not run:
                raise NotHunting("先に /参加 でボス討伐に参加してください。")
            forge = await self.service.get_forge(str(interaction.user.id))
            roles = (
                [role.name for role in interaction.user.roles]
                if isinstance(interaction.user, discord.Member)
                else []
            )
            damage = calculate_damage(
                str(forge.get("weaponId", "")),
                int(forge.get("level", 1)),
                roles,
                int(run["boss_defense"]),
            )
            counter_damage = calculate_boss_counter_damage(
                int(run["boss_attack"]),
                int(forge.get("defense", 0)),
                int(forge.get("level", 1)),
            )
            outcome = await self.service.perform_action(
                interaction_id=str(interaction.id),
                user_id=str(interaction.user.id),
                action_type="attack",
                cost=BOSS_ATTACK_COST,
                damage=damage.damage,
                is_critical=damage.critical,
                counter_damage=counter_damage,
            )
            await self._send_action_result(interaction, outcome)
        except Exception as error:
            await self._respond_error(interaction, error)

    @app_commands.command(
        name=ja("boss_heal", "回復"),
        description=ja("Heal yourself during a boss fight", "参加中に自分を回復します"),
    )
    @app_commands.guild_only()
    async def heal(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            user_id = str(interaction.user.id)
            run = self.store.get_user_run(user_id)
            if not run:
                raise NotHunting("先に /参加 でボス討伐に参加してください。")
            if not self.store.get_pending_action_for_user(user_id) and run["player_hp"] >= PLAYER_MAX_HP:
                await interaction.followup.send("HPはすでに最大です。回復費用はかかりません。", ephemeral=True)
                return
            outcome = await self.service.perform_action(
                interaction_id=str(interaction.id),
                user_id=user_id,
                action_type="heal",
                cost=BOSS_HEAL_COST,
                heal_amount=BOSS_HEAL_AMOUNT,
            )
            await self._send_action_result(interaction, outcome)
        except Exception as error:
            await self._respond_error(interaction, error)

    async def _send_action_result(
        self, interaction: discord.Interaction, outcome: dict[str, Any]
    ) -> None:
        recovered = bool(outcome.get("recovered_previous_action"))
        prefix = "前回の処理を復旧しました。\n" if recovered else ""
        if outcome["action_type"] == "heal":
            message = (
                f"{prefix}💚 HPを{outcome.get('healed', 0)}回復しました。"
                f"（{outcome.get('player_hp', 0)}/{PLAYER_MAX_HP}）"
            )
        else:
            critical = " 会心の一撃！" if outcome.get("critical") else ""
            message = (
                f"{prefix}⚔️ ボスに{outcome.get('damage', 0)}ダメージ！{critical}\n"
                f"ボス残りHP：{outcome.get('hp_remaining', 0):,}/{outcome.get('max_hp', 0):,}\n"
                f"自分のHP：{outcome.get('player_hp', PLAYER_MAX_HP)}"
            )
        message += f"\n消費：${int(outcome.get('cost', 0)):,}"
        await interaction.followup.send(message, ephemeral=True)
        if outcome.get("killed") and outcome.get("newly_applied"):
            await self._announce_victory(str(outcome["run_id"]))

    async def _announce_victory(self, run_id: str) -> None:
        await self.service.pay_pending_rewards()
        result = self.store.get_run_result(run_id)
        if not result:
            return
        mvp = result.get("mvp")
        mvp_text = f"<@{mvp['user_id']}>（{mvp['damage_total']:,}ダメージ）" if mvp else "なし"
        members = result["members"]
        rewards = result.get("payouts", [])
        all_paid = bool(rewards) and all(reward["status"] == "paid" for reward in rewards)
        reward_text = (
            f"${sum(reward['amount'] for reward in rewards):,}を支給しました。"
            if all_paid
            else "報酬を凪APIで支給処理中です。"
        )
        message = (
            f"🏆 **討伐成功！** {result['icon']} {result['boss_name']}\n"
            f"討伐時間：{self._duration_text(result['duration_seconds'])}\n"
            f"参加人数：{len(members)}人　/　総ダメージ：{result['total_damage']:,}\n"
            f"MVP：{mvp_text}\n報酬：{reward_text}"
        )
        await self._send_to_event(result["event_id"], message)


async def setup(
    bot: commands.Bot,
    api,
    mirror,
    store: BossStore,
) -> None:
    await bot.add_cog(BossCog(bot, api, mirror, store))
