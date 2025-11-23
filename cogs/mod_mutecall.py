import discord
from discord.ext import commands, tasks
import logging
import asyncio
from discord.utils import utcnow
from config_loader import config_manager

logger = logging.getLogger(__name__)

class MuteRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Carrega config via config_manager (arquivo: config/cogs/mutecall.json)
        self.config = config_manager.load_cog("mutecall").get("mutecall", {})
        self._interval = self.config.get("interval_seconds", 10)
        self._batch_every = self.config.get("batch_sleep_every", 10)
        self._batch_sleep = self.config.get("batch_sleep_seconds", 1)
        # Loop usa intervalo fixo (decorator). Se configurado diferente, avisa.
        if self._interval != 10:
            logger.warning(f"interval_seconds={self._interval} em mutecall.json, mas o loop está fixo em 10s. Ajuste manual se necessário.")
        self.sync_roles_periodically.start()
        self._scheduled_unmutes = {}

        self.embed_cfg = self.config.get("embed_settings", {})
        self.log_channel_id = self.config.get("log_channel_id", 0)

    def refresh_config(self):
        try:
            raw = config_manager.reload_cog("mutecall")
            self.config = raw.get("mutecall", {})
        except Exception:
            pass
        self.embed_cfg = self.config.get("embed_settings", {})
        self.log_channel_id = self.config.get("log_channel_id", 0)

    def _color(self, key: str, fallback: str) -> int:
        hex_str = self.embed_cfg.get("colors", {}).get(key, fallback)
        try:
            return int(hex_str, 16)
        except ValueError:
            return int(fallback, 16)

    def build_embed(self, tipo: str, moderator: discord.Member, alvo: discord.Member, motivo: str, duração_segundos: int = None, inicio_ts: float = None):
        """tipo: 'mute' ou 'unmute'"""
        title_key = "title_mute" if tipo == "mute" else "title_unmute"
        title = self.embed_cfg.get(title_key, "Mute")
        color = self._color(tipo, "FFAA00" if tipo == "mute" else "1E90FF")
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
        if tipo == "mute" and duração_segundos:
            # tempo restante / duração total
            embed.add_field(name="tempo", value=f"{duração_segundos} segundos", inline=True)
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

    def is_authorized(self, ctx: commands.Context) -> bool:
        """Retorna True se o autor tem permissão para usar os comandos mutecall/unmutecall.
        Critérios:
        - Se possuir qualquer cargo cujo ID esteja em authorized_roles (config)
        - OU possuir as permissões de servidor (mute_members & manage_roles)
        """
        auth_roles = self.config.get("authorized_roles", []) or []
        if auth_roles and any(r.id in auth_roles for r in ctx.author.roles):
            return True
        perms = ctx.author.guild_permissions
        return perms.mute_members and perms.manage_roles

    async def sync_member_roles(self, member: discord.Member, muted_role: discord.Role):
        """Sincroniza o cargo de um membro com base no seu estado de mute (apenas mute do servidor)."""
        # Determina se o membro está mutado pelo servidor
        is_muted = False
        if member.voice and member.voice.channel:  # Membro está em um canal de voz
            # Verifica se o bot tem acesso ao canal
            if not member.voice.channel.permissions_for(member.guild.me).view_channel:
                logger.warning(f"Bot não tem permissão para ver o canal {member.voice.channel.name} (ID: {member.voice.channel.id}) no servidor {member.guild.name} (ID: {member.guild.id}). Não é possível verificar o estado de mute do membro {member.name}#{member.discriminator} (ID: {member.id}).")
                return
            # Considera apenas o mute aplicado pelo servidor (ignora auto-mute)
            is_muted = member.voice.mute
            # Log adicional para debug
            if member.voice.self_mute and not is_muted:
                logger.debug(f"Membro {member.name}#{member.discriminator} (ID: {member.id}) está auto-silenciado, mas não mutado pelo servidor. Cargo {muted_role.name} não será aplicado.")
        else:  # Membro não está em um canal de voz
            is_muted = False

        try:
            # Se o membro está mutado pelo servidor, ele DEVE ter o cargo
            if is_muted:
                if muted_role not in member.roles:
                    await member.add_roles(muted_role, reason="Membro silenciado pelo servidor")
                    logger.info(f"Cargo {muted_role.name} (ID: {muted_role.id}) adicionado ao membro {member.name}#{member.discriminator} (ID: {member.id}) no servidor {member.guild.name} (ID: {member.guild.id}) porque o membro foi silenciado pelo servidor.")
            # Se o membro não está mutado pelo servidor, ele NÃO DEVE ter o cargo
            else:
                if muted_role in member.roles:
                    await member.remove_roles(muted_role, reason="Membro não está mais silenciado pelo servidor")
                    logger.info(f"Cargo {muted_role.name} (ID: {muted_role.id}) removido do membro {member.name}#{member.discriminator} (ID: {member.id}) no servidor {member.guild.name} (ID: {member.guild.id}) porque o membro não está mais silenciado pelo servidor.")
        except discord.Forbidden:
            logger.error(f"Permissões insuficientes para gerenciar o cargo {muted_role.name} (ID: {muted_role.id}) do membro {member.name}#{member.discriminator} (ID: {member.id}) no servidor {member.guild.name} (ID: {member.guild.id}).")
        except discord.HTTPException as e:
            logger.error(f"Erro ao gerenciar o cargo {muted_role.name} (ID: {muted_role.id}) do membro {member.name}#{member.discriminator} (ID: {member.id}) no servidor {member.guild.name} (ID: {member.guild.id}): {e}")

    @tasks.loop(seconds=10)
    async def sync_roles_periodically(self):
        """Verifica periodicamente o estado de mute de todos os membros e sincroniza os cargos."""
        if self.config.get("enable_debug"):
            logger.debug("Iniciando verificação periódica de cargos...")

        muted_role_id = self.config.get("muted_role_id")
        if not muted_role_id:
            logger.warning("Nenhum 'muted_role_id' configurado em mutecall.json.")
            return

        for guild in self.bot.guilds:
            # Busca o cargo no servidor
            muted_role = guild.get_role(muted_role_id)
            if not muted_role:
                logger.error(f"Cargo com ID {muted_role_id} não encontrado no servidor {guild.name} (ID: {guild.id}).")
                continue

            # Verifica se o bot tem permissões para gerenciar cargos
            bot_member = guild.me
            if not bot_member.guild_permissions.manage_roles:
                logger.error(f"Bot não tem permissão para gerenciar cargos no servidor {guild.name} (ID: {guild.id}).")
                continue

            # Verifica se o cargo está abaixo do cargo mais alto do bot
            if muted_role.position >= bot_member.top_role.position:
                logger.error(f"O cargo {muted_role.name} (ID: {muted_role.id}) está acima do cargo mais alto do bot no servidor {guild.name} (ID: {guild.id}).")
                continue

            # Sincroniza os cargos de todos os membros
            members_processed = 0
            for member in guild.members:
                await self.sync_member_roles(member, muted_role)
                members_processed += 1
                if members_processed % self._batch_every == 0:
                    await asyncio.sleep(self._batch_sleep)

            if self.config.get("enable_debug"):
                logger.debug(f"Verificação periódica concluída para o servidor {guild.name} (ID: {guild.id}). {members_processed} membros processados.")

    @sync_roles_periodically.before_loop
    async def before_sync_roles(self):
        """Garante que a tarefa periódica só comece após o bot estar pronto."""
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        """Sincroniza os cargos de todos os membros quando o bot é iniciado."""
        logger.info("Bot iniciado. Sincronizando cargos de membros silenciados...")
        muted_role_id = self.config.get("muted_role_id")
        if not muted_role_id:
            logger.warning("Nenhum 'muted_role_id' configurado em mutecall.json.")
            return

        for guild in self.bot.guilds:
            # Busca o cargo no servidor
            muted_role = guild.get_role(muted_role_id)
            if not muted_role:
                logger.error(f"Cargo com ID {muted_role_id} não encontrado no servidor {guild.name} (ID: {guild.id}).")
                continue

            # Verifica se o bot tem permissões para gerenciar cargos
            bot_member = guild.me
            if not bot_member.guild_permissions.manage_roles:
                logger.error(f"Bot não tem permissão para gerenciar cargos no servidor {guild.name} (ID: {guild.id}).")
                continue

            # Verifica se o cargo está abaixo do cargo mais alto do bot
            if muted_role.position >= bot_member.top_role.position:
                logger.error(f"O cargo {muted_role.name} (ID: {muted_role.id}) está acima do cargo mais alto do bot no servidor {guild.name} (ID: {guild.id}).")
                continue

            # Sincroniza os cargos de todos os membros
            members_processed = 0
            for member in guild.members:
                await self.sync_member_roles(member, muted_role)
                members_processed += 1
                if members_processed % self._batch_every == 0:
                    await asyncio.sleep(self._batch_sleep)

            logger.info(f"Sincronização inicial concluída para o servidor {guild.name} (ID: {guild.id}). {members_processed} membros processados.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Monitora mudanças no estado de voz e atualiza os cargos."""
        # Verifica se o cargo foi configurado
        muted_role_id = self.config.get("muted_role_id")
        if not muted_role_id:
            logger.warning("Nenhum 'muted_role_id' configurado em mutecall.json.")
            return

        # Busca o cargo no servidor
        muted_role = member.guild.get_role(muted_role_id)
        if not muted_role:
            logger.error(f"Cargo com ID {muted_role_id} não encontrado no servidor {member.guild.name} (ID: {member.guild.id}).")
            return

        # Verifica se o bot tem permissões para gerenciar cargos
        bot_member = member.guild.me
        if not bot_member.guild_permissions.manage_roles:
            logger.error(f"Bot não tem permissão para gerenciar cargos no servidor {member.guild.name} (ID: {member.guild.id}).")
            return

        # Verifica se o cargo está abaixo do cargo mais alto do bot
        if muted_role.position >= bot_member.top_role.position:
            logger.error(f"O cargo {muted_role.name} (ID: {muted_role.id}) está acima do cargo mais alto do bot no servidor {member.guild.name} (ID: {member.guild.id}).")
            return

        # Sincroniza o cargo com base no estado atual
        await self.sync_member_roles(member, muted_role)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Monitora mudanças nos cargos dos membros e impede a remoção manual do cargo de mutado se o membro ainda estiver mutado."""
        # Verifica se o cargo foi configurado
        muted_role_id = self.config.get("muted_role_id")
        if not muted_role_id:
            logger.warning("Nenhum cargo configurado em role_muted_voice.json. Configure o 'muted_role_id' para usar esta funcionalidade.")
            return

        # Busca o cargo no servidor
        muted_role = after.guild.get_role(muted_role_id)
        if not muted_role:
            logger.error(f"Cargo com ID {muted_role_id} não encontrado no servidor {after.guild.name} (ID: {after.guild.id}).")
            return

        # Verifica se o bot tem permissões para gerenciar cargos
        bot_member = after.guild.me
        if not bot_member.guild_permissions.manage_roles:
            logger.error(f"Bot não tem permissão para gerenciar cargos no servidor {after.guild.name} (ID: {after.guild.id}).")
            return

        # Verifica se o cargo está abaixo do cargo mais alto do bot
        if muted_role.position >= bot_member.top_role.position:
            logger.error(f"O cargo {muted_role.name} (ID: {muted_role.id}) está acima do cargo mais alto do bot no servidor {after.guild.name} (ID: {after.guild.id}).")
            return

        # Verifica se o cargo de mutado foi removido manualmente
        if muted_role in before.roles and muted_role not in after.roles:
            # Verifica se o membro ainda está mutado pelo servidor
            is_muted = False
            if after.voice and after.voice.channel:
                if not after.voice.channel.permissions_for(after.guild.me).view_channel:
                    logger.warning(f"Bot não tem permissão para ver o canal {after.voice.channel.name} (ID: {after.voice.channel.id}) no servidor {after.guild.name} (ID: {after.guild.id}). Não é possível verificar o estado de mute do membro {after.name}#{after.discriminator} (ID: {after.id}).")
                    return
                is_muted = after.voice.mute  # Considera apenas o mute do servidor

            # Se o membro ainda está mutado pelo servidor, recoloca o cargo
            if is_muted:
                try:
                    await after.add_roles(muted_role, reason="Cargo de mutado removido manualmente, mas o membro ainda está silenciado pelo servidor")
                    logger.info(f"Cargo {muted_role.name} (ID: {muted_role.id}) recolocado no membro {after.name}#{after.discriminator} (ID: {after.id}) no servidor {after.guild.name} (ID: {after.guild.id}) porque o membro ainda está silenciado pelo servidor.")
                except discord.Forbidden:
                    logger.error(f"Permissões insuficientes para recolocar o cargo {muted_role.name} (ID: {muted_role.id}) no membro {after.name}#{after.discriminator} (ID: {after.id}) no servidor {after.guild.name} (ID: {after.guild.id}).")
                except discord.HTTPException as e:
                    logger.error(f"Erro ao recolocar o cargo {muted_role.name} (ID: {muted_role.id}) no membro {after.name}#{after.discriminator} (ID: {after.id}) no servidor {after.guild.name} (ID: {after.guild.id}): {e}")

    # ==================== COMANDOS PREFIX ====================
    def parse_duration(self, duration_str: str):
        import re
        m = re.match(r"(\d+)([smhd])", duration_str.lower())
        if not m:
            return None
        val = int(m.group(1)); unit = m.group(2)
        mult = {"s":1, "m":60, "h":3600, "d":86400}.get(unit, None)
        return val * mult if mult else None

    @commands.command(name="mutecall")
    async def mutecall(self, ctx: commands.Context, membro: discord.Member, duração: str, *, motivo: str = "Nenhum motivo especificado"):
        """Silencia (server mute) um membro em chamadas por duração. Uso: !mutecall @user 5m Flood.
        Aceita unidades s/m/h/d."""
        self.refresh_config()
        # Verificação de autorização customizada (usa authorized_roles ou permissões nativas)
        if not self.is_authorized(ctx):
            await self.bot.send_no_permission(ctx)
            return
        muted_role_id = self.config.get("muted_role_id")
        if not muted_role_id:
            sent = await ctx.send("Config 'muted_role_id' não definida em mutecall.json.")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent.delete(delay=delete_bot)
                except Exception:
                    pass
            return
        # Valida hierarquia
        if ctx.author.top_role <= membro.top_role and ctx.author != ctx.guild.owner:
            sent = await ctx.send("Você não pode mutar alguém com cargo igual ou superior ao seu.")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent.delete(delay=delete_bot)
                except Exception:
                    pass
            return
        # Parse duração
        seconds = self.parse_duration(duração)
        if not seconds or seconds <= 0:
            sent = await ctx.send("Formato de duração inválido. Ex: 30s, 5m, 2h, 1d")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent.delete(delay=delete_bot)
                except Exception:
                    pass
            return
        # Aplica mute server-side
        try:
            await membro.edit(mute=True, reason=motivo)
        except discord.Forbidden:
            sent = await ctx.send("Não tenho permissão para mutar esse usuário.")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent.delete(delay=delete_bot)
                except Exception:
                    pass
            return
        except discord.HTTPException as e:
            sent = await ctx.send(f"Falha ao mutar: {e}")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent.delete(delay=delete_bot)
                except Exception:
                    pass
            return
        # Aplica cargo
        role = ctx.guild.get_role(muted_role_id)
        if role and role not in membro.roles:
            try:
                await membro.add_roles(role, reason="MuteCall aplicado")
            except discord.Forbidden:
                await ctx.send("Mute aplicado, mas não consegui dar o cargo (permissão).")
        # Embed de mute
        embed = self.build_embed("mute", ctx.author, membro, motivo, duração_segundos=seconds)
        sent_ok = await ctx.send(embed=embed)
        delete_bot = self.embed_cfg.get("delete_message_delay", self.config.get("delete_bot_reply_seconds"))
        if delete_bot:
            try:
                await sent_ok.delete(delay=delete_bot)
            except Exception:
                pass
        log_channel = self.bot.get_channel(self.log_channel_id)
        if log_channel:
            try:
                await log_channel.send(embed=embed)
            except Exception as e:
                logger.warning(f"Falha ao enviar embed de mute ao canal de log: {e}")
        # Agenda unmute
        async def unmute_task():
            await asyncio.sleep(seconds)
            try:
                await membro.edit(mute=False, reason="Duração do mutecall expirada")
                if role and role in membro.roles:
                    await membro.remove_roles(role, reason="Tempo de mute expirado")
                embed_un = self.build_embed("unmute", ctx.author, membro, "Tempo expirado")
                sent_auto = await ctx.send(embed=embed_un)
                delete_bot2 = self.embed_cfg.get("delete_message_delay", self.config.get("delete_bot_reply_seconds"))
                if delete_bot2:
                    try:
                        await sent_auto.delete(delay=delete_bot2)
                    except Exception:
                        pass
                log_channel = self.bot.get_channel(self.log_channel_id)
                if log_channel:
                    try:
                        await log_channel.send(embed=embed_un)
                    except Exception as e:
                        logger.warning(f"Falha ao enviar embed de unmute automático ao canal de log: {e}")
            except Exception as e:
                logger.error(f"Erro ao desmutar automaticamente: {e}")
        task = asyncio.create_task(unmute_task())
        self._scheduled_unmutes[membro.id] = task

    @commands.command(name="unmutecall")
    async def unmutecall(self, ctx: commands.Context, membro: discord.Member, *, motivo: str = "Remoção manual do mute"):
        """Remove o mute de voz aplicado pelo servidor antes do tempo. Uso: !unmutecall @user [motivo]."""
        self.refresh_config()
        if not self.is_authorized(ctx):
            await self.bot.send_no_permission(ctx)
            return
        role_id = self.config.get("muted_role_id")
        role = ctx.guild.get_role(role_id) if role_id else None
        try:
            await membro.edit(mute=False, reason=motivo)
            if role and role in membro.roles:
                await membro.remove_roles(role, reason=motivo)
            # Cancela tarefa programada se existir
            task = self._scheduled_unmutes.pop(membro.id, None)
            if task and not task.done():
                task.cancel()
            embed_un = self.build_embed("unmute", ctx.author, membro, motivo)
            sent_ok = await ctx.send(embed=embed_un)
            delete_bot = self.embed_cfg.get("delete_message_delay", self.config.get("delete_bot_reply_seconds"))
            if delete_bot:
                try:
                    await sent_ok.delete(delay=delete_bot)
                except Exception:
                    pass
            log_channel = self.bot.get_channel(self.log_channel_id)
            if log_channel:
                try:
                    await log_channel.send(embed=embed_un)
                except Exception as e:
                    logger.warning(f"Falha ao enviar embed de unmute manual ao canal de log: {e}")
        except discord.Forbidden:
            sent_err = await ctx.send("Não tenho permissão para desmutar esse usuário.")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent_err.delete(delay=delete_bot)
                except Exception:
                    pass
        except discord.HTTPException as e:
            sent_err = await ctx.send(f"Falha ao desmutar: {e}")
            delete_bot = self.config.get("delete_bot_reply_seconds")
            if delete_bot:
                try:
                    await sent_err.delete(delay=delete_bot)
                except Exception:
                    pass

async def setup(bot):
    await bot.add_cog(MuteRoles(bot))