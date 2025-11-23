import discord
from discord.ext import commands
from typing import Dict, Any, List
import asyncio

from config_loader import config_manager

DEFAULTS = {
    "protect_files": {
        "enabled": True,
        "allow_images": True,
        "allow_videos": True,
        "allow_gifs": True,
        "allowed_extensions": [],
        "blocked_extensions": [
            "exe", "bat", "cmd", "msi", "js", "jar", "scr", "com", "ps1", "vbs", "lnk", "reg", "dll", "sys",
            "zip", "rar", "7z", "gz", "tar", "iso"
        ],
        "bypass_roles": [],
        "ignore_channels": [],
        "log_channel_id": 0,
        "log_embed": {
            "enabled": True,
            "color": "FFAA00",
            "title_blocked": "Anexo bloqueado",
            "title_removed": "Mensagem removida (arquivo não permitido)"
        },
        "feedback": {
            "notify_user": True,
            "delete_delay": 5,
            "dm_user": False
        },
        "messages": {
            "deleted": "{user} sua mensagem foi removida: {reason}",
            "reason_blocked_ext": "Extensão bloqueada: {ext}",
            "reason_not_allowed": "Tipo de arquivo não permitido neste canal.",
            "summary_header": "Proteção de arquivos",
            "summary_flags": "Imgs:{allow_images} Vídeos:{allow_videos} GIFs:{allow_gifs}",
            "line_allowed": "Permitido: .{ext}",
            "line_blocked": "Bloqueado: .{ext}",
            "no_items": "(lista vazia)"
        },
        "debug": False
    }
}

class ProtectFilesCog(commands.Cog):
    """Bloqueia anexos com extensões suspeitas; por padrão permite imagens, vídeos e GIFs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('protect_files', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('protect_files', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.allow_images: bool = self.cfg.get('allow_images', True)
        self.allow_videos: bool = self.cfg.get('allow_videos', True)
        self.allow_gifs: bool = self.cfg.get('allow_gifs', True)
        self.allowed_ext: List[str] = [e.lower().lstrip('.') for e in self.cfg.get('allowed_extensions', [])]
        self.blocked_ext: List[str] = [e.lower().lstrip('.') for e in self.cfg.get('blocked_extensions', [])]
        self.bypass_roles: List[int] = self.cfg.get('bypass_roles', [])
        self.ignore_channels: List[int] = self.cfg.get('ignore_channels', [])
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.log_embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.feedback_cfg: Dict[str, Any] = self.cfg.get('feedback', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('protect_files')
        self.__init__(self.bot)

    def _ext_from_name(self, filename: str) -> str:
        if not filename or '.' not in filename:
            return ''
        return filename.rsplit('.', 1)[1].lower()

    def _is_allowed_attachment(self, att: discord.Attachment) -> tuple[bool, str]:
        ctype = (att.content_type or '').lower()
        ext = self._ext_from_name(att.filename)
        # GIF pode ter content-type image/gif; trate separado
        if ctype == 'image/gif' or ext == 'gif':
            if self.allow_gifs:
                return True, ''
            # caso allow_images esteja true e gifs não, ainda bloqueia
            return False, self.msgs.get('reason_not_allowed', 'GIF não permitido.')
        if ctype.startswith('image/'):
            return (self.allow_images, '' if self.allow_images else self.msgs.get('reason_not_allowed', 'Imagem não permitida.'))
        if ctype.startswith('video/'):
            return (self.allow_videos, '' if self.allow_videos else self.msgs.get('reason_not_allowed', 'Vídeo não permitido.'))
        # Se extensão explícita bloqueada, bloqueia
        if ext in self.blocked_ext:
            return False, self.msgs.get('reason_blocked_ext', 'Extensão bloqueada: {ext}').format(ext=ext)
        # Se extensão whitelisted, permite
        if ext in self.allowed_ext:
            return True, ''
        # Por padrão: bloquear outros tipos
        return False, self.msgs.get('reason_not_allowed', 'Tipo de arquivo não permitido neste canal.')

    async def _log(self, guild: discord.Guild, *, title: str, fields: List[tuple]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.log_embed_cfg.get('enabled', True):
            color_hex = self.log_embed_cfg.get('color', 'FFAA00')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('FFAA00', 16)
            embed = discord.Embed(title=title, color=color_val)
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
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

    async def _delete_with_feedback(self, message: discord.Message, reason: str):
        notify = self.feedback_cfg.get('notify_user', True)
        delete_delay = self.feedback_cfg.get('delete_delay', 5)
        dm_user = self.feedback_cfg.get('dm_user', False)
        try:
            await message.delete()
        except Exception:
            return
        if notify:
            try:
                sent = await message.channel.send(self.msgs.get('deleted', '{user} sua mensagem foi removida: {reason}').format(user=message.author.mention, reason=reason))
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.enabled or message.author.bot or not message.guild:
            return
        if message.channel.id in self.ignore_channels:
            return
        if self.bypass_roles and any(r.id in self.bypass_roles for r in getattr(message.author, 'roles', [])):
            return
        if not message.attachments:
            return
        # Avalia todos os anexos
        reasons = []
        for att in message.attachments:
            ok, reason = self._is_allowed_attachment(att)
            if not ok:
                reasons.append((att.filename, reason))
        if reasons:
            # Log e remoção
            detail = '\n'.join([f"• {name} — {rsn}" for name, rsn in reasons])
            await self._log(message.guild, title=self.log_embed_cfg.get('title_removed', 'Mensagem removida (arquivo não permitido)'), fields=[
                ('Autor', message.author.mention, True),
                ('Canal', message.channel.mention, True),
                ('Detalhes', detail[:1024], False)
            ])
            await self._delete_with_feedback(message, reasons[0][1])
        elif self.debug:
            try:
                await message.channel.send('[files debug] OK', delete_after=3)
            except Exception:
                pass

    @commands.command(name='filesreload')
    async def files_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config proteção de arquivos recarregada.')

    @commands.command(name='filespolicy')
    async def files_policy(self, ctx: commands.Context):
        lines = [self.msgs.get('summary_header', 'Proteção de arquivos')]
        lines.append(self.msgs.get('summary_flags', '').format(allow_images=self.allow_images, allow_videos=self.allow_videos, allow_gifs=self.allow_gifs))
        allowed = self.allowed_ext
        blocked = self.blocked_ext
        lines.append('Permitidos:')
        if allowed:
            for e in allowed:
                lines.append(self.msgs.get('line_allowed', 'Permitido: .{ext}').format(ext=e))
        else:
            lines.append(self.msgs.get('no_items', '(lista vazia)'))
        lines.append('Bloqueados:')
        if blocked:
            for e in blocked:
                lines.append(self.msgs.get('line_blocked', 'Bloqueado: .{ext}').format(ext=e))
        else:
            lines.append(self.msgs.get('no_items', '(lista vazia)'))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(ProtectFilesCog(bot))
