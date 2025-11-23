import asyncio
import datetime
from collections import defaultdict, deque
from typing import Dict, Any, Deque, Tuple, List, Set

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "protect_antinuke": {
        "enabled": True,
        "debug": False,
        "log_channel_id": 0,
        "interval_seconds": 30,
        "thresholds": {
            "channel_delete": 3,
            "role_delete": 2,
            "emoji_delete": 5,
            "webhook_delete": 5,
            "combined": 6
        },
        "monitor": {
            "channel_delete": True,
            "role_delete": True,
            "emoji_delete": True,
            "webhook_delete": True
        },
        "punishment": {
            "type": "ban",  # ban | kick | timeout | remove_roles
            "timeout_seconds": 1800,
            "remove_roles": True,
            "remove_dangerous_only": True,
            "keep_role_ids": []
        },
        "dangerous_permissions": ["administrator", "manage_channels", "manage_roles", "manage_webhooks", "manage_emojis"],
        "lockdown": {
            "enabled": True,
            "apply_on_trigger": True,
            "slowmode_seconds": 10,
            "target_channel_ids": [],
            "all_text_channels": True,
            "remove_manage_channels_from_roles": True,
            "restore_after_seconds": 600
        },
        "messages": {
            "log_delete": "AntiNuke: {action} por {executor} alvo={target} (total {count}/{threshold} em {interval}s)",
            "log_punish": "Punindo {executor} por ações massivas: {reason}",
            "log_lockdown": "Lockdown aplicado: slowmode={slowmode}s canais={channel_count}",
            "log_restore": "Lockdown restaurado.",
            "status_header": "Proteção AntiNuke",
            "status_main": "Enabled={enabled} | Interval={interval}s | Combined={combined_t}/{combined_th}",
            "status_thresholds": "Del Canal={cd_t}/{cd_th} | Del Cargo={rd_t}/{rd_th} | Del Emoji={ed_t}/{ed_th} | Del Webhook={wd_t}/{wd_th}",
            "punish_reason": "Excesso de ações destrutivas"
        },
        "log_embed": {
            "enabled": True,
            "color": "AA00FF",
            "title_event": "Evento AntiNuke",
            "title_punish": "Usuário punido (AntiNuke)",
            "title_lockdown": "Lockdown Ativado",
            "title_restore": "Lockdown Encerrado"
        }
    }
}


class ProtectAntiNukeCog(commands.Cog):
    """Protege contra deleções em massa de canais, cargos, emojis e webhooks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('protect_antinuke', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('protect_antinuke', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.debug: bool = self.cfg.get('debug', False)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.interval: int = int(self.cfg.get('interval_seconds', 30))
        self.thresholds: Dict[str, int] = self.cfg.get('thresholds', {})
        self.monitor_cfg: Dict[str, bool] = self.cfg.get('monitor', {})
        self.punish_cfg: Dict[str, Any] = self.cfg.get('punishment', {})
        self.dangerous_perms: List[str] = self.cfg.get('dangerous_permissions', [])
        self.lockdown_cfg: Dict[str, Any] = self.cfg.get('lockdown', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})

        # executor_id -> deque timestamps por ação
        self._action_windows: Dict[str, Dict[int, Deque[float]]] = defaultdict(lambda: defaultdict(lambda: deque()))
        # executor_id -> combined total recente
        self._combined_counts: Dict[int, Deque[Tuple[str, float]]] = defaultdict(lambda: deque())
        # lockdown estado
        self._lockdown_active: bool = False
        self._original_slowmodes: Dict[int, int] = {}
        self._original_role_perms: Dict[int, discord.Permissions] = {}
        self._restore_task: asyncio.Task | None = None

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('protect_antinuke')
        self.__init__(self.bot)

    # ---------------- Utilidades -----------------
    def _now(self) -> float:
        return asyncio.get_event_loop().time()

    async def _log(self, guild: discord.Guild, title: str, fields: List[tuple]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.embed_cfg.get('enabled', True):
            color_hex = self.embed_cfg.get('color', 'AA00FF')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('AA00FF', 16)
            embed = discord.Embed(title=title, color=color_val)
            for n, v, inline in fields:
                embed.add_field(name=n, value=v, inline=inline)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
        else:
            try:
                lines = [title] + [f"{n}: {v}" for n, v, _ in fields]
                await ch.send('\n'.join(lines))
            except Exception:
                pass

    def _record_action(self, executor_id: int, action_key: str):
        now = self._now()
        dq = self._action_windows[action_key][executor_id]
        dq.append(now)
        # expurgo
        while dq and (now - dq[0]) > self.interval:
            dq.popleft()
        # combined
        c_dq = self._combined_counts[executor_id]
        c_dq.append((action_key, now))
        while c_dq and (now - c_dq[0][1]) > self.interval:
            c_dq.popleft()

    def _count(self, executor_id: int, action_key: str) -> int:
        return len(self._action_windows[action_key][executor_id])

    def _combined_count(self, executor_id: int) -> int:
        return len(self._combined_counts[executor_id])

    def _exceeds_threshold(self, executor_id: int, action_key: str) -> bool:
        th = int(self.thresholds.get(action_key, 999999))
        return th > 0 and self._count(executor_id, action_key) >= th

    def _exceeds_combined(self, executor_id: int) -> bool:
        th = int(self.thresholds.get('combined', 999999))
        return th > 0 and self._combined_count(executor_id) >= th

    async def _apply_lockdown(self, guild: discord.Guild):
        if self._lockdown_active or not self.lockdown_cfg.get('enabled', True):
            return
        self._lockdown_active = True
        slow_val = int(self.lockdown_cfg.get('slowmode_seconds', 10))
        targets: List[discord.TextChannel] = []
        if self.lockdown_cfg.get('all_text_channels', True):
            targets.extend(guild.text_channels)
        else:
            for cid in self.lockdown_cfg.get('target_channel_ids', []):
                c = guild.get_channel(cid)
                if isinstance(c, discord.TextChannel):
                    targets.append(c)
        changed = 0
        for ch in targets:
            try:
                self._original_slowmodes[ch.id] = ch.rate_limit_per_user
                if ch.rate_limit_per_user != slow_val:
                    await ch.edit(rate_limit_per_user=slow_val, reason='AntiNuke Lockdown')
                    changed += 1
                    await asyncio.sleep(0.25)
            except Exception:
                pass
        # Remover permissões perigosas de roles (opcional)
        if self.lockdown_cfg.get('remove_manage_channels_from_roles', True):
            for role in guild.roles:
                if role.is_default():
                    continue
                perms = role.permissions
                original = perms.value
                if perms.manage_channels or perms.manage_roles or perms.administrator:
                    self._original_role_perms[role.id] = perms
                    new_perms = discord.Permissions(value=original)
                    new_perms.manage_channels = False
                    new_perms.manage_roles = False
                    new_perms.administrator = False
                    try:
                        await role.edit(permissions=new_perms, reason='AntiNuke Lockdown perms strip')
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
        await self._log(guild, self.embed_cfg.get('title_lockdown', 'Lockdown Ativado'), [
            ('Slowmode', f'{slow_val}s', True),
            ('Canais alterados', str(changed), True),
            ('Roles perms alterados', str(len(self._original_role_perms)), True)
        ])
        restore_after = int(self.lockdown_cfg.get('restore_after_seconds', 600))
        if restore_after > 0:
            self._restore_task = asyncio.create_task(self._restore_lockdown_later(guild, restore_after))

    async def _restore_lockdown_later(self, guild: discord.Guild, seconds: int):
        try:
            await asyncio.sleep(seconds)
            await self._restore_lockdown(guild, auto=True)
        except asyncio.CancelledError:
            pass

    async def _restore_lockdown(self, guild: discord.Guild, auto: bool = False):
        if not self._lockdown_active:
            return
        # Reverter slowmode
        for cid, original in list(self._original_slowmodes.items()):
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                try:
                    if ch.rate_limit_per_user != original:
                        await ch.edit(rate_limit_per_user=original, reason='AntiNuke Lockdown restore')
                        await asyncio.sleep(0.25)
                except Exception:
                    pass
        self._original_slowmodes.clear()
        # Reverter permissões
        for rid, perms in list(self._original_role_perms.items()):
            role = guild.get_role(rid)
            if role:
                try:
                    await role.edit(permissions=perms, reason='AntiNuke restore perms')
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
        self._original_role_perms.clear()
        self._lockdown_active = False
        await self._log(guild, self.embed_cfg.get('title_restore', 'Lockdown Encerrado'), [
            ('Origem', 'Auto' if auto else 'Manual', True)
        ])
        if self._restore_task and not self._restore_task.done():
            self._restore_task.cancel()
        self._restore_task = None

    async def _punish(self, guild: discord.Guild, member: discord.Member, reason: str):
        if not member:
            return
        ptype = self.punish_cfg.get('type', 'ban')
        await self._log(guild, self.embed_cfg.get('title_punish', 'Usuário punido'), [
            ('Executor', member.mention, True),
            ('Ação', ptype, True),
            ('Motivo', reason, False)
        ])
        try:
            if ptype == 'ban':
                await guild.ban(member, reason=reason, delete_message_days=0)
            elif ptype == 'kick':
                await guild.kick(member, reason=reason)
            elif ptype == 'timeout':
                seconds = int(self.punish_cfg.get('timeout_seconds', 1800))
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
                await member.timeout(until, reason=reason)
            elif ptype == 'remove_roles':
                keep_ids = set(self.punish_cfg.get('keep_role_ids', []))
                dangerous_only = self.punish_cfg.get('remove_dangerous_only', True)
                to_remove = []
                for r in member.roles:
                    if r.is_default() or r.id in keep_ids:
                        continue
                    if dangerous_only:
                        perms = r.permissions
                        if any(getattr(perms, p, False) for p in self.dangerous_perms):
                            to_remove.append(r)
                    else:
                        to_remove.append(r)
                if to_remove:
                    await member.remove_roles(*to_remove, reason=reason)
        except Exception:
            if self.debug:
                print('[antinuke] Falha ao punir usuário')

    async def _handle_action(self, guild: discord.Guild, action: str, target: str, executor: discord.Member):
        if not executor:
            return
        self._record_action(executor.id, action)
        count = self._count(executor.id, action)
        threshold = int(self.thresholds.get(action, 999999))
        combined_t = self._combined_count(executor.id)
        combined_th = int(self.thresholds.get('combined', 999999))
        # Log do evento
        await self._log(guild, self.embed_cfg.get('title_event', 'Evento AntiNuke'), [
            ('Executor', executor.mention, True),
            ('Ação', action, True),
            ('Alvo', target, True),
            ('Count', f'{count}/{threshold}', True),
            ('Combined', f'{combined_t}/{combined_th}', True)
        ])
        # Verificar thresholds
        if self._exceeds_threshold(executor.id, action) or self._exceeds_combined(executor.id):
            await self._punish(guild, executor, self.msgs.get('punish_reason', 'Excesso de ações destrutivas'))
            # Ativar lockdown se configurado
            if self.lockdown_cfg.get('enabled', True) and self.lockdown_cfg.get('apply_on_trigger', True):
                await self._apply_lockdown(guild)

    # ---------------- Audit Helpers -----------------
    async def _fetch_audit_executor(self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int | None) -> discord.Member | None:
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                if target_id is not None:
                    if getattr(entry.target, 'id', None) != target_id:
                        continue
                return guild.get_member(entry.user.id)
        except Exception:
            return None
        return None

    # ---------------- Eventos -----------------
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not self.enabled or not self.monitor_cfg.get('channel_delete', True):
            return
        guild = channel.guild
        executor = await self._fetch_audit_executor(guild, discord.AuditLogAction.channel_delete, channel.id)
        await self._handle_action(guild, 'channel_delete', f'{channel.name}', executor)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not self.enabled or not self.monitor_cfg.get('role_delete', True):
            return
        guild = role.guild
        executor = await self._fetch_audit_executor(guild, discord.AuditLogAction.role_delete, role.id)
        await self._handle_action(guild, 'role_delete', f'{role.name}', executor)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before: List[discord.Emoji], after: List[discord.Emoji]):
        if not self.enabled or not self.monitor_cfg.get('emoji_delete', True):
            return
        # Emojis removidos
        removed = {e.id: e for e in before if e.id not in {a.id for a in after}}
        if not removed:
            return
        for eid, emoji in list(removed.items())[:5]:  # limita spam
            executor = await self._fetch_audit_executor(guild, discord.AuditLogAction.emoji_delete, eid)
            await self._handle_action(guild, 'emoji_delete', f'{emoji.name}', executor)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        if not self.enabled or not self.monitor_cfg.get('webhook_delete', True):
            return
        guild = channel.guild
        # audit log para deletions recentes
        executor = await self._fetch_audit_executor(guild, discord.AuditLogAction.webhook_delete, None)
        if executor:
            await self._handle_action(guild, 'webhook_delete', f'{channel.name}', executor)

    # ---------------- Commands -----------------
    @commands.command(name='antinukestatus')
    async def antinuke_status(self, ctx: commands.Context):
        t = self.thresholds
        # Totais atuais por ação para autor (diagnóstico local) - opcional
        user_id = ctx.author.id
        cd_t = self._count(user_id, 'channel_delete')
        rd_t = self._count(user_id, 'role_delete')
        ed_t = self._count(user_id, 'emoji_delete')
        wd_t = self._count(user_id, 'webhook_delete')
        combined_t = self._combined_count(user_id)
        lines = [self.msgs.get('status_header', 'Proteção AntiNuke')]
        lines.append(self.msgs.get('status_main', 'Enabled={enabled} | Interval={interval}s | Combined={combined_t}/{combined_th}').format(
            enabled=self.enabled, interval=self.interval, combined_t=combined_t, combined_th=t.get('combined', 0)
        ))
        lines.append(self.msgs.get('status_thresholds', '...').format(
            cd_t=cd_t, cd_th=t.get('channel_delete', 0),
            rd_t=rd_t, rd_th=t.get('role_delete', 0),
            ed_t=ed_t, ed_th=t.get('emoji_delete', 0),
            wd_t=wd_t, wd_th=t.get('webhook_delete', 0)
        ))
        await ctx.reply('\n'.join(lines))

    @commands.command(name='antinukereload')
    async def antinuke_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config AntiNuke recarregada.')

    @commands.command(name='antinukerestorelockdown')
    async def antinuke_restore_lockdown(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        await self._restore_lockdown(ctx.guild, auto=False)
        await ctx.reply('Lockdown restaurado manualmente.')

    @commands.command(name='antinukeclear')
    async def antinuke_clear(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self._action_windows.clear()
        self._combined_counts.clear()
        await ctx.reply('Counters AntiNuke limpos.')

async def setup(bot: commands.Bot):
    await bot.add_cog(ProtectAntiNukeCog(bot))
