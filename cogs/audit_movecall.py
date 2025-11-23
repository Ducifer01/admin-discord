import datetime
import discord
from discord.ext import commands
from typing import Dict, Any, List, Tuple
from config_loader import config_manager

DEFAULTS = {
    "audit_movecall": {
        "enabled": True,
        "log_channel_id": 0,
        "audit_window_seconds": 5,
        "log_embed": {
            "enabled": True,
            "color": "3366FF",
            "title_move": "Movimento na call",
            "title_disconnect": "Desconexão da call"
        },
        "messages": {
            "user_self_move": "{target} moveu-se de {from} para {to}",
            "user_moved_other": "{executor} moveu {target} de {from} para {to}",
            "user_self_disconnect": "{target} saiu da call {from}",
            "user_disconnect_other": "{executor} desconectou {target} da call {from}",
            "status_header": "Auditoria movimentações call",
            "status_main": "Habilitado: {enabled} | Janela: {window}s",
            "status_channel": "Canal log: {channel}"
        },
        "debug": False
    }
}

class AuditMoveCallCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('audit_movecall', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('audit_movecall', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.window: int = self.cfg.get('audit_window_seconds', 5)
        self.embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.opts: Dict[str, Any] = self.cfg.get('options', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('audit_movecall')
        self.__init__(self.bot)

    async def _log(self, guild: discord.Guild, title: str, lines: List[Tuple[str,str,bool]]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.embed_cfg.get('enabled', True):
            color_hex = self.embed_cfg.get('color', '3366FF')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('3366FF', 16)
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

    async def _find_executor(self, guild: discord.Guild, target: discord.Member, action: discord.AuditLogAction) -> discord.User | None:
        if not guild.me.guild_permissions.view_audit_log:
            return None
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=6, action=action):
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
        # Movimentação
        before_ch = before.channel
        after_ch = after.channel
        if before_ch != after_ch:
            audit_user = self.bot.get_cog('AuditUserCog')
            # Se evitar duplicatas inversas estiver ativo e audit_user também loga voz (sem evitar duplicatas com movecall), movecall pula
            if self.opts.get('avoid_duplicate_with_useraudit', True) and audit_user and getattr(audit_user, 'enabled', False):
                au_opts = getattr(audit_user, 'opts', {})
                # audit_user loga moves/disconnect se log_voice True e avoid_duplicate_with_movecall False
                if au_opts.get('log_voice', True) and not au_opts.get('avoid_duplicate_with_movecall', True):
                    # Se é desconexão ou movimento (before e after distintos e after não None / after None) então pulamos aqui
                    if after_ch is None or (before_ch is not None and after_ch is not None):
                        return
            if after_ch is None:
                # Desconectou
                executor = await self._find_executor(member.guild, member, discord.AuditLogAction.member_disconnect)
                if executor:
                    template = self.msgs.get('user_disconnect_other', '{executor} desconectou {target} da call {from}')
                    msg = template.format(**{
                        'executor': executor.mention,
                        'target': member.mention,
                        'from': before_ch.mention if before_ch else '(nenhuma)'
                    })
                    title = self.embed_cfg.get('title_disconnect', 'Desconexão da call')
                else:
                    template = self.msgs.get('user_self_disconnect', '{target} saiu da call {from}')
                    msg = template.format(**{
                        'target': member.mention,
                        'from': before_ch.mention if before_ch else '(nenhuma)'
                    })
                    title = self.embed_cfg.get('title_disconnect', 'Desconexão da call')
                await self._log(member.guild, title, [('Evento', msg, False)])
            elif before_ch is None:
                # Entrou - ignorar; foco em mover/desconectar
                return
            else:
                # Moveu
                executor = await self._find_executor(member.guild, member, discord.AuditLogAction.member_move)
                if executor:
                    template = self.msgs.get('user_moved_other', '{executor} moveu {target} de {from} para {to}')
                    msg = template.format(**{
                        'executor': executor.mention,
                        'target': member.mention,
                        'from': before_ch.mention,
                        'to': after_ch.mention
                    })
                else:
                    template = self.msgs.get('user_self_move', '{target} moveu-se de {from} para {to}')
                    msg = template.format(**{
                        'target': member.mention,
                        'from': before_ch.mention,
                        'to': after_ch.mention
                    })
                title = self.embed_cfg.get('title_move', 'Movimento na call')
                await self._log(member.guild, title, [('Evento', msg, False)])

    @commands.command(name='movecallauditreload')
    async def movecall_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config audit_movecall recarregada.')

    @commands.command(name='movecallauditstatus')
    async def movecall_status(self, ctx: commands.Context):
        m = self.msgs
        ch = ctx.guild.get_channel(self.log_channel_id)
        lines = [m.get('status_header', 'Auditoria movimentações call')]
        lines.append(m.get('status_main', '').format(enabled=self.enabled, window=self.window))
        lines.append(m.get('status_channel', '').format(channel=ch.mention if ch else '(não definido)'))
        lines.append(m.get('status_dup', '').format(avoid_dup=self.opts.get('avoid_duplicate_with_useraudit', True)))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(AuditMoveCallCog(bot))
