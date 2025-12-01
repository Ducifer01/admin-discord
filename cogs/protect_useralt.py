import asyncio
import datetime
from typing import Dict, Any, List, Set

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "protect_useralt": {
        "enabled": True,
        "min_account_age_hours": 24,
        "ban": True,
        "kick_if_ban_fails": True,
        "log_channel_id": 0,
        "log_embed": {
            "enabled": True,
            "color": "FF3333",
            "title_banned": "Conta nova banida",
            "title_kicked": "Conta nova expulsa",
            "title_fail": "Falha ao punir conta nova"
        },
        "exempt_user_ids": [],
        "exempt_role_ids": [],
        "exempt_invite_codes": [],
        "messages": {
            "log_banned": "{user} banido (idade {age_hours}h < {min_age}h)",
            "log_kicked": "{user} expulso (idade {age_hours}h < {min_age}h)",
            "log_fail": "Falha ao punir {user}: {error}",
            "status_header": "Proteção contra contas novas",
            "status_main": "Habilitado: {enabled} | Mínimo horas: {min_age} | Banir: {ban} | Kick se falhar: {kick_if_ban_fails}",
            "status_exempts": "Exceções usuários: {users} | roles: {roles} | convites: {invites}"
        },
        "debug": False
    }
}

class ProtectUserAltCog(commands.Cog):
    """Cog que bane ou expulsa contas recém-criadas ao entrar no servidor."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('protect_useralt', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('protect_useralt', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.min_age_hours: int = self.cfg.get('min_account_age_hours', 24)
        self.do_ban: bool = self.cfg.get('ban', True)
        self.kick_if_ban_fails: bool = self.cfg.get('kick_if_ban_fails', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.log_embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.exempt_user_ids: Set[int] = set(self.cfg.get('exempt_user_ids', []))
        self.exempt_role_ids: Set[int] = set(self.cfg.get('exempt_role_ids', []))
        self.exempt_invite_codes: Set[str] = set([c.lower() for c in self.cfg.get('exempt_invite_codes', [])])
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('protect_useralt')
        self.__init__(self.bot)

    async def _log(self, guild: discord.Guild, title: str, fields: List[tuple]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.log_embed_cfg.get('enabled', True):
            color_hex = self.log_embed_cfg.get('color', 'FF3333')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('FF3333', 16)
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

    def _account_age_hours(self, user: discord.User) -> float:
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        created = user.created_at
        if not created:
            return 999999.0
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        delta = now - created
        return delta.total_seconds() / 3600.0

    def _is_exempt(self, member: discord.Member, invite_code: str | None) -> bool:
        if member.id in self.exempt_user_ids:
            return True
        if any(r.id in self.exempt_role_ids for r in member.roles):
            return True
        if invite_code and invite_code.lower() in self.exempt_invite_codes:
            return True
        return False

    async def _punish(self, member: discord.Member, age_hours: float):
        # Banir ou expulsar
        action_title = self.log_embed_cfg.get('title_banned' if self.do_ban else 'title_kicked', 'Conta nova punida')
        if self.do_ban:
            try:
                # Incluir motivo detalhado com idade real da conta; log será feito pela cog de Ban via on_member_ban
                await member.ban(reason=f"Conta nova ({age_hours:.1f}h < {self.min_age_hours}h)")
                # Não enviar log próprio para bans bem-sucedidos; o log padrão de ban cuidará disso
                return
            except Exception as e:
                if self.kick_if_ban_fails:
                    try:
                        await member.kick(reason="Falha ban conta nova")
                        await self._log(member.guild, self.log_embed_cfg.get('title_kicked', 'Conta nova expulsa'), [
                            ('Usuário', member.mention, True),
                            ('Falha ban', f'```{e}```', False)
                        ])
                        return
                    except Exception as e2:
                        await self._log(member.guild, self.log_embed_cfg.get('title_fail', 'Falha ao punir conta nova'), [
                            ('Usuário', member.mention, True),
                            ('Erro ban', f'```{e}```', False),
                            ('Erro kick', f'```{e2}```', False)
                        ])
                        return
                else:
                    await self._log(member.guild, self.log_embed_cfg.get('title_fail', 'Falha ao punir conta nova'), [
                        ('Usuário', member.mention, True),
                        ('Erro ban', f'```{e}```', False)
                    ])
                    return
        else:
            try:
                await member.kick(reason=f"Conta nova (<{self.min_age_hours}h)")
                await self._log(member.guild, action_title, [
                    ('Usuário', member.mention, True),
                    ('Idade (h)', f"{age_hours:.1f}", True)
                ])
            except Exception as e:
                await self._log(member.guild, self.log_embed_cfg.get('title_fail', 'Falha ao punir conta nova'), [
                    ('Usuário', member.mention, True),
                    ('Erro kick', f'```{e}```', False)
                ])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self.enabled or member.bot:
            return
        # Idade da conta
        age_hours = self._account_age_hours(member)
        if age_hours >= self.min_age_hours:
            return
        # Invite code não é fornecido diretamente no evento; se quiser suportar, pode registrar invites antes.
        invite_code = None
        if self._is_exempt(member, invite_code):
            return
        await self._punish(member, age_hours)
        if self.debug:
            try:
                ch = member.guild.system_channel
                if ch:
                    await ch.send(f"[alt debug] Punido {member} idade {age_hours:.1f}h", delete_after=5)
            except Exception:
                pass

    @commands.command(name='useraltreload')
    async def useralt_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config proteção contas novas recarregada.')

    @commands.command(name='useraltstatus')
    async def useralt_status(self, ctx: commands.Context):
        m = self.msgs
        users = [f"<@{uid}>" for uid in self.exempt_user_ids] or ['(nenhum)']
        roles = [ctx.guild.get_role(rid).mention for rid in self.exempt_role_ids if ctx.guild.get_role(rid)] or ['(nenhum)']
        invites = list(self.exempt_invite_codes) or ['(nenhum)']
        lines = [m.get('status_header', 'Proteção contra contas novas')]
        lines.append(m.get('status_main', '').format(enabled=self.enabled, min_age=self.min_age_hours, ban=self.do_ban, kick_if_ban_fails=self.kick_if_ban_fails))
        lines.append(m.get('status_exempts', '').format(users=', '.join(users), roles=', '.join(roles), invites=', '.join(invites)))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(ProtectUserAltCog(bot))
