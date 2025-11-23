import re
import discord
from discord.ext import commands
from typing import List, Dict, Any
from urllib.parse import urlparse
import asyncio
from config_loader import config_manager

DEFAULTS = {
    "protect_links": {
        "enabled": True,
        "mode": "whitelist",  # whitelist | blacklist
        "domains_whitelist": [],
        "domains_blacklist": [],
        "regex_whitelist": [],
        "regex_blacklist": [],
        "delete_reason_whitelist_fail": "Link não permitido neste servidor.",
        "delete_reason_blacklist_hit": "Link bloqueado.",
        "bypass_roles": [],
        "ignore_channels": [],
        "log_channel_id": 0,
        "feedback": {
            "notify_user": True,
            "delete_delay": 5,
            "dm_user": False
        },
        "messages": {
            "deleted": "{user} sua mensagem foi removida: {reason}",
            "summary_header": "Config proteção de links:",
            "line_domain": "{type}: {domain}",
            "mode_info": "Modo atual: {mode}",
            "no_domains": "(lista vazia)"
        },
        "debug": False
    }
}

# Regex simples para detectar links (http(s):// ou domínio com ponto + path)
LINK_REGEX = re.compile(r"(https?://[\w.-]+(?:/[\w\-._~:/?#\[\]@!$&'()*+,;=%]*)?|(?:[\w.-]+\.[a-zA-Z]{2,})(?:/[\w\-._~:/?#\[\]@!$&'()*+,;=%]*)?)")

class ProtectLinksCog(commands.Cog):
    """Protege contra envio de links fora da política definida."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('protect_links', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('protect_links', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.mode: str = self.cfg.get('mode', 'whitelist').lower()
        self.dom_whitelist: List[str] = [d.lower() for d in self.cfg.get('domains_whitelist', [])]
        self.dom_blacklist: List[str] = [d.lower() for d in self.cfg.get('domains_blacklist', [])]
        self.regex_whitelist: List[str] = self.cfg.get('regex_whitelist', [])
        self.regex_blacklist: List[str] = self.cfg.get('regex_blacklist', [])
        self.comp_regex_whitelist = [re.compile(p, re.IGNORECASE) for p in self.regex_whitelist]
        self.comp_regex_blacklist = [re.compile(p, re.IGNORECASE) for p in self.regex_blacklist]
        self.bypass_roles: List[int] = self.cfg.get('bypass_roles', [])
        self.ignore_channels: List[int] = self.cfg.get('ignore_channels', [])
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.feedback_cfg: Dict[str, Any] = self.cfg.get('feedback', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('protect_links')
        self.cfg = self.raw_cfg.get('protect_links', {})
        self.__init__(self.bot)  # Reinitialize state cleanly

    def extract_domain(self, raw: str) -> str | None:
        # Adiciona esquema se ausente para urlparse funcionar
        if not raw.startswith(('http://', 'https://')):
            candidate = 'http://' + raw
        else:
            candidate = raw
        try:
            parsed = urlparse(candidate)
            host = parsed.hostname
            if host:
                return host.lower()
        except Exception:
            pass
        return None

    def domain_matches(self, domain: str, target_list: List[str]) -> bool:
        # Match se domínio termina com alvo (cobre subdomínios) Ex: cdn.youtube.com termina com youtube.com
        for t in target_list:
            if domain == t or domain.endswith('.' + t):
                return True
        return False

    def regex_matches(self, url: str, compiled_list: List[re.Pattern]) -> bool:
        return any(r.search(url) for r in compiled_list)

    async def delete_and_feedback(self, message: discord.Message, reason: str):
        notify = self.feedback_cfg.get('notify_user', True)
        delete_delay = self.feedback_cfg.get('delete_delay', 5)
        dm_user = self.feedback_cfg.get('dm_user', False)
        try:
            await message.delete()
        except Exception:
            return
        if notify:
            text = self.msgs.get('deleted', '{user} sua mensagem foi removida: {reason}').format(user=message.author.mention, reason=reason)
            try:
                sent = await message.channel.send(text)
                if delete_delay > 0:
                    await asyncio.sleep(delete_delay)
                    try:
                        await sent.delete()
                    except Exception:
                        pass
            except Exception:
                pass
        if dm_user:
            try:
                await message.author.send(f"Sua mensagem foi removida: {reason}")
            except Exception:
                pass
        if self.log_channel_id:
            ch = message.guild.get_channel(self.log_channel_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.send(f"Removido link de {message.author} em {message.channel.mention}: {reason}\nConteúdo: {message.content[:1900]}")
                except Exception:
                    pass

    def should_delete(self, url: str, domain: str) -> str | None:
        # Retorna razão se deve deletar, senão None
        if self.mode == 'whitelist':
            # Se domínio NÃO está na whitelist e nenhum regex whitelist liberou, deletar
            whitelisted = self.domain_matches(domain, self.dom_whitelist) or self.regex_matches(url, self.comp_regex_whitelist)
            if not whitelisted:
                return self.cfg.get('delete_reason_whitelist_fail', 'Link não permitido.')
        elif self.mode == 'blacklist':
            blacklisted = self.domain_matches(domain, self.dom_blacklist) or self.regex_matches(url, self.comp_regex_blacklist)
            if blacklisted:
                return self.cfg.get('delete_reason_blacklist_hit', 'Link bloqueado.')
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.enabled or message.author.bot or not message.guild:
            return
        if message.channel.id in self.ignore_channels:
            return
        if self.bypass_roles and any(r.id in self.bypass_roles for r in getattr(message.author, 'roles', [])):
            return
        if message.author.guild_permissions.manage_messages:
            return

        matched_any = False
        for match in LINK_REGEX.finditer(message.content):
            raw_link = match.group(0)
            domain = self.extract_domain(raw_link)
            if not domain:
                continue
            reason = self.should_delete(raw_link, domain)
            if reason:
                await self.delete_and_feedback(message, reason)
                return  # Mensagem deletada; encerra
            matched_any = True
        if self.debug and matched_any:
            try:
                await message.channel.send(f"[links debug] OK: {message.author.mention}", delete_after=3)
            except Exception:
                pass

    @commands.command(name='linksreload')
    async def links_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config proteção de links recarregada.')

    @commands.command(name='linkspolicy')
    async def links_policy(self, ctx: commands.Context):
        lines = [self.msgs.get('summary_header', 'Proteção de links')] 
        lines.append(self.msgs.get('mode_info', 'Modo: {mode}').format(mode=self.mode))
        if self.mode == 'whitelist':
            src = self.dom_whitelist
            label = 'Whitelist'
        else:
            src = self.dom_blacklist
            label = 'Blacklist'
        if src:
            for d in src:
                lines.append(self.msgs.get('line_domain', '{type}: {domain}').format(type=label, domain=d))
        else:
            lines.append(self.msgs.get('no_domains', '(vazia)'))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(ProtectLinksCog(bot))
