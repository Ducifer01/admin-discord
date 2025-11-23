import discord
from discord.ext import commands
import datetime
import re
from typing import List, Dict, Any
from config_loader import config_manager

DEFAULTS = {
    "automod_chat": {
        "enabled": True,
        "debug": False,
        "action": "delete_warn",  # delete | delete_warn | delete_punish
        "forbidden_words": ["palavra1", "palavra2", "ofensa"],
        "case_sensitive": False,
        "match_whole_words": True,
        "warn": {
            "message": "{user} sua mensagem foi removida: uso de palavra proibida.",
            "delete_delay": 6,
            "dm_user": False
        },
        "punishment": {
            "type": "timeout",  # timeout (padrão)
            "duration_seconds": 600,
            "reason": "Uso de palavras proibidas",
            "notify": True
        },
        "exempt": {
            "roles": [],  # IDs de cargos isentos
            "users": [],  # IDs de usuários isentos
            "manage_messages_bypass": True
        },
        "log_channel_id": None
    }
}

class AutoModChat(commands.Cog):
    """Automod de chat baseado em lista de palavras proibidas."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('automod_chat', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('automod_chat', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.debug: bool = self.cfg.get('debug', False)
        self.action: str = self.cfg.get('action', 'delete_warn')
        self.case_sensitive: bool = self.cfg.get('case_sensitive', False)
        self.match_whole: bool = self.cfg.get('match_whole_words', True)
        self.forbidden_words: List[str] = self.cfg.get('forbidden_words', [])
        self.warn_cfg: Dict[str, Any] = self.cfg.get('warn', {})
        self.punishment_cfg: Dict[str, Any] = self.cfg.get('punishment', {})
        self.exempt_cfg: Dict[str, Any] = self.cfg.get('exempt', {})
        self.log_channel_id = self.cfg.get('log_channel_id')
        self._compiled_patterns: List[re.Pattern] = []
        self._compile_patterns()

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('automod_chat')
        self.cfg = self.raw_cfg.get('automod_chat', {})
        self.enabled = self.cfg.get('enabled', True)
        self.debug = self.cfg.get('debug', False)
        self.action = self.cfg.get('action', 'delete_warn')
        self.case_sensitive = self.cfg.get('case_sensitive', False)
        self.match_whole = self.cfg.get('match_whole_words', True)
        self.forbidden_words = self.cfg.get('forbidden_words', [])
        self.warn_cfg = self.cfg.get('warn', {})
        self.punishment_cfg = self.cfg.get('punishment', {})
        self.exempt_cfg = self.cfg.get('exempt', {})
        self.log_channel_id = self.cfg.get('log_channel_id')
        self._compile_patterns()

    def _compile_patterns(self):
        flags = 0 if self.case_sensitive else re.IGNORECASE
        self._compiled_patterns.clear()
        for w in self.forbidden_words:
            w = w.strip()
            if not w:
                continue
            if self.match_whole:
                pattern = re.compile(rf"\b{re.escape(w)}\b", flags)
            else:
                pattern = re.compile(re.escape(w), flags)
            self._compiled_patterns.append(pattern)

    def _exempt(self, member: discord.Member) -> bool:
        if not member:
            return False
        # Por permissão
        if self.exempt_cfg.get('manage_messages_bypass', True) and member.guild_permissions.manage_messages:
            return True
        # Por role
        role_ids: List[int] = self.exempt_cfg.get('roles', [])
        if role_ids and any(r.id in role_ids for r in getattr(member, 'roles', [])):
            return True
        # Por usuário
        user_ids: List[int] = self.exempt_cfg.get('users', [])
        if member.id in user_ids:
            return True
        return False

    def _contains_forbidden(self, content: str) -> bool:
        if not content:
            return False
        for pat in self._compiled_patterns:
            if pat.search(content):
                return True
        return False

    async def _apply_punishment(self, member: discord.Member, reason: str):
        p_type = self.punishment_cfg.get('type', 'timeout')
        if p_type == 'timeout':
            duration = int(self.punishment_cfg.get('duration_seconds', 600))
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
            try:
                await member.timeout(until, reason=reason)
            except Exception:
                if self.debug:
                    print('[automod_chat] Falha ao aplicar timeout')
        # Futuro: adicionar outras punições (mutechat, ban, etc.)

    async def _log(self, message: discord.Message, matched: bool, reason: str):
        if not self.log_channel_id:
            return
        channel = message.guild.get_channel(self.log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(f"[automod_chat] {'BLOQUEADA' if matched else 'PASSOU'} mensagem de {message.author} em {message.channel.mention}: {reason}")
        except Exception:
            pass

    async def _handle_violation(self, message: discord.Message, reason: str):
        action = self.action
        member = message.author
        # Deleta mensagem
        try:
            await message.delete()
        except Exception:
            return
        # Aviso
        if action in ('delete_warn', 'delete_punish'):
            warn_msg = self.warn_cfg.get('message', '{user} mensagem removida.')
            delete_delay = int(self.warn_cfg.get('delete_delay', 6))
            text = warn_msg.format(user=member.mention, reason=reason)
            try:
                sent = await message.channel.send(text)
                if delete_delay > 0:
                    await sent.delete(delay=delete_delay)
            except Exception:
                pass
            if self.warn_cfg.get('dm_user'):
                try:
                    await member.send(f"Você usou palavra proibida em {message.channel.mention}: {reason}")
                except Exception:
                    pass
        # Punição
        if action == 'delete_punish':
            await self._apply_punishment(member, reason)
        await self._log(message, True, reason)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not self.enabled:
            return
        if self._exempt(message.author):
            return
        content = message.content or ''
        if not content:
            return
        matched = self._contains_forbidden(content)
        if not matched:
            if self.debug:
                await self._log(message, False, 'sem correspondência')
            return
        reason = 'Uso de palavra proibida.'
        await self._handle_violation(message, reason)

    @commands.command(name='automodchatreload')
    async def automodchat_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config automod_chat recarregada.')

    @commands.command(name='automodchatinfo')
    async def automodchat_info(self, ctx: commands.Context):
        fw = ', '.join(self.forbidden_words) or '(nenhuma)'
        await ctx.reply(f"AutomodChat ativo: {self.enabled}\nAção: {self.action}\nPalavras proibidas: {fw}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoModChat(bot))
