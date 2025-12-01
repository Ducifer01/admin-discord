import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "entret_mentions": {
        "enabled": True,
        "ignore_channels": [],
        "bypass_roles": [],
        "targets": {
            "users": [
                {
                    "id": 0,
                    "react_emojis": ["👋"],
                    "reply": "Olá {author}, você mencionou {user}!",
                    "cooldown_seconds": 5
                }
            ],
            "roles": [
                {
                    "id": 0,
                    "react_emojis": ["✅"],
                    "reply": "Você mencionou o cargo {role}.",
                    "cooldown_seconds": 5
                }
            ],
            "bot": {
                "enabled": True,
                "react_emojis": ["👀"],
                "reply": "Oi! Eu sou o {bot}. Precisa de algo, {author}?",
                "cooldown_seconds": 10
            }
        },
        "messages": {
            "replied": "Mensagem enviada.",
            "reacted": "Reações adicionadas."
        },
        "debug": False
    }
}

EMOJI_ID_PATTERN = re.compile(r"^<a?:[A-Za-z0-9_]+:(\d+)>$")

class EntretMentionsCog(commands.Cog):
    """Reage a menções configuradas: usuários, cargos e menção ao próprio bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('entret_mentions', defaults=DEFAULTS)
        self.cfg: Dict[str, Any] = self.raw_cfg.get('entret_mentions', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.ignore_channels: List[int] = self.cfg.get('ignore_channels', [])
        self.bypass_roles: List[int] = self.cfg.get('bypass_roles', [])
        self.targets: Dict[str, Any] = self.cfg.get('targets', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug: bool = self.cfg.get('debug', False)
        # cooldowns: chave (tipo,user/role/bot,id) -> timestamp
        self._cooldowns: Dict[Tuple[str, int], float] = {}

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('entret_mentions')
        self.__init__(self.bot)

    def _cooldown_ok(self, key: Tuple[str, int], seconds: int) -> bool:
        if seconds <= 0:
            return True
        now = time.time()
        last = self._cooldowns.get(key, 0.0)
        if now - last >= seconds:
            self._cooldowns[key] = now
            return True
        return False

    def _allowed_mentions(self) -> discord.AllowedMentions:
        # Evita pings acidentais nos replies
        return discord.AllowedMentions.none()

    def _format(self, template: Optional[str], **kwargs) -> Optional[str]:
        if not template:
            return None
        try:
            return template.format(**kwargs)
        except Exception:
            return template

    def _resolve_emoji(self, guild: discord.Guild, token: str) -> Optional[discord.PartialEmoji | str]:
        if not token:
            return None
        m = EMOJI_ID_PATTERN.match(token)
        if m:
            try:
                eid = int(m.group(1))
                e = discord.utils.get(guild.emojis, id=eid)
                return e
            except Exception:
                return None
        # id numérico puro
        if token.isdigit():
            try:
                eid = int(token)
                e = discord.utils.get(guild.emojis, id=eid)
                return e
            except Exception:
                return None
        # assume unicode
        return token

    async def _do_actions(self, message: discord.Message, react_emojis: List[str] | None, reply: Optional[str]):
        # Reagir
        if react_emojis:
            for tok in react_emojis:
                emoji = self._resolve_emoji(message.guild, tok) if message.guild else tok
                if emoji is None:
                    continue
                try:
                    await message.add_reaction(emoji)
                except Exception:
                    continue
        # Responder
        if reply:
            try:
                await message.channel.send(reply, allowed_mentions=self._allowed_mentions())
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not self.enabled:
            return
        if message.channel.id in self.ignore_channels:
            return
        if self.bypass_roles and isinstance(message.author, discord.Member):
            if any(r.id in self.bypass_roles for r in message.author.roles):
                return

        # Preparar contexto
        bot_member = message.guild.me
        mentions_users = list(message.mentions) if message.mentions else []
        mentions_roles = list(message.role_mentions) if message.role_mentions else []

        # Alvos: usuários específicos
        for user_rule in self.targets.get('users', []) or []:
            uid = int(user_rule.get('id', 0))
            if uid <= 0:
                continue
            if any(u.id == uid for u in mentions_users):
                cooldown = int(user_rule.get('cooldown_seconds', 0))
                key = ('user', uid)
                if not self._cooldown_ok(key, cooldown):
                    continue
                u = message.guild.get_member(uid) or discord.Object(id=uid)
                ctx_vars = {
                    'author': message.author.mention,
                    'user': getattr(u, 'mention', f'<@{uid}>'),
                    'channel': message.channel.mention,
                    'bot': bot_member.mention if bot_member else 'bot'
                }
                reply = self._format(user_rule.get('reply'), **ctx_vars)
                await self._do_actions(message, user_rule.get('react_emojis'), reply)

        # Alvos: cargos específicos
        for role_rule in self.targets.get('roles', []) or []:
            rid = int(role_rule.get('id', 0))
            if rid <= 0:
                continue
            if any(r.id == rid for r in mentions_roles):
                cooldown = int(role_rule.get('cooldown_seconds', 0))
                key = ('role', rid)
                if not self._cooldown_ok(key, cooldown):
                    continue
                role = message.guild.get_role(rid)
                ctx_vars = {
                    'author': message.author.mention,
                    'role': role.mention if role else f'<@&{rid}>',
                    'channel': message.channel.mention,
                    'bot': bot_member.mention if bot_member else 'bot'
                }
                reply = self._format(role_rule.get('reply'), **ctx_vars)
                await self._do_actions(message, role_rule.get('react_emojis'), reply)

        # Alvo: menção ao bot
        bot_rule = (self.targets.get('bot') or {}) if isinstance(self.targets.get('bot'), dict) else {}
        if bot_rule.get('enabled', True) and bot_member:
            if any(u.id == bot_member.id for u in mentions_users):
                cooldown = int(bot_rule.get('cooldown_seconds', 0))
                key = ('bot', bot_member.id)
                if self._cooldown_ok(key, cooldown):
                    ctx_vars = {
                        'author': message.author.mention,
                        'bot': bot_member.mention,
                        'channel': message.channel.mention
                    }
                    reply = self._format(bot_rule.get('reply'), **ctx_vars)
                    await self._do_actions(message, bot_rule.get('react_emojis'), reply)

    @commands.command(name='entretmentionsreload')
    async def entret_mentions_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config entret_mentions recarregada.')

async def setup(bot: commands.Bot):
    await bot.add_cog(EntretMentionsCog(bot))
