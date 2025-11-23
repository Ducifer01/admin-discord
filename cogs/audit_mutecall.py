import datetime
import discord
from discord.ext import commands
from typing import Dict, Any, List, Tuple
from config_loader import config_manager

DEFAULTS = {
    "audit_mutecall": {
        "enabled": True,
        "log_channel_id": 0,
        "audit_window_seconds": 5,
        "log_embed": {
            "enabled": True,
            "color": "9933FF",
            "title_mute": "Silêncio em call",
            "title_unmute": "Remoção de silêncio",
            "title_deaf": "Ensurdecimento em call",
            "title_undeaf": "Remoção de ensurdecimento"
        },
        "messages": {
            "mute_other": "{executor} silenciou {target}",
            "unmute_other": "{executor} removeu silêncio de {target}",
            "deaf_other": "{executor} ensurdeceu {target}",
            "undeaf_other": "{executor} removeu ensurdecimento de {target}",
            "mute_self": "{target} se auto-silenciou (ignorado)",
            "deaf_self": "{target} se auto-ensurdeceu (ignorado)",
            "status_header": "Auditoria mute/deaf call",
            "status_main": "Habilitado: {enabled} | Janela: {window}s",
            "status_channel": "Canal log: {channel}"
        },
        "debug": False
    }
}

class AuditMuteCallCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('audit_mutecall', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('audit_mutecall', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.window: int = self.cfg.get('audit_window_seconds', 5)
        self.embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('audit_mutecall')
        self.__init__(self.bot)

    async def _log(self, guild: discord.Guild, title: str, lines: List[Tuple[str,str,bool]]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.embed_cfg.get('enabled', True):
            color_hex = self.embed_cfg.get('color', '9933FF')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('9933FF', 16)
            embed = discord.Embed(title=title, color=color_val)
            for n,v,i in lines:
                embed.add_field(name=n, value=v, inline=i)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
        else:
            try:
                txt = title + '\n' + '\n'.join(f"{n}: {v}" for n,v,_ in lines)
                await ch.send(txt)
            except Exception:
                pass

    async def _find_executor(self, guild: discord.Guild, target: discord.Member) -> discord.User | None:
        if not guild.me.guild_permissions.view_audit_log:
            return None
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_update):
            if entry.target and getattr(entry.target, 'id', None) == target.id:
                created = entry.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=datetime.timezone.utc)
                if (now - created).total_seconds() <= self.window:
                    return entry.user
        return None

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not self.enabled:
            return
        # Server mute/deaf changes (ignore self mute/deaf)
        if before.mute != after.mute:
            executor = await self._find_executor(member.guild, member)
            if after.mute:
                title = self.embed_cfg.get('title_mute', 'Silêncio em call')
                if executor:
                    msg = self.msgs.get('mute_other', '{executor} silenciou {target}').format(executor=executor.mention, target=member.mention)
                else:
                    msg = self.msgs.get('mute_self', '{target} se auto-silenciou (ignorado)').format(target=member.mention)
            else:
                title = self.embed_cfg.get('title_unmute', 'Remoção de silêncio')
                if executor:
                    msg = self.msgs.get('unmute_other', '{executor} removeu silêncio de {target}').format(executor=executor.mention, target=member.mention)
                else:
                    msg = f"Silêncio removido de {member.mention} (executor desconhecido)"
            await self._log(member.guild, title, [('Evento', msg, False)])
        if before.deaf != after.deaf:
            executor = await self._find_executor(member.guild, member)
            if after.deaf:
                title = self.embed_cfg.get('title_deaf', 'Ensurd.')
                if executor:
                    msg = self.msgs.get('deaf_other', '{executor} ensurdeceu {target}').format(executor=executor.mention, target=member.mention)
                else:
                    msg = self.msgs.get('deaf_self', '{target} se auto-ensurdeceu (ignorado)').format(target=member.mention)
            else:
                title = self.embed_cfg.get('title_undeaf', 'Remoção de ensurdecimento')
                if executor:
                    msg = self.msgs.get('undeaf_other', '{executor} removeu ensurdecimento de {target}').format(executor=executor.mention, target=member.mention)
                else:
                    msg = f"Ensurdecimento removido de {member.mention} (executor desconhecido)"
            await self._log(member.guild, title, [('Evento', msg, False)])

    @commands.command(name='mutecallauditreload')
    async def mutecall_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config audit_mutecall recarregada.')

    @commands.command(name='mutecallauditstatus')
    async def mutecall_status(self, ctx: commands.Context):
        m = self.msgs
        ch = ctx.guild.get_channel(self.log_channel_id)
        lines = [m.get('status_header', 'Auditoria mute/deaf call')]
        lines.append(m.get('status_main', '').format(enabled=self.enabled, window=self.window))
        lines.append(m.get('status_channel', '').format(channel=ch.mention if ch else '(não definido)'))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(AuditMuteCallCog(bot))
