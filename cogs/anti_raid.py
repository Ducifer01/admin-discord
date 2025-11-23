import asyncio
import datetime
from collections import deque
from typing import Deque, Dict, Any, List, Set

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "anti_raid": {
        "enabled": True,
        "detection": {
            "join_threshold_count": 10,
            "join_threshold_interval_seconds": 30,
            "min_account_age_hours_flag": 12,
            "flagged_join_threshold_count": 5,
            "sliding_window_seconds": 120
        },
        "emergency": {
            "auto_disable_seconds": 600,
            "apply_slowmode": True,
            "slowmode_seconds": 8,
            "apply_slowmode_channel_ids": [],
            "apply_slowmode_all_text": True,
            "revert_slowmode_seconds": 0,
            "timeout_newcomers": True,
            "timeout_duration_seconds": 900,
            "timeout_account_age_hours_max": 72,
            "revoke_invites": True,
            "recreate_invites_after": False,
            "notify_channel_id": 0,
            "notify_ping_role_ids": [],
            "notify_ping_user_ids": [],
            "notify_embed": {
                "enabled": True,
                "color": "FF0000",
                "title_activate": "⚠️ Modo Emergência Anti-Raid Ativado",
                "title_deactivate": "✅ Modo Emergência Desativado"
            }
        },
        "manual": {
            "command_whitelist_user_ids": [],
            "command_whitelist_role_ids": [],
            "require_manage_guild": True
        },
        "messages": {
            "status_header": "Anti-Raid Status",
            "status_values": "Emergência: {active} | Entradas janela: {joins} | Flagged: {flagged} | Threshold: {threshold}/{interval}s",
            "activated_reason": "Ativado automaticamente: {count} entradas em {interval}s",
            "manual_activate": "Modo emergência ativado manualmente por {user}.",
            "manual_deactivate": "Modo emergência desativado manualmente por {user}.",
            "timeout_reason": "Modo emergência anti-raid (conta jovem)",
            "invite_revoke_fail": "Falha ao revogar convites: {error}"
        },
        "log_channel_id": 0,
        "debug": False
    }
}


class AntiRaidCog(commands.Cog):
    """Detecção de raid: monitora taxa de joins e idade das contas e ativa modo emergência."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('anti_raid', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('anti_raid', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.detection: Dict[str, Any] = self.cfg.get('detection', {})
        self.emergency_cfg: Dict[str, Any] = self.cfg.get('emergency', {})
        self.manual_cfg: Dict[str, Any] = self.cfg.get('manual', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.debug: bool = self.cfg.get('debug', False)

        self._join_times: Deque[float] = deque(maxlen=500)
        self._flagged_join_times: Deque[float] = deque(maxlen=500)
        self._emergency_active: bool = False
        self._emergency_started_at: float | None = None
        self._auto_disable_task: asyncio.Task | None = None
        self._original_slowmodes: Dict[int, int] = {}

    # ---------------- Recarregar -----------------
    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('anti_raid')
        self.__init__(self.bot)

    # ---------------- Utilidades -----------------
    def _now(self) -> float:
        return asyncio.get_event_loop().time()

    def _account_age_hours(self, user: discord.User) -> float:
        created = getattr(user, 'created_at', None)
        if not created:
            return 999999.0
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        delta = datetime.datetime.now(datetime.timezone.utc) - created
        return delta.total_seconds() / 3600.0

    def _is_manual_authorized(self, member: discord.Member) -> bool:
        if not member:
            return False
        if self.manual_cfg.get('require_manage_guild', True) and not member.guild_permissions.manage_guild:
            return False
        if member.id in self.manual_cfg.get('command_whitelist_user_ids', []):
            return True
        role_ids: Set[int] = set(self.manual_cfg.get('command_whitelist_role_ids', []))
        if role_ids and any(r.id in role_ids for r in member.roles):
            return True
        # Se listas vazias, fallback para manage_guild já validada
        if not role_ids and not self.manual_cfg.get('command_whitelist_user_ids', []):
            return True
        return False

    async def _log_embed(self, guild: discord.Guild, title: str, fields: List[tuple], color_hex: str | None = None):
        channel_id = self.emergency_cfg.get('notify_channel_id') or self.log_channel_id
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        embed_cfg = self.emergency_cfg.get('notify_embed', {})
        if not embed_cfg.get('enabled', True):
            # texto simples
            try:
                lines = [title] + [f"{n}: {v}" for n, v, _ in fields]
                await ch.send('\n'.join(lines))
            except Exception:
                pass
            return
        color_hex = color_hex or embed_cfg.get('color', 'FF0000')
        try:
            color_val = int(str(color_hex), 16)
        except Exception:
            color_val = int('FF0000', 16)
        embed = discord.Embed(title=title, color=color_val)
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        p_roles = self.emergency_cfg.get('notify_ping_role_ids', [])
        p_users = self.emergency_cfg.get('notify_ping_user_ids', [])
        ping_text = ''
        if p_roles:
            ping_text += ' '.join(f'<@&{rid}>' for rid in p_roles)
        if p_users:
            ping_text += ' ' + ' '.join(f'<@{uid}>' for uid in p_users)
        try:
            if ping_text.strip():
                await ch.send(ping_text.strip(), embed=embed)
            else:
                await ch.send(embed=embed)
        except Exception:
            pass

    # ---------------- Emergência -----------------
    async def _activate_emergency(self, guild: discord.Guild, reason: str):
        if self._emergency_active:
            return
        self._emergency_active = True
        self._emergency_started_at = self._now()
        embed_cfg = self.emergency_cfg.get('notify_embed', {})
        title = embed_cfg.get('title_activate', 'Modo Emergência Ativado')
        await self._log_embed(guild, title, [
            ('Motivo', reason, False),
            ('Entradas janela', str(len(self._join_times)), True),
            ('Flagged', str(len(self._flagged_join_times)), True)
        ])
        # Ações
        if self.emergency_cfg.get('revoke_invites', True):
            try:
                invites = await guild.invites()
                for inv in invites:
                    try:
                        await inv.delete(reason='Anti-Raid emergência')
                        await asyncio.sleep(0.3)  # rate-limit friendly
                    except Exception:
                        pass
            except Exception as e:
                await self._log_embed(guild, 'Aviso revogar convites', [
                    ('Erro', f'```{e}```', False)
                ])
        if self.emergency_cfg.get('apply_slowmode', True):
            target_channels: List[discord.TextChannel] = []
            if self.emergency_cfg.get('apply_slowmode_all_text', True):
                for ch in guild.text_channels:
                    target_channels.append(ch)
            else:
                ids = set(self.emergency_cfg.get('apply_slowmode_channel_ids', []))
                for cid in ids:
                    c = guild.get_channel(cid)
                    if isinstance(c, discord.TextChannel):
                        target_channels.append(c)
            slow_val = int(self.emergency_cfg.get('slowmode_seconds', 8))
            for ch in target_channels:
                try:
                    self._original_slowmodes[ch.id] = ch.rate_limit_per_user
                    if ch.rate_limit_per_user != slow_val:
                        await ch.edit(rate_limit_per_user=slow_val, reason='Anti-Raid emergência slowmode')
                        await asyncio.sleep(0.25)
                except Exception:
                    pass
        # Auto-disable
        auto_sec = int(self.emergency_cfg.get('auto_disable_seconds', 600))
        if auto_sec > 0:
            self._auto_disable_task = asyncio.create_task(self._auto_disable_later(guild, auto_sec))

    async def _auto_disable_later(self, guild: discord.Guild, seconds: int):
        try:
            await asyncio.sleep(seconds)
            await self._deactivate_emergency(guild, auto=True)
        except asyncio.CancelledError:
            pass

    async def _deactivate_emergency(self, guild: discord.Guild, auto: bool = False, user: discord.Member | None = None):
        if not self._emergency_active:
            return
        self._emergency_active = False
        embed_cfg = self.emergency_cfg.get('notify_embed', {})
        title = embed_cfg.get('title_deactivate', 'Modo Emergência Desativado')
        reason_field = 'Automático (timeout)' if auto else (f'Manual por {user.mention}' if user else 'Manual')
        await self._log_embed(guild, title, [
            ('Origem', reason_field, False),
            ('Duração (s)', f"{int(self._now() - (self._emergency_started_at or self._now()))}", True)
        ], color_hex='00AA55')
        self._emergency_started_at = None
        # Reverter slowmode
        if self.emergency_cfg.get('apply_slowmode', True) and self._original_slowmodes:
            revert_val = int(self.emergency_cfg.get('revert_slowmode_seconds', 0))
            for cid, original in list(self._original_slowmodes.items()):
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    try:
                        target_val = revert_val if revert_val >= 0 else original
                        if ch.rate_limit_per_user != target_val:
                            await ch.edit(rate_limit_per_user=target_val, reason='Anti-Raid revert slowmode')
                            await asyncio.sleep(0.25)
                    except Exception:
                        pass
            self._original_slowmodes.clear()
        # Cancel auto task
        if self._auto_disable_task and not self._auto_disable_task.done():
            self._auto_disable_task.cancel()
        self._auto_disable_task = None

    # ---------------- Detecção -----------------
    def _record_join(self, age_hours: float):
        now = self._now()
        self._join_times.append(now)
        if age_hours <= float(self.detection.get('min_account_age_hours_flag', 12)):
            self._flagged_join_times.append(now)
        # Expurgo por sliding_window
        window_sec = float(self.detection.get('sliding_window_seconds', 120))
        while self._join_times and (now - self._join_times[0]) > window_sec:
            self._join_times.popleft()
        while self._flagged_join_times and (now - self._flagged_join_times[0]) > window_sec:
            self._flagged_join_times.popleft()

    def _should_activate(self) -> str | None:
        # Retorna motivo ou None
        count_needed = int(self.detection.get('join_threshold_count', 10))
        interval = float(self.detection.get('join_threshold_interval_seconds', 30))
        flagged_needed = int(self.detection.get('flagged_join_threshold_count', 5))
        now = self._now()
        # Conta quantos joins últimos interval segundos
        recent = [t for t in self._join_times if now - t <= interval]
        if len(recent) >= count_needed:
            return self.msgs.get('activated_reason', 'Threshold atingido').format(count=len(recent), interval=int(interval))
        flagged_recent = [t for t in self._flagged_join_times if now - t <= interval]
        if flagged_needed > 0 and len(flagged_recent) >= flagged_needed:
            return f"Flagged atingido: {len(flagged_recent)} em {int(interval)}s"
        return None

    # ---------------- Eventos -----------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self.enabled or not member.guild:
            return
        guild = member.guild
        age_hours = self._account_age_hours(member)
        self._record_join(age_hours)
        # Check trigger
        if not self._emergency_active:
            reason = self._should_activate()
            if reason:
                await self._activate_emergency(guild, reason)
        # Apply newcomer timeout if emergency active
        if self._emergency_active and self.emergency_cfg.get('timeout_newcomers', True):
            max_age = float(self.emergency_cfg.get('timeout_account_age_hours_max', 72))
            if age_hours <= max_age:
                duration = int(self.emergency_cfg.get('timeout_duration_seconds', 900))
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
                try:
                    await member.timeout(until, reason=self.msgs.get('timeout_reason', 'Anti-raid'))
                except Exception:
                    if self.debug:
                        print('[anti_raid] Falha timeout newcomer')

    # ---------------- Comandos -----------------
    @commands.command(name='antiraidstatus')
    async def anti_raid_status(self, ctx: commands.Context):
        if not ctx.guild:
            return
        now = self._now()
        interval = float(self.detection.get('join_threshold_interval_seconds', 30))
        recent = [t for t in self._join_times if now - t <= interval]
        flagged_recent = [t for t in self._flagged_join_times if now - t <= interval]
        header = self.msgs.get('status_header', 'Anti-Raid Status')
        line = self.msgs.get('status_values', 'Estado').format(
            active=self._emergency_active,
            joins=len(recent),
            flagged=len(flagged_recent),
            threshold=self.detection.get('join_threshold_count', 10),
            interval=int(interval)
        )
        await ctx.reply(f"{header}\n{line}")

    @commands.command(name='antiraidactivate')
    async def anti_raid_activate(self, ctx: commands.Context):
        if not self._is_manual_authorized(ctx.author):
            return await ctx.reply('Sem permissão.')
        if self._emergency_active:
            return await ctx.reply('Já ativo.')
        await self._activate_emergency(ctx.guild, self.msgs.get('manual_activate', 'Manual').format(user=ctx.author.mention))
        await ctx.reply('Modo emergência ativado.')

    @commands.command(name='antiraiddeactivate')
    async def anti_raid_deactivate(self, ctx: commands.Context):
        if not self._is_manual_authorized(ctx.author):
            return await ctx.reply('Sem permissão.')
        if not self._emergency_active:
            return await ctx.reply('Não está ativo.')
        await self._deactivate_emergency(ctx.guild, auto=False, user=ctx.author)
        await ctx.reply('Modo emergência desativado.')

    @commands.command(name='antiraidreload')
    async def anti_raid_reload(self, ctx: commands.Context):
        if not self._is_manual_authorized(ctx.author):
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config anti_raid recarregada.')

async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaidCog(bot))
