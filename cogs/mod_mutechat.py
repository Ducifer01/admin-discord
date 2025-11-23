import discord
from discord.ext import commands
import asyncio
from discord.utils import utcnow
from typing import Dict, Any
from config_loader import config_manager

class MuteChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: Dict[str, Any] = config_manager.load_cog("mutechat").get("mutechat", {})
        self.embed_cfg: Dict[str, Any] = self.config.get("embed_settings", {})
        self.log_channel_id: int = self.config.get("log_channel_id", 0)
        self._scheduled_unmutes: Dict[int, asyncio.Task] = {}
        self.messages: Dict[str, str] = self.config.get("messages", {})

    def refresh_config(self):
        raw = config_manager.reload_cog("mutechat")
        self.config = raw.get("mutechat", {})
        self.embed_cfg = self.config.get("embed_settings", {})
        self.log_channel_id = self.config.get("log_channel_id", 0)
        self.messages = self.config.get("messages", {})

    def _color(self, key: str, fallback: str) -> int:
        hex_str = self.embed_cfg.get("colors", {}).get(key, fallback)
        try:
            return int(hex_str, 16)
        except ValueError:
            return int(fallback, 16)

    def is_authorized(self, ctx: commands.Context) -> bool:
        auth_roles = self.config.get("authorized_roles", []) or []
        if auth_roles and any(r.id in auth_roles for r in ctx.author.roles):
            return True
        perms = ctx.author.guild_permissions
        return (perms.manage_messages and perms.manage_roles) or perms.administrator

    def parse_duration(self, duration_str: str):
        import re
        m = re.match(r"(\d+)([smhd])", duration_str.lower())
        if not m:
            return None
        val = int(m.group(1)); unit = m.group(2)
        mult = {"s":1, "m":60, "h":3600, "d":86400}.get(unit, None)
        return val * mult if mult else None

    def build_embed(self, tipo: str, moderator: discord.Member, alvo: discord.Member, motivo: str, duracao_segundos: int | None = None):
        title_key = "title_mute" if tipo == "mute" else "title_unmute"
        title = self.embed_cfg.get(title_key, "Chat Mute")
        color = self._color("mute" if tipo == "mute" else "unmute", "FF5500" if tipo == "mute" else "33AAFF")
        use_ts = self.embed_cfg.get("use_timestamp", True)
        motivo_cb = self.embed_cfg.get("motivo_codeblock", True)
        show_ids = self.embed_cfg.get("show_ids", True)
        embed = discord.Embed(title=title, color=color)
        if use_ts:
            embed.timestamp = utcnow()
        mod_field = f"{moderator.mention} | {moderator.id}" if show_ids else moderator.mention
        alvo_field = f"{alvo.mention} | {alvo.id}" if show_ids else alvo.mention
        embed.add_field(name="moderador", value=mod_field, inline=True)
        embed.add_field(name="membro", value=alvo_field, inline=True)
        if tipo == "mute" and duracao_segundos:
            embed.add_field(name="tempo", value=f"{duracao_segundos} segundos", inline=True)
        motivo_fmt = f"```{motivo}```" if motivo_cb else motivo
        embed.add_field(name="motivo", value=motivo_fmt, inline=False)
        thumb = self.embed_cfg.get("thumbnail_url")
        if thumb:
            embed.set_thumbnail(url=thumb)
        banner = self.embed_cfg.get("banner_url")
        if banner:
            embed.set_image(url=banner)
        footer_text = self.embed_cfg.get("footer_text", "Sistema de Moderação")
        footer_icon = self.embed_cfg.get("footer_icon_url") or None
        embed.set_footer(text=footer_text, icon_url=footer_icon)
        return embed

    async def _log_embed(self, embed: discord.Embed):
        if not self.log_channel_id:
            return
        ch = self.bot.get_channel(self.log_channel_id)
        if ch and isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

    async def _dm_user(self, member: discord.Member, content: str):
        if not self.config.get("dm_user", False):
            return
        try:
            await member.send(content)
        except Exception:
            pass

    @commands.command(name="mutechat")
    async def mutechat(self, ctx: commands.Context, membro: discord.Member, duracao: str, *, motivo: str = "Nenhum motivo especificado"):
        self.refresh_config()
        if not self.is_authorized(ctx):
            await ctx.reply(self.messages.get("no_permission", "Sem permissão."))
            return
        role_id = self.config.get("muted_role_id")
        if not role_id:
            await ctx.reply(self.messages.get("no_role", "Sem role configurado."))
            return
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.reply(self.messages.get("no_role", "Role não encontrada."))
            return
        # Hierarchy check
        if ctx.author.top_role <= membro.top_role and ctx.author != ctx.guild.owner:
            await ctx.reply(self.messages.get("hierarchy_fail", "Hierarquia impede ação."))
            return
        # Duration parse
        seconds = self.parse_duration(duracao)
        if not seconds or seconds <= 0:
            await ctx.reply(self.messages.get("duration_invalid", "Duração inválida."))
            return
        # Already muted check
        if role in membro.roles:
            await ctx.reply(self.messages.get("already_muted", "Já mutado."))
            return
        try:
            await membro.add_roles(role, reason=f"MuteChat: {motivo}")
        except discord.Forbidden:
            await ctx.reply(self.messages.get("mute_apply_fail", "Falha ao adicionar cargo."))
            return
        except discord.HTTPException as e:
            await ctx.reply(f"Falha: {e}")
            return
        # Embed & log
        embed = self.build_embed("mute", ctx.author, membro, motivo, duracao_segundos=seconds)
        await ctx.reply(embed=embed)
        await self._log_embed(embed)
        # DM
        await self._dm_user(membro, self.messages.get("dm_mute", "Você foi mutado.").format(duration_str=duracao, reason=motivo))
        # Schedule unmute
        async def unmute_task():
            await asyncio.sleep(seconds)
            await self._perform_unmute(ctx.guild, ctx.author, membro, reason="Tempo expirado")
        task = asyncio.create_task(unmute_task())
        self._scheduled_unmutes[membro.id] = task

    async def _perform_unmute(self, guild: discord.Guild, moderator: discord.Member | None, membro: discord.Member, reason: str):
        role_id = self.config.get("muted_role_id")
        role = guild.get_role(role_id) if role_id else None
        if role and role in membro.roles:
            try:
                await membro.remove_roles(role, reason=f"UnmuteChat: {reason}")
            except discord.Forbidden:
                # log minimal
                pass
        embed = self.build_embed("unmute", moderator or membro, membro, reason)
        await self._log_embed(embed)
        # Try send to a channel maybe (no ctx) - choose system channel
        sys_ch = guild.system_channel
        if sys_ch and sys_ch.permissions_for(guild.me).send_messages:
            try:
                await sys_ch.send(embed=embed)
            except Exception:
                pass
        await self._dm_user(membro, self.messages.get("dm_unmute", "Seu mute terminou.").format(reason=reason))

    @commands.command(name="unmutechat")
    async def unmutechat(self, ctx: commands.Context, membro: discord.Member, *, motivo: str = "Remoção manual do mute"):
        self.refresh_config()
        if not self.is_authorized(ctx):
            await ctx.reply(self.messages.get("no_permission", "Sem permissão."))
            return
        role_id = self.config.get("muted_role_id")
        role = ctx.guild.get_role(role_id) if role_id else None
        if not role or role not in membro.roles:
            await ctx.reply(self.messages.get("not_muted", "Não está mutado."))
            return
        task = self._scheduled_unmutes.pop(membro.id, None)
        if task and not task.done():
            task.cancel()
        try:
            await membro.remove_roles(role, reason=f"UnmuteChat manual: {motivo}")
        except discord.Forbidden:
            await ctx.reply(self.messages.get("role_remove_fail", "Erro ao remover cargo."))
            return
        except discord.HTTPException as e:
            await ctx.reply(f"Falha: {e}")
            return
        embed = self.build_embed("unmute", ctx.author, membro, motivo)
        await ctx.reply(embed=embed)
        await self._log_embed(embed)
        await self._dm_user(membro, self.messages.get("dm_unmute", "Seu mute terminou.").format(reason=motivo))

    @commands.command(name="mutechatreload")
    async def mutechat_reload(self, ctx: commands.Context):
        if not self.is_authorized(ctx):
            return await ctx.reply(self.messages.get("no_permission", "Sem permissão."))
        self.refresh_config()
        await ctx.reply("Config mutechat recarregada.")

    @commands.command(name="mutechatstatus")
    async def mutechat_status(self, ctx: commands.Context):
        role_id = self.config.get("muted_role_id")
        role = ctx.guild.get_role(role_id) if role_id else None
        ch = self.bot.get_channel(self.log_channel_id)
        lines = ["Status MuteChat"]
        lines.append(f"Cargo mutechat: {role.mention if role else '(não definido)'}")
        lines.append(f"Log canal: {ch.mention if ch else '(não definido)'}")
        lines.append(f"DM usuário: {self.config.get('dm_user', False)}")
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(MuteChat(bot))
