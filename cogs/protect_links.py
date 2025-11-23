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
        "use_embed": True,
        "embed_color": "D9534F",
        "feedback": {
            "notify_user": True,
            "delete_delay": 5,
            "dm_user": False
        },
        "messages": {
            "deleted": "{user} sua mensagem foi removida: {reason}",
            "embed_title": "Proteção de Links",
            "embed_field_user": "Usuário",
            "embed_field_channel": "Canal",
            "embed_field_reason": "Motivo",
            "embed_field_excerpt": "Trecho",
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
            if self.cfg.get('use_embed', True):
                try:
                    emb = self._build_feedback_embed(message, reason)
                    sent = await message.channel.send(embed=emb)
                    if delete_delay > 0:
                        await sent.delete(delay=delete_delay)
                except Exception:
                    pass
            else:
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
                    if self.cfg.get('use_embed', True):
                        emb = self._build_log_embed(message, reason)
                        await ch.send(embed=emb)
                    else:
                        await ch.send(f"Removido link de {message.author} em {message.channel.mention}: {reason}\nConteúdo: {self._sanitize_links_for_plain(message.content[:1900])}")
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

# ---------------- EMBED HELPERS ----------------
def _sanitize_for_embed(text: str) -> str:
    # Evita tornar links clicáveis: substitui . por [.] e injeta zero-width após ://
    # Também corta para tamanho seguro
    if not text:
        return '(vazio)'
    sanitized = re.sub(r'https?://', lambda m: m.group(0) + '\u200b', text)
    sanitized = sanitized.replace('.', '[.]')
    return sanitized[:500]

def _excerpt(msg: discord.Message) -> str:
    return _sanitize_for_embed(msg.content)

def _color_from_hex(hex_str: str) -> int:
    try:
        return int(hex_str, 16)
    except Exception:
        return int('D9534F', 16)

def _build_base_embed(cfg: Dict[str, Any], msgs: Dict[str, str], color_hex: str, title_key: str) -> discord.Embed:
    title = msgs.get('embed_title', 'Proteção')
    color = _color_from_hex(color_hex)
    emb = discord.Embed(title=title, color=color)
    return emb

def _field_name(msgs: Dict[str,str], key: str, fallback: str) -> str:
    return msgs.get(key, fallback)

def _format_reason(reason: str) -> str:
    return reason.replace('`', '\u200b`')

def ProtectLinksCog__build_feedback_embed(self: ProtectLinksCog, message: discord.Message, reason: str) -> discord.Embed:
    emb = _build_base_embed(self.cfg, self.msgs, self.cfg.get('embed_color', 'D9534F'), 'embed_title')
    user_field = f"{message.author.mention} | {message.author.id}"
    emb.add_field(name=_field_name(self.msgs,'embed_field_user','Usuário'), value=user_field, inline=True)
    emb.add_field(name=_field_name(self.msgs,'embed_field_channel','Canal'), value=message.channel.mention, inline=True)
    emb.add_field(name=_field_name(self.msgs,'embed_field_reason','Motivo'), value=_format_reason(reason), inline=False)
    emb.add_field(name=_field_name(self.msgs,'embed_field_excerpt','Trecho'), value=f"```{_excerpt(message)}```", inline=False)
    return emb

def ProtectLinksCog__build_log_embed(self: ProtectLinksCog, message: discord.Message, reason: str) -> discord.Embed:
    emb = ProtectLinksCog__build_feedback_embed(self, message, reason)
    return emb

# Monkey patch methods into class (keeps single file, minimal diff in existing code flow)
ProtectLinksCog._build_feedback_embed = ProtectLinksCog__build_feedback_embed
ProtectLinksCog._build_log_embed = ProtectLinksCog__build_log_embed
ProtectLinksCog._sanitize_links_for_plain = lambda self, text: _sanitize_for_embed(text)
