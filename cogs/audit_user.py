import datetime
import discord
from discord.ext import commands
from typing import Dict, Any, List, Tuple
from config_loader import config_manager

DEFAULTS = {
    "audit_user": {
        "enabled": True,
        "log_channel_id": 0,
        "audit_window_seconds": 5,
        "log_embed": {
            "enabled": True,
            "color": "0077AA",
            "title_message_delete": "Mensagem apagada",
            "title_message_bulk_delete": "Mensagens apagadas em massa",
            "title_voice_join": "Entrou na call",
            "title_voice_leave": "Saiu da call",
            "title_voice_move": "Moveu de call"
        },
        "options": {
            "log_message_delete": True,
            "log_bulk_delete": True,
            "log_voice": True,
            "include_content": True,
            "truncate_content": 300,
            "avoid_duplicate_with_movecall": True
        },
        "messages": {
            "message_delete_other": "{executor} apagou mensagem de {author}",
            "message_delete_self": "{author} apagou a própria mensagem",
            "message_delete_unknown": "Mensagem de {author} apagada (executor desconhecido)",
            "message_bulk_delete": "{executor} apagou {count} mensagens em {channel}",
            "voice_join_self": "{user} entrou em {channel}",
            "voice_leave_self": "{user} saiu de {channel}",
            "voice_move_self": "{user} moveu-se de {from} para {to}",
            "voice_move_other": "{executor} moveu {user} de {from} para {to}",
            "status_header": "Auditoria usuário",
            "status_main": "Habilitado: {enabled} | Janela audit: {window}s",
            "status_opts": "Msg delete: {msgdel} | Bulk: {bulk} | Voz: {voice}"
            ,"status_dup": "Evitar duplicatas movecall: {avoid_dup}"
        },
        "debug": False
    }
}

class AuditUserCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('audit_user', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('audit_user', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.window: int = self.cfg.get('audit_window_seconds', 5)
        self.embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.opts: Dict[str, Any] = self.cfg.get('options', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('audit_user')
        self.__init__(self.bot)

    async def _log(self, guild: discord.Guild, title: str, fields: List[Tuple[str,str,bool]]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.embed_cfg.get('enabled', True):
            color_hex = self.embed_cfg.get('color', '0077AA')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('0077AA', 16)
            embed = discord.Embed(title=title, color=color_val, timestamp=datetime.datetime.utcnow())
            for n,v,i in fields:
                embed.add_field(name=n, value=v, inline=i)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
        else:
            try:
                txt = title + '\n' + '\n'.join(f"{n}: {v}" for n,v,_ in fields)
                await ch.send(txt)
            except Exception:
                pass

    async def _find_executor_message_delete(self, guild: discord.Guild, author: discord.User | discord.Member, channel: discord.TextChannel) -> discord.User | None:
        if not guild.me.guild_permissions.view_audit_log:
            return None
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.message_delete):
            if entry.target and getattr(entry.target, 'id', None) == author.id:
                created = entry.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=datetime.timezone.utc)
                if (now - created).total_seconds() <= self.window:
                    extra = getattr(entry, 'extra', None)
                    # Optionally match channel if available
                    if extra and getattr(extra, 'channel', None) and extra.channel.id != channel.id:
                        continue
                    return entry.user
        return None

    async def _find_executor_voice(self, guild: discord.Guild, member: discord.Member, action: discord.AuditLogAction) -> discord.User | None:
        if not guild.me.guild_permissions.view_audit_log:
            return None
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=6, action=action):
            if entry.target and getattr(entry.target, 'id', None) == member.id:
                created = entry.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=datetime.timezone.utc)
                if (now - created).total_seconds() <= self.window:
                    return entry.user
        return None

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not self.enabled or not self.opts.get('log_message_delete', True):
            return
        if not message.guild or not message.author:
            return
        guild = message.guild
        author = message.author
        channel = message.channel if isinstance(message.channel, discord.TextChannel) else None
        if channel is None:
            return
        executor = await self._find_executor_message_delete(guild, author, channel)
        title = self.embed_cfg.get('title_message_delete', 'Mensagem apagada')
        content_field = ''
        if self.opts.get('include_content', True):
            raw = message.content or '(sem texto)'
            limit = int(self.opts.get('truncate_content', 300))
            if len(raw) > limit:
                raw = raw[:limit] + '…'
            content_field = raw.replace('`', '\u200b`')
        if executor:
            if executor.id == author.id:
                msg_line = self.msgs.get('message_delete_self', '{author} apagou a própria mensagem').format(author=author.mention)
            else:
                msg_line = self.msgs.get('message_delete_other', '{executor} apagou mensagem de {author}').format(executor=executor.mention, author=author.mention)
        else:
            msg_line = self.msgs.get('message_delete_unknown', 'Mensagem de {author} apagada (executor desconhecido)').format(author=author.mention)
        fields = [
            ('Evento', msg_line, False),
            ('Canal', channel.mention, True),
            ('Autor', author.mention, True)
        ]
        if content_field:
            fields.append(('Conteúdo', content_field, False))
        await self._log(guild, title, fields)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: List[discord.Message]):
        if not self.enabled or not self.opts.get('log_bulk_delete', True):
            return
        if not messages:
            return
        guild = messages[0].guild
        if guild is None:
            return
        channel = messages[0].channel if isinstance(messages[0].channel, discord.TextChannel) else None
        if channel is None:
            return
        # Bulk delete audit log entry target may not match; we take first author as sample
        sample_author = messages[0].author
        executor = await self._find_executor_message_delete(guild, sample_author, channel)
        count = len(messages)
        title = self.embed_cfg.get('title_message_bulk_delete', 'Mensagens apagadas em massa')
        if executor:
            msg_line = self.msgs.get('message_bulk_delete', '{executor} apagou {count} mensagens em {channel}').format(executor=executor.mention, count=count, channel=channel.mention)
        else:
            msg_line = f"{count} mensagens apagadas em {channel.mention} (executor desconhecido)"
        await self._log(guild, title, [('Evento', msg_line, False)])

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not self.enabled or not self.opts.get('log_voice', True):
            return
        movecall = self.bot.get_cog('AuditMoveCallCog')
        before_ch = before.channel
        after_ch = after.channel
        if before_ch == after_ch:
            return
        # Evita duplicar eventos de move/disconnect se audit_movecall ativo
        if self.opts.get('avoid_duplicate_with_movecall', True) and movecall and getattr(movecall, 'enabled', False):
            # Movimentos (before!=after ambos não None) e disconnect (after None) já serão logados por audit_movecall
            if (before_ch and after_ch and before_ch != after_ch) or (after_ch is None and before_ch is not None):
                return
        # Join
        if before_ch is None and after_ch is not None:
            title = self.embed_cfg.get('title_voice_join', 'Entrou na call')
            msg_line = self.msgs.get('voice_join_self', '{user} entrou em {channel}').format(user=member.mention, channel=after_ch.mention)
            await self._log(member.guild, title, [('Evento', msg_line, False)])
            return
        # Leave
        if after_ch is None and before_ch is not None:
            title = self.embed_cfg.get('title_voice_leave', 'Saiu da call')
            msg_line = self.msgs.get('voice_leave_self', '{user} saiu de {channel}').format(user=member.mention, channel=before_ch.mention)
            await self._log(member.guild, title, [('Evento', msg_line, False)])
            return
        # Move
        if before_ch and after_ch and before_ch != after_ch:
            executor = await self._find_executor_voice(member.guild, member, discord.AuditLogAction.member_move)
            title = self.embed_cfg.get('title_voice_move', 'Moveu de call')
            if executor and executor.id != member.id:
                template = self.msgs.get('voice_move_other', '{executor} moveu {user} de {from} para {to}')
                msg_line = template.format(**{
                    'executor': executor.mention,
                    'user': member.mention,
                    'from': before_ch.mention,
                    'to': after_ch.mention
                })
            else:
                template = self.msgs.get('voice_move_self', '{user} moveu-se de {from} para {to}')
                msg_line = template.format(**{
                    'user': member.mention,
                    'from': before_ch.mention,
                    'to': after_ch.mention
                })
            await self._log(member.guild, title, [('Evento', msg_line, False)])

    @commands.command(name='userauditreload')
    async def user_audit_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config audit_user recarregada.')

    @commands.command(name='userauditstatus')
    async def user_audit_status(self, ctx: commands.Context):
        m = self.msgs
        ch = ctx.guild.get_channel(self.log_channel_id)
        lines = [m.get('status_header', 'Auditoria usuário')]
        lines.append(m.get('status_main', '').format(enabled=self.enabled, window=self.window))
        lines.append(m.get('status_opts', '').format(msgdel=self.opts.get('log_message_delete', True), bulk=self.opts.get('log_bulk_delete', True), voice=self.opts.get('log_voice', True)))
        lines.append(m.get('status_dup', '').format(avoid_dup=self.opts.get('avoid_duplicate_with_movecall', True)))
        lines.append(f"Canal log: {ch.mention if ch else '(não definido)'}")
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(AuditUserCog(bot))
