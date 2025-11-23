import asyncio
import datetime
from typing import Dict, Any, List, Set, Tuple

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "protect_roles": {
        "enabled": True,
        "protected_role_ids": [],
        "allowed_user_ids": [],
        "allowed_granter_role_ids": [],
        "audit_window_seconds": 12,
        "enforce_delay_seconds": 2,
        "enforce_retries": 3,
        "bypass_bot_users": True,
        "log_channel_id": 0,
        "feedback": {
            "notify_executer": False,
            "notify_target": False,
            "notify_message": "Você não está autorizado a conceder o cargo protegido {role}. Ação revertida.",
            "delete_delay": 6
        },
        "messages": {
            "log_denied": "Proteção de cargos: {executor} tentou conceder {roles} para {member}. Ação revertida.",
            "log_fail": "Falha ao remover {roles} de {member}: {error}",
            "log_retry": "Reforço #{attempt}: removendo {roles} de {member}.",
            "status_header": "Proteção de cargos — resumo",
            "status_roles": "Cargos protegidos: {roles}",
            "status_allowed": "Autorizados (users): {users} | (roles): {roles}"
        },
        "debug": False
    }
}

class ProtectRolesCog(commands.Cog):
    """Impede concessão de cargos protegidos por usuários não autorizados."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('protect_roles', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('protect_roles', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.protected: Set[int] = set(self.cfg.get('protected_role_ids', []))
        self.allowed_users: Set[int] = set(self.cfg.get('allowed_user_ids', []))
        self.allowed_roles: Set[int] = set(self.cfg.get('allowed_granter_role_ids', []))
        self.audit_window: int = self.cfg.get('audit_window_seconds', 12)
        self.enforce_delay: float = float(self.cfg.get('enforce_delay_seconds', 2))
        self.enforce_retries: int = self.cfg.get('enforce_retries', 3)
        self.bypass_bot_users: bool = self.cfg.get('bypass_bot_users', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.log_embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.feedback: Dict[str, Any] = self.cfg.get('feedback', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.debug: bool = self.cfg.get('debug', False)

        # Controle de reforço para evitar duplicação massiva (member_id, role_id) -> task running
        self._enforce_tasks: Dict[Tuple[int, int], asyncio.Task] = {}

    def refresh_config(self):
        # Recarrega config e reinicializa campos
        self.raw_cfg = config_manager.reload_cog('protect_roles')
        new_cfg = self.raw_cfg.get('protect_roles', {})
        self.__init__(self.bot)  # reusar inicialização padrão
        self.cfg.update(new_cfg)

    def _is_authorized_executor(self, guild: discord.Guild, executor: discord.abc.User) -> bool:
        if executor is None:
            return False
        if executor.id in self.allowed_users:
            return True
        member = guild.get_member(executor.id)
        if member and any(r.id in self.allowed_roles for r in getattr(member, 'roles', [])):
            return True
        return False

    def _role_objs(self, guild: discord.Guild, role_ids: Set[int]) -> List[discord.Role]:
        roles: List[discord.Role] = []
        for rid in role_ids:
            r = guild.get_role(rid)
            if r:
                roles.append(r)
        return roles

    async def _log(self, guild: discord.Guild, text: str = None, *, title: str | None = None, fields: List[tuple] | None = None, color_hex: str | None = None):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        use_embed = self.log_embed_cfg.get('enabled', True)
        if use_embed:
            color = color_hex or self.log_embed_cfg.get('color', 'FF5555')
            try:
                color_value = int(str(color), 16)
            except Exception:
                color_value = int('FF5555', 16)
            description = text if text is not None else ''
            embed = discord.Embed(title=title or 'Proteção de cargos', description=description, color=color_value)
            if fields:
                for name, value, inline in fields:
                    embed.add_field(name=name, value=value, inline=inline)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
        else:
            try:
                await ch.send(text or title or 'Proteção de cargos')
            except Exception:
                pass

    async def _enforce_remove(self, member: discord.Member, role_ids: Set[int], reason: str, attempt: int = 0):
        roles = self._role_objs(member.guild, role_ids)
        if not roles:
            return
        try:
            await member.remove_roles(*roles, reason=f"ProtectRoles: {reason}")
        except Exception as e:
            await self._log(
                member.guild,
                title=self.cfg.get('log_embed', {}).get('title_fail', 'Falha ao remover cargo'),
                fields=[
                    ('Membro', member.mention, True),
                    ('Cargos', ','.join([r.mention for r in roles]), True),
                    ('Erro', f'```{e}```', False)
                ]
            )
        else:
            if attempt:
                await self._log(
                    member.guild,
                    title=self.cfg.get('log_embed', {}).get('title_retry', 'Reforço de proteção'),
                    fields=[
                        ('Tentativa', str(attempt), True),
                        ('Membro', member.mention, True),
                        ('Cargos', ','.join([r.mention for r in roles]), False)
                    ]
                )

    async def _enforce_loop(self, member_id: int, role_ids: Set[int], guild: discord.Guild, base_reason: str):
        key_list = [(member_id, rid) for rid in role_ids]
        # marca tasks
        for key in key_list:
            self._enforce_tasks[key] = asyncio.current_task()
        try:
            for attempt in range(self.enforce_retries):
                await asyncio.sleep(self.enforce_delay)
                member = guild.get_member(member_id)
                if not member:
                    return
                current = {r.id for r in member.roles}
                still_present = set(role_ids) & current
                if not still_present:
                    return
                await self._enforce_remove(member, still_present, base_reason, attempt=attempt+1)
        finally:
            for key in key_list:
                self._enforce_tasks.pop(key, None)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.enabled:
            return
        if self.bypass_bot_users and after.bot:
            return

        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        added = after_ids - before_ids
        # Interessa apenas aos protegidos
        protected_added = added & self.protected
        if not protected_added:
            return

        guild = after.guild

        # Descobrir executor pelo audit log
        executor = None
        try:
            now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.member_role_update):
                if entry.target.id != after.id:
                    continue
                # só eventos recentes
                if entry.created_at and (now - entry.created_at).total_seconds() > self.audit_window:
                    break
                executor = entry.user
                break
        except Exception:
            executor = None

        authorized = self._is_authorized_executor(guild, executor) if executor else False
        if authorized:
            return  # Permitido

        # Não autorizado -> remover imediatamente
        # Log bonito com embed
        roles_text = ', '.join([guild.get_role(rid).mention for rid in protected_added if guild.get_role(rid)])
        await self._log(
            guild,
            title=self.log_embed_cfg.get('title_denied', 'Cargo protegido revertido'),
            fields=[
                ('Executor', getattr(executor, 'mention', 'desconhecido'), True),
                ('Membro', after.mention, True),
                ('Cargos', roles_text or '(?)', False)
            ]
        )
        await self._enforce_remove(after, protected_added, "executor não autorizado")
        # Evita duplicar enforcement se já rodando
        pending = {(after.id, rid) for rid in protected_added}
        if not any(k in self._enforce_tasks for k in pending):
            asyncio.create_task(self._enforce_loop(after.id, protected_added, guild, "reforço de segurança"))

    @commands.command(name='protectrolesreload')
    async def protect_roles_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config proteção de cargos recarregada.')

    @commands.command(name='protectrolesstatus')
    async def protect_roles_status(self, ctx: commands.Context):
        g = ctx.guild
        pr = [g.get_role(rid).mention for rid in self.protected if g.get_role(rid)] or ['(nenhum)']
        au = [f"<@{uid}>" for uid in self.allowed_users] or ['(nenhum)']
        ar = [g.get_role(rid).mention for rid in self.allowed_roles if g.get_role(rid)] or ['(nenhum)']
        lines = [self.msgs.get('status_header', 'Proteção de cargos — resumo')]
        lines.append(self.msgs.get('status_roles', 'Cargos protegidos: {roles}').format(roles=', '.join(pr)))
        lines.append(self.msgs.get('status_allowed', 'Autorizados (users): {users} | (roles): {roles}').format(users=', '.join(au), roles=', '.join(ar)))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(ProtectRolesCog(bot))
