import datetime
from typing import Dict, Any

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "automod_nomention": {
        "enabled": True,
        "debug": False,
        "log_channel_id": 0,
        "action": "delete_warn",  # delete | delete_warn | delete_punish
        "punishment": {
            "type": "timeout",
            "duration_seconds": 600,
            "reason": "Menção proibida"
        },
        "blocked": {
            "role_ids": [],              # IDs de cargos específicos proibidos
            "block_everyone": True,      # Bloquear @everyone
            "block_here": True,          # Bloquear @here
            "block_role_mentions": True, # Bloquear menção de qualquer cargo (override de role_ids)
            "block_user_ids": []         # IDs de usuários cuja menção é proibida
        },
        "exempt": {
            "roles": [],                 # Cargos isentos
            "users": [],                 # Usuários isentos
            "manage_messages_bypass": True
        },
        "warn": {
            "message": "{user} menção não permitida: {reason}",
            "delete_delay": 6,
            "dm_user": False
        },
        "messages": {
            "log_violation": "NoMention: {user} tipo={type} razão={reason}",
            "status_header": "Automod NoMention — resumo",
            "status_main": "Enabled: {enabled} | Ação: {action} | Canal log: {log_channel_id}",
            "status_blocked": "Bloqueados: everyone={everyone} here={here} roles={roles} ids={ids} role_mentions={role_mentions}",
            "type_everyone": "menção @everyone",
            "type_here": "menção @here",
            "type_role": "menção de cargo bloqueado",
            "type_role_generic": "menção de qualquer cargo",
            "type_user": "menção de usuário bloqueado"
        }
    }
}


class AutoModNoMention(commands.Cog):
    """Impede menção de determinados cargos, @everyone/@here ou usuários específicos, com punição configurável."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('automod_nomention', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('automod_nomention', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.debug: bool = self.cfg.get('debug', False)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.action: str = self.cfg.get('action', 'delete_warn')
        self.punish_cfg: Dict[str, Any] = self.cfg.get('punishment', {})
        self.blocked_cfg: Dict[str, Any] = self.cfg.get('blocked', {})
        self.exempt_cfg: Dict[str, Any] = self.cfg.get('exempt', {})
        self.warn_cfg: Dict[str, Any] = self.cfg.get('warn', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('automod_nomention')
        self.__init__(self.bot)

    def _exempt(self, member: discord.Member) -> bool:
        if not member:
            return False
        if self.exempt_cfg.get('manage_messages_bypass', True) and member.guild_permissions.manage_messages:
            return True
        if member.id in self.exempt_cfg.get('users', []):
            return True
        role_ids = set(self.exempt_cfg.get('roles', []))
        if role_ids and any(r.id in role_ids for r in getattr(member, 'roles', [])):
            return True
        return False

    async def _log(self, guild: discord.Guild, user: discord.Member, vtype: str, reason: str):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        text = self.msgs.get('log_violation', 'Violação').format(user=user.mention, type=vtype, reason=reason)
        try:
            await ch.send(text)
        except Exception:
            pass

    async def _apply_punishment(self, member: discord.Member, reason: str):
        if self.action != 'delete_punish':
            return
        p_type = self.punish_cfg.get('type', 'timeout')
        if p_type == 'timeout':
            duration = int(self.punish_cfg.get('duration_seconds', 600))
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
            try:
                await member.timeout(until, reason=reason)
            except Exception:
                if self.debug:
                    print('[automod_nomention] Falha timeout')

    async def _handle_violation(self, message: discord.Message, vtype: str, reason: str):
        member = message.author
        # Deleta
        try:
            await message.delete()
        except Exception:
            return
        # Aviso
        if self.action in ('delete_warn', 'delete_punish'):
            warn_msg = self.warn_cfg.get('message', '{user} menção bloqueada: {reason}')
            delete_delay = int(self.warn_cfg.get('delete_delay', 6))
            text = warn_msg.format(user=member.mention, reason=reason, type=vtype)
            try:
                sent = await message.channel.send(text)
                if delete_delay > 0:
                    await sent.delete(delay=delete_delay)
            except Exception:
                pass
            if self.warn_cfg.get('dm_user'):
                try:
                    await member.send(f"Menção proibida detectada: {reason}")
                except Exception:
                    pass
        await self._apply_punishment(member, f"{vtype}: {reason}")
        await self._log(message.guild, member, vtype, reason)

    def _detect_violation(self, message: discord.Message):
        content = message.content or ''
        # everyone / here
        if self.blocked_cfg.get('block_everyone', True) and '@everyone' in content:
            return 'everyone', self.msgs.get('type_everyone', 'menção @everyone')
        if self.blocked_cfg.get('block_here', True) and '@here' in content:
            return 'here', self.msgs.get('type_here', 'menção @here')
        # user ids específicos
        blocked_users = set(self.blocked_cfg.get('block_user_ids', []))
        if blocked_users:
            for u in message.mentions:
                if u.id in blocked_users:
                    return 'user', self.msgs.get('type_user', 'menção de usuário bloqueado') + f" ({u.id})"
        # role generic
        if self.blocked_cfg.get('block_role_mentions', True) and message.role_mentions:
            return 'role_generic', self.msgs.get('type_role_generic', 'menção de qualquer cargo')
        # roles específicos
        blocked_roles = set(self.blocked_cfg.get('role_ids', []))
        if blocked_roles:
            for r in message.role_mentions:
                if r.id in blocked_roles:
                    return 'role_specific', self.msgs.get('type_role', 'menção de cargo bloqueado') + f" ({r.id})"
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.enabled or not message.guild:
            return
        if message.author.bot:
            return
        if self._exempt(message.author):
            return
        result = self._detect_violation(message)
        if result:
            vtype, reason = result
            await self._handle_violation(message, vtype, reason)
        elif self.debug and self.log_channel_id:
            ch = message.guild.get_channel(self.log_channel_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.send(f"[nomention debug] ok: {message.author} len={len(message.content or '')}")
                except Exception:
                    pass

    # ---------------- Commands -----------------
    @commands.command(name='automodnomentionreload')
    async def automod_nomention_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config automod_nomention recarregada.')

    @commands.command(name='automodnomentionstatus')
    async def automod_nomention_status(self, ctx: commands.Context):
        b = self.blocked_cfg
        lines = [self.msgs.get('status_header', 'Automod NoMention — resumo')]
        lines.append(self.msgs.get('status_main', 'Enabled: {enabled} | Ação: {action} | Canal log: {log_channel_id}').format(
            enabled=self.enabled, action=self.action, log_channel_id=self.log_channel_id
        ))
        lines.append(self.msgs.get('status_blocked', 'Bloqueados').format(
            everyone=b.get('block_everyone', True),
            here=b.get('block_here', True),
            roles=', '.join(str(rid) for rid in b.get('role_ids', [])) or '(nenhum)',
            ids=', '.join(str(uid) for uid in b.get('block_user_ids', [])) or '(nenhum)',
            role_mentions=b.get('block_role_mentions', True)
        ))
        await ctx.reply('\n'.join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoModNoMention(bot))
