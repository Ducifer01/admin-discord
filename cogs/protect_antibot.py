import asyncio
from typing import Dict, Any, List, Set

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "protect_antibot": {
        "enabled": True,
        "action": "kick",  # kick | ban
        "log_channel_id": 0,
        "whitelist_bot_ids": [],
        "whitelist_inviter_ids": [],
        "whitelist_inviter_role_ids": [],
        "block_if_missing_inviter": True,
        "dm_inviter": True,
        "dm_inviter_message": "Você não pode adicionar bots ao servidor.",
        "dm_delete_delay": 8,
        "reason_template": "Bot não autorizado: {bot} (ID {bot_id}) adicionado por {inviter}.",
        "messages": {
            "log_action": "{action_title}: {bot} ({bot_id}) ação={action} por {inviter}",
            "log_fail": "Falha ao {action} bot {bot} ({bot_id}): {error}",
            "status_header": "Proteção AntiBot — resumo",
            "status_main": "Habilitado: {enabled} | Ação: {action} | Canal log: {log_channel_id}",
            "status_whitelists": "Bots permitidos: {bots} | Inviters permitidos: {users} | Roles inviters: {roles}",
            "dm_inviter": "{user}, você não está autorizado(a) a adicionar bots aqui.",
            "log_missing_inviter": "Não foi possível determinar o autor do add do bot {bot_id}."
        },
        "log_embed": {
            "enabled": True,
            "color": "FF8800",
            "title_action": "Bot bloqueado",
            "title_fail": "Falha AntiBot",
            "title_allowed": "Bot permitido (whitelist)"
        },
        "debug": False
    }
}


class ProtectAntiBotCog(commands.Cog):
    """Impede adição de bots não autorizados. Expulsa ou bane conforme configuração."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('protect_antibot', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('protect_antibot', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.action: str = self.cfg.get('action', 'kick').lower()  # 'kick' ou 'ban'
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.whitelist_bot_ids: Set[int] = set(self.cfg.get('whitelist_bot_ids', []))
        self.whitelist_inviter_ids: Set[int] = set(self.cfg.get('whitelist_inviter_ids', []))
        self.whitelist_inviter_role_ids: Set[int] = set(self.cfg.get('whitelist_inviter_role_ids', []))
        self.block_if_missing_inviter: bool = self.cfg.get('block_if_missing_inviter', True)
        self.dm_inviter: bool = self.cfg.get('dm_inviter', True)
        self.dm_inviter_message: str = self.cfg.get('dm_inviter_message', 'Você não pode adicionar bots ao servidor.')
        self.dm_delete_delay: int = int(self.cfg.get('dm_delete_delay', 8))
        self.reason_template: str = self.cfg.get('reason_template', 'Bot não autorizado.')
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.log_embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('protect_antibot')
        self.__init__(self.bot)

    def _is_inviter_whitelisted(self, guild: discord.Guild, inviter: discord.abc.User | None) -> bool:
        if inviter is None:
            return False
        if inviter.id in self.whitelist_inviter_ids:
            return True
        member = guild.get_member(inviter.id)
        if member:
            for r in getattr(member, 'roles', []):
                if r.id in self.whitelist_inviter_role_ids:
                    return True
        return False

    async def _log(self, guild: discord.Guild, title: str, fields: List[tuple]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.log_embed_cfg.get('enabled', True):
            color_hex = self.log_embed_cfg.get('color', 'FF8800')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('FF8800', 16)
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

    async def _punish_bot(self, member: discord.Member, inviter: discord.abc.User | None):
        reason = self.reason_template.format(
            bot=str(member), bot_id=member.id, inviter=getattr(inviter, 'mention', 'desconhecido'), action=self.action
        )
        action_title = self.log_embed_cfg.get('title_action', 'Bot bloqueado')
        if self.action == 'ban':
            try:
                await member.ban(reason=reason, delete_message_days=0)
                await self._log(member.guild, action_title, [
                    ('Bot', member.mention, True),
                    ('ID', str(member.id), True),
                    ('Ação', 'ban', True),
                    ('Inviter', getattr(inviter, 'mention', 'desconhecido'), True)
                ])
            except Exception as e:
                await self._log(member.guild, self.log_embed_cfg.get('title_fail', 'Falha AntiBot'), [
                    ('Bot', member.mention, True),
                    ('Erro', f'```{e}```', False)
                ])
        else:  # kick padrão
            try:
                await member.kick(reason=reason)
                await self._log(member.guild, action_title, [
                    ('Bot', member.mention, True),
                    ('ID', str(member.id), True),
                    ('Ação', 'kick', True),
                    ('Inviter', getattr(inviter, 'mention', 'desconhecido'), True)
                ])
            except Exception as e:
                await self._log(member.guild, self.log_embed_cfg.get('title_fail', 'Falha AntiBot'), [
                    ('Bot', member.mention, True),
                    ('Erro', f'```{e}```', False)
                ])

        # DM para inviter (opcional)
        if self.dm_inviter and inviter and isinstance(inviter, (discord.User, discord.Member)):
            try:
                dm_msg = self.msgs.get('dm_inviter', self.dm_inviter_message).format(user=inviter.mention)
                sent = await inviter.send(dm_msg)
                if self.dm_delete_delay > 0:
                    await asyncio.sleep(self.dm_delete_delay)
                    try:
                        await sent.delete()
                    except Exception:
                        pass
            except Exception:
                pass

    async def _fetch_inviter(self, guild: discord.Guild, bot_member: discord.Member) -> discord.abc.User | None:
        # Usa audit log bot_add
        inviter = None
        try:
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.bot_add):
                if entry.target.id == bot_member.id:
                    inviter = entry.user
                    break
        except Exception:
            inviter = None
        return inviter

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self.enabled:
            return
        if not member.bot:
            return  # só interessa bots

        # Whitelist de bot
        if member.id in self.whitelist_bot_ids:
            if self.log_channel_id:
                await self._log(member.guild, self.log_embed_cfg.get('title_allowed', 'Bot permitido'), [
                    ('Bot', member.mention, True),
                    ('ID', str(member.id), True)
                ])
            return

        inviter = await self._fetch_inviter(member.guild, member)
        if inviter is None and self.block_if_missing_inviter:
            # Sem inviter conhecido: punir
            await self._punish_bot(member, inviter)
            return

        # Verifica whitelist inviter
        if inviter and self._is_inviter_whitelisted(member.guild, inviter):
            if self.log_channel_id:
                await self._log(member.guild, self.log_embed_cfg.get('title_allowed', 'Bot permitido'), [
                    ('Bot', member.mention, True),
                    ('Inviter', inviter.mention, True)
                ])
            return

        # Punir bot
        await self._punish_bot(member, inviter)

        if self.debug:
            try:
                sc = member.guild.system_channel
                if sc:
                    await sc.send(f"[antibot debug] bot {member} tratado (inviter={getattr(inviter, 'id', None)})", delete_after=6)
            except Exception:
                pass

    @commands.command(name='antibotreload')
    async def antibot_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config AntiBot recarregada.')

    @commands.command(name='antibotstatus')
    async def antibot_status(self, ctx: commands.Context):
        bots = [str(bid) for bid in self.whitelist_bot_ids] or ['(nenhum)']
        users = [f"<@{uid}>" for uid in self.whitelist_inviter_ids] or ['(nenhum)']
        roles = []
        for rid in self.whitelist_inviter_role_ids:
            r = ctx.guild.get_role(rid)
            if r:
                roles.append(r.mention)
        roles = roles or ['(nenhum)']
        lines = [self.msgs.get('status_header', 'Proteção AntiBot — resumo')]
        lines.append(self.msgs.get('status_main', 'Habilitado: {enabled} | Ação: {action} | Canal log: {log_channel_id}').format(
            enabled=self.enabled, action=self.action, log_channel_id=self.log_channel_id
        ))
        lines.append(self.msgs.get('status_whitelists', 'Bots permitidos: {bots} | Inviters permitidos: {users} | Roles inviters: {roles}').format(
            bots=', '.join(bots), users=', '.join(users), roles=', '.join(roles)
        ))
        await ctx.reply('\n'.join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(ProtectAntiBotCog(bot))
