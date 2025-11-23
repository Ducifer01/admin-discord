import discord
from discord.ext import commands
from typing import Dict, Any, List
import asyncio
from config_loader import config_manager

DEFAULTS = {
    "nomsg": {
        "rules": [],
        "debug": False,
        "feedback": {
            "notify_user": True,
            "delete_delay": 5,
            "dm_user": False
        },
        "messages": {
            "deleted": "{user} sua mensagem foi removida: {reason}",
            "rule_summary_header": "Regras ativas (canal -> restrições)",
            "rule_line": "<#{channel_id}> texto:{allow_text} imagens:{allow_images} vídeos:{allow_videos} outros:{allow_other_attachments} max_att:{max_attachments}"
        }
    }
}

class NoMsgCog(commands.Cog):
    """Enforça regras de conteúdo por canal (ex: somente imagens)."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('nomsg', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('nomsg', {})
        self.rules: List[Dict[str, Any]] = self.cfg.get('rules', [])
        self.feedback_cfg: Dict[str, Any] = self.cfg.get('feedback', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('nomsg')
        self.cfg = self.raw_cfg.get('nomsg', {})
        self.rules = self.cfg.get('rules', [])
        self.feedback_cfg = self.cfg.get('feedback', {})
        self.msgs = self.cfg.get('messages', {})
        self.debug = self.cfg.get('debug', False)

    def get_rule_for_channel(self, channel_id: int) -> Dict[str, Any] | None:
        for r in self.rules:
            if r.get('channel_id') == channel_id:
                return r
        return None

    async def delete_and_feedback(self, message: discord.Message, rule: Dict[str, Any], reason: str):
        notify = self.feedback_cfg.get('notify_user', True)
        delete_delay = self.feedback_cfg.get('delete_delay', 5)
        dm_user = self.feedback_cfg.get('dm_user', False)
        # Delete original
        try:
            await message.delete()
        except Exception:
            return
        # Feedback
        if notify:
            text = self.msgs.get('deleted', '{user} sua mensagem foi removida: {reason}').format(
                user=message.author.mention, reason=reason
            )
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
                await message.author.send(f"Sua mensagem em {message.channel.mention} foi removida: {reason}")
            except Exception:
                pass
        # Log opcional
        log_channel_id = rule.get('log_channel_id')
        if log_channel_id:
            ch = message.guild.get_channel(log_channel_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.send(f"Removida mensagem de {message.author} em {message.channel.mention}: {reason}")
                except Exception:
                    pass

    def attachment_type_allowed(self, att: discord.Attachment, rule: Dict[str, Any]) -> bool:
        ctype = (att.content_type or '').lower()
        # Fallback por extensão quando content_type vem vazio
        if not ctype and att.filename:
            name = att.filename.lower()
            if name.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                ctype = 'image/unknown'
            elif name.endswith(('.mp4', '.mov', '.webm', '.mkv')):
                ctype = 'video/unknown'
        if ctype.startswith('image/'):
            return rule.get('allow_images', True)
        if ctype.startswith('video/'):
            return rule.get('allow_videos', False)
        return rule.get('allow_other_attachments', False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        rule = self.get_rule_for_channel(message.channel.id)
        if not rule:
            return
        # Bypass por permissão ou role
        bypass_roles: List[int] = rule.get('bypass_roles', [])
        if bypass_roles and any(r.id in bypass_roles for r in getattr(message.author, 'roles', [])):
            return
        if message.author.guild_permissions.manage_messages:
            return

        allow_text = rule.get('allow_text', True)
        if not allow_text and message.content.strip():
            reason = rule.get('delete_reason', 'Este canal não permite texto.')
            return await self.delete_and_feedback(message, rule, reason)

        # Verifica anexos
        atts = message.attachments
        if atts:
            max_att = rule.get('max_attachments')
            if max_att is not None and len(atts) > max_att:
                reason = rule.get('delete_reason', 'Anexos acima do limite.')
                return await self.delete_and_feedback(message, rule, reason)
            # Cada tipo
            for att in atts:
                if not self.attachment_type_allowed(att, rule):
                    reason = rule.get('delete_reason', 'Tipo de anexo não permitido.')
                    return await self.delete_and_feedback(message, rule, reason)
            # require_attachment (se definido) garante que ao menos um anexo aceito exista
            if rule.get('require_attachment') and len(atts) == 0:
                reason = rule.get('delete_reason', 'Anexo obrigatório.')
                return await self.delete_and_feedback(message, rule, reason)
        else:
            # Sem anexos e canal exige imagens?
            if rule.get('allow_images') and not allow_text and not rule.get('allow_other_attachments') and not rule.get('allow_videos'):
                reason = rule.get('delete_reason', 'É obrigatório enviar imagem.')
                return await self.delete_and_feedback(message, rule, reason)
            if rule.get('require_attachment'):
                reason = rule.get('delete_reason', 'Anexo obrigatório.')
                return await self.delete_and_feedback(message, rule, reason)

        if self.debug:
            try:
                await message.channel.send(f"[nomsg debug] Aceita: msg_ok em {message.channel.mention}", delete_after=3)
            except Exception:
                pass

    @commands.command(name='nomsgreload')
    async def nomsg_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config nomsg recarregada.')

    @commands.command(name='nomsgrules')
    async def nomsg_rules(self, ctx: commands.Context):
        lines = [self.msgs.get('rule_summary_header', 'Regras ativas:')]
        for r in self.rules:
            tmpl = self.msgs.get('rule_line', '<#{channel_id}>')
            lines.append(tmpl.format(**r))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(NoMsgCog(bot))
