import os
import asyncio
from typing import Dict, Any

import discord
from discord import app_commands
from discord.ext import commands

try:  # Import opcional do gTTS para evitar falha de carregamento da cog
    from gtts import gTTS
    _GTTS_AVAILABLE = True
except ImportError:
    _GTTS_AVAILABLE = False

from config_loader import config_manager

class VoiceCommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = config_manager.load_cog("voice")
        self.voice_cfg: Dict[str, Any] = self.config.get("voice", {})
        self.msgs: Dict[str, str] = self.voice_cfg.get("messages", {})
        self.tts_cfg: Dict[str, Any] = self.voice_cfg.get("tts", {})

    def refresh_config(self):
        try:
            self.config = config_manager.reload_cog("voice")
        except Exception:
            pass
        self.voice_cfg = self.config.get("voice", {})
        self.msgs = self.voice_cfg.get("messages", {})
        self.tts_cfg = self.voice_cfg.get("tts", {})

    def _has_permission(self, interaction: discord.Interaction, command_name: str) -> bool:
        auth_roles = set(self.voice_cfg.get("authorized_roles", {}).get(command_name, []))
        auth_users = set(self.voice_cfg.get("authorized_users", {}).get(command_name, []))
        if auth_users and interaction.user.id in auth_users:
            return True
        if auth_roles and isinstance(interaction.user, discord.Member):
            if any(r.id in auth_roles for r in interaction.user.roles):
                return True
        # Se listas estiverem vazias, permitir por padrão
        return not auth_roles and not auth_users

    async def _ensure_ffmpeg(self) -> str:
        ffmpeg_path = self.tts_cfg.get("ffmpeg_path", "ffmpeg")
        return ffmpeg_path

    async def _play_tts(self, vc: discord.VoiceClient, text: str, lang: str = "pt", slow: bool = False):
        if not _GTTS_AVAILABLE:
            raise RuntimeError("Biblioteca gTTS não instalada. Use: pip install gTTS")
        # Gera arquivo temporário TTS
        os.makedirs("data", exist_ok=True)
        file_path = os.path.join("data", "tts_tmp.mp3")
        try:
            tts = gTTS(text=text, lang=lang, slow=slow)
            tts.save(file_path)
        except Exception as e:
            raise RuntimeError(f"Falha ao gerar TTS: {e}")
        ffmpeg = await self._ensure_ffmpeg()
        try:
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(file_path, executable=ffmpeg))
            if vc.is_playing():
                vc.stop()
            vc.play(source)
        except Exception as e:
            raise RuntimeError(f"Falha ao tocar áudio: {e}")

    @app_commands.command(name="conectar", description="Conectar o bot em uma call escolhida")
    async def conectar(self, interaction: discord.Interaction, canal: discord.VoiceChannel):
        self.refresh_config()
        if not self._has_permission(interaction, "conectar"):
            msg = self.msgs.get("no_permission", "Sem permissão.")
            return await interaction.response.send_message(msg, ephemeral=True)
        try:
            if interaction.guild is None:
                return await interaction.response.send_message("Este comando deve ser usado em um servidor.", ephemeral=True)
            current_vc: discord.VoiceClient | None = discord.utils.get(self.bot.voice_clients, guild=interaction.guild)
            if current_vc and current_vc.is_connected():
                await current_vc.move_to(canal)
                msg = self.msgs.get("moved", "Movido para {channel}.").format(channel=canal.mention)
                return await interaction.response.send_message(msg, ephemeral=True)
            else:
                vc = await canal.connect()
                msg = self.msgs.get("connected", "Conectado em {channel}.").format(channel=canal.mention)
                return await interaction.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            return await interaction.response.send_message("Sem permissão para conectar na call.", ephemeral=True)
        except Exception as e:
            return await interaction.response.send_message(f"Erro ao conectar: {e}", ephemeral=True)

    @app_commands.command(name="falar", description="Falar um texto na call atual do bot (TTS)")
    async def falar(self, interaction: discord.Interaction, texto: str):
        self.refresh_config()
        if not self._has_permission(interaction, "falar"):
            msg = self.msgs.get("no_permission", "Sem permissão.")
            return await interaction.response.send_message(msg, ephemeral=True)
        if not self.tts_cfg.get("enabled", True):
            return await interaction.response.send_message("TTS está desativado nas configurações.", ephemeral=True)
        if not _GTTS_AVAILABLE:
            return await interaction.response.send_message("TTS indisponível (gTTS não instalado).", ephemeral=True)
        try:
            if interaction.guild is None:
                return await interaction.response.send_message("Este comando deve ser usado em um servidor.", ephemeral=True)
            vc: discord.VoiceClient | None = discord.utils.get(self.bot.voice_clients, guild=interaction.guild)
            if not vc or not vc.is_connected():
                msg = self.msgs.get("not_connected", "O bot não está conectado a uma call.")
                return await interaction.response.send_message(msg, ephemeral=True)
            await interaction.response.send_message(self.msgs.get("speaking", "Falando na call: {text}").format(text=texto[:200]), ephemeral=True)
            await self._play_tts(vc, texto, lang=self.tts_cfg.get("language", "pt"), slow=bool(self.tts_cfg.get("slow", False)))
        except Exception as e:
            await interaction.followup.send(f"Erro ao falar: {e}", ephemeral=True)

    @app_commands.command(name="falar_chat", description="Enviar uma mensagem em um canal de texto escolhido")
    async def falar_chat(self, interaction: discord.Interaction, canal: discord.TextChannel, texto: str):
        self.refresh_config()
        if not self._has_permission(interaction, "falar_chat"):
            msg = self.msgs.get("no_permission", "Sem permissão.")
            return await interaction.response.send_message(msg, ephemeral=True)
        try:
            await canal.send(texto)
            msg = self.msgs.get("sent_chat", "Mensagem enviada em {channel}.").format(channel=canal.mention)
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Sem permissão para enviar mensagem nesse canal.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Erro ao enviar mensagem: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCommandsCog(bot))
