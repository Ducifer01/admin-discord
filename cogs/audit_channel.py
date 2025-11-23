import datetime
import discord
from discord.ext import commands
from typing import Dict, Any, List, Tuple
from config_loader import config_manager

DEFAULTS = {
    "audit_channel": {
        "enabled": True,
        "log_channel_id": 0,
        "audit_window_seconds": 5,
        "log_embed": {
            "enabled": True,
            "color": "33CC99",
            "title_channel_update": "Atualização de canal",
            "title_channel_create": "Criação de canal",
            "title_channel_delete": "Exclusão de canal"
        },
        "options": {
            "log_update": True,
            "log_create": False,
            "log_delete": False
        },
        "messages": {
            "channel_update": "{executor} modificou canal {channel} alterações: {changes}",
            "channel_update_unknown": "Canal {channel} modificado (executor desconhecido) alterações: {changes}",
            "channel_create": "{executor} criou canal {channel}",
            "channel_create_unknown": "Canal {channel} criado (executor desconhecido)",
            "channel_delete": "{executor} excluiu canal {channel}",
            "channel_delete_unknown": "Canal {channel} excluído (executor desconhecido)",
            "status_header": "Auditoria canais",
            "status_main": "Habilitado: {enabled} | Janela: {window}s",
            "status_channel": "Canal log: {channel}",
            "status_opts": "Update: {update} | Create: {create} | Delete: {delete}"
        },
        "debug": False
    }
}

class AuditChannelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('audit_channel', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('audit_channel', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.window: int = self.cfg.get('audit_window_seconds', 5)
        self.embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.opts: Dict[str, Any] = self.cfg.get('options', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('audit_channel')
        self.__init__(self.bot)

    async def _log(self, guild: discord.Guild, title: str, lines: List[Tuple[str,str,bool]]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.embed_cfg.get('enabled', True):
            color_hex = self.embed_cfg.get('color', '33CC99')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('33CC99', 16)
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

    async def _find_executor(self, guild: discord.Guild, target: discord.abc.GuildChannel, action: discord.AuditLogAction) -> discord.User | None:
        if not guild.me.guild_permissions.view_audit_log:
            return None
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=10, action=action):
            if entry.target and getattr(entry.target, 'id', None) == target.id:
                created = entry.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=datetime.timezone.utc)
                if (now - created).total_seconds() <= self.window:
                    return entry.user
        return None

    def _diff_channel(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> List[str]:
        changes = []
        # Generic attributes
        if hasattr(before, 'name') and before.name != after.name:
            changes.append(f"nome: {before.name} -> {after.name}")
        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
            if before.topic != after.topic:
                changes.append('topic alterado')
            if before.nsfw != after.nsfw:
                changes.append(f"nsfw: {before.nsfw} -> {after.nsfw}")
            if before.slowmode_delay != after.slowmode_delay:
                changes.append(f"slowmode: {before.slowmode_delay} -> {after.slowmode_delay}")
        if isinstance(before, discord.VoiceChannel) and isinstance(after, discord.VoiceChannel):
            if before.bitrate != after.bitrate:
                changes.append(f"bitrate: {before.bitrate} -> {after.bitrate}")
            if before.user_limit != after.user_limit:
                changes.append(f"user_limit: {before.user_limit} -> {after.user_limit}")
        # Overwrites count change (detailed diff seria maior)
        if len(before.overwrites) != len(after.overwrites):
            changes.append('permissões (overwrites) alteradas')
        return changes

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if not self.enabled or not self.opts.get('log_update', True):
            return
        changes = self._diff_channel(before, after)
        if not changes:
            return
        executor = await self._find_executor(after.guild, after, discord.AuditLogAction.channel_update)
        title = self.embed_cfg.get('title_channel_update', 'Atualização de canal')
        joined = ', '.join(changes)
        if executor:
            msg = self.msgs.get('channel_update', '{executor} modificou canal {channel} alterações: {changes}').format(executor=executor.mention, channel=after.mention if hasattr(after,'mention') else after.name, changes=joined)
        else:
            msg = self.msgs.get('channel_update_unknown', 'Canal {channel} modificado (executor desconhecido) alterações: {changes}').format(channel=after.mention if hasattr(after,'mention') else after.name, changes=joined)
        await self._log(after.guild, title, [('Evento', msg, False)])

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not self.enabled or not self.opts.get('log_create', False):
            return
        executor = await self._find_executor(channel.guild, channel, discord.AuditLogAction.channel_create)
        title = self.embed_cfg.get('title_channel_create', 'Criação de canal')
        if executor:
            msg = self.msgs.get('channel_create', '{executor} criou canal {channel}').format(executor=executor.mention, channel=channel.mention if hasattr(channel,'mention') else channel.name)
        else:
            msg = self.msgs.get('channel_create_unknown', 'Canal {channel} criado (executor desconhecido)').format(channel=channel.mention if hasattr(channel,'mention') else channel.name)
        await self._log(channel.guild, title, [('Evento', msg, False)])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not self.enabled or not self.opts.get('log_delete', False):
            return
        executor = await self._find_executor(channel.guild, channel, discord.AuditLogAction.channel_delete)
        title = self.embed_cfg.get('title_channel_delete', 'Exclusão de canal')
        if executor:
            msg = self.msgs.get('channel_delete', '{executor} excluiu canal {channel}').format(executor=executor.mention, channel=channel.name)
        else:
            msg = self.msgs.get('channel_delete_unknown', 'Canal {channel} excluído (executor desconhecido)').format(channel=channel.name)
        await self._log(channel.guild, title, [('Evento', msg, False)])

    @commands.command(name='channelauditreload')
    async def channel_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config audit_channel recarregada.')

    @commands.command(name='channelauditstatus')
    async def channel_status(self, ctx: commands.Context):
        m = self.msgs
        ch = ctx.guild.get_channel(self.log_channel_id)
        lines = [m.get('status_header', 'Auditoria canais')]
        lines.append(m.get('status_main', '').format(enabled=self.enabled, window=self.window))
        lines.append(m.get('status_channel', '').format(channel=ch.mention if ch else '(não definido)'))
        lines.append(m.get('status_opts', '').format(update=self.opts.get('log_update', True), create=self.opts.get('log_create', False), delete=self.opts.get('log_delete', False)))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(AuditChannelCog(bot))
