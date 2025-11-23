import asyncio
import datetime
import re
from collections import deque, defaultdict
from typing import Dict, Any, Deque, Tuple, List

import discord
from discord.ext import commands

from config_loader import config_manager

DEFAULTS = {
    "automod_spam": {
        "enabled": True,
        "debug": False,
        "log_channel_id": 0,
        "action": "delete_warn",  # delete | delete_warn | delete_punish
        "punishment": {
            "type": "timeout",
            "duration_seconds": 300,
            "reason": "Spam/Flood no chat"
        },
        "thresholds": {
            "flood_messages": 6,
            "flood_interval_seconds": 5,
            "repeat_same_content": 3,
            "repeat_interval_seconds": 12,
            "max_mentions": 6,
            "max_emojis": 15,
            "caps_ratio_trigger": 0.7,
            "min_caps_length": 15
        },
        "ignore": {
            "channel_ids": [],
            "user_ids": [],
            "role_ids": []
        },
        "warn": {
            "message": "{user} detectado spam/flood: {reason}",
            "delete_delay": 6,
            "dm_user": False
        },
        "messages": {
            "log_violation": "Spam/Flood: {user} tipo={type} razão={reason}",
            "status_header": "AntiSpam/AntiFlood — resumo",
            "status_main": "Enabled: {enabled} | Ação: {action} | Canal log: {log_channel_id}",
            "status_thresholds": "Flood: {flood_messages}/{flood_interval}s | Repetição: {repeat_same}/{repeat_interval}s | Caps: {caps_ratio} (min {caps_min}) | Mentions: {mentions} | Emojis: {emojis}",
            "type_flood": "flood de mensagens",
            "type_repeat": "mensagens repetidas",
            "type_mentions": "menções excessivas",
            "type_emojis": "excesso de emojis",
            "type_caps": "excesso de CAPS"
        }
    }
}


class AutoModSpam(commands.Cog):
    """Detecção de spam e flood (mensagens rápidas, repetidas, caps, menções e emojis)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('automod_spam', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('automod_spam', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.debug: bool = self.cfg.get('debug', False)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.action: str = self.cfg.get('action', 'delete_warn')
        self.punish_cfg: Dict[str, Any] = self.cfg.get('punishment', {})
        self.thresholds: Dict[str, Any] = self.cfg.get('thresholds', {})
        self.ignore_cfg: Dict[str, Any] = self.cfg.get('ignore', {})
        self.warn_cfg: Dict[str, Any] = self.cfg.get('warn', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})

        # Estruturas de tracking: flood & repetição
        # user_id -> deque de timestamps
        self._user_message_times: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=50))
        # user_id -> deque de (content, timestamp)
        self._user_recent_contents: Dict[int, Deque[Tuple[str, float]]] = defaultdict(lambda: deque(maxlen=20))
        self._cleanup_task: asyncio.Task | None = None
        self._start_cleanup_loop()

    def _start_cleanup_loop(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        # Limpa itens antigos para evitar crescimento indefinido
        try:
            while True:
                now = asyncio.get_event_loop().time()
                flood_int = float(self.thresholds.get('flood_interval_seconds', 5))
                repeat_int = float(self.thresholds.get('repeat_interval_seconds', 12))
                for uid, dq in list(self._user_message_times.items()):
                    while dq and (now - dq[0]) > max(flood_int * 2, 30):
                        dq.popleft()
                for uid, dq2 in list(self._user_recent_contents.items()):
                    while dq2 and (now - dq2[0][1]) > max(repeat_int * 2, 60):
                        dq2.popleft()
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('automod_spam')
        self.__init__(self.bot)

    # ----------------- Helpers -----------------
    def _ignored(self, message: discord.Message) -> bool:
        if not message.guild:
            return True
        if message.author.bot:
            return True
        if message.channel.id in self.ignore_cfg.get('channel_ids', []):
            return True
        if message.author.id in self.ignore_cfg.get('user_ids', []):
            return True
        role_ids = set(self.ignore_cfg.get('role_ids', []))
        if role_ids and any(r.id in role_ids for r in getattr(message.author, 'roles', [])):
            return True
        return False

    def _count_emojis(self, content: str) -> int:
        # custom emoji <:name:id> ou <a:name:id>
        custom = re.findall(r'<a?:\w+:\d+>', content)
        # unicode emojis grosseiro: contar símbolos não ascii (heurística)
        unicode_count = sum(1 for ch in content if ord(ch) > 0xFFFF or (0x2100 <= ord(ch) <= 0x2BFF))
        return len(custom) + unicode_count

    def _caps_ratio(self, content: str) -> float:
        letters = [c for c in content if c.isalpha()]
        if not letters:
            return 0.0
        upp = sum(1 for c in letters if c.isupper())
        return upp / len(letters)

    def _detect_mentions_excess(self, message: discord.Message, max_mentions: int) -> bool:
        total = len(message.mentions) + len(message.role_mentions) + (1 if '@everyone' in message.content else 0) + (1 if '@here' in message.content else 0)
        return total >= max_mentions

    def _detect_flood(self, user_id: int, now: float) -> bool:
        flood_count = int(self.thresholds.get('flood_messages', 6))
        flood_interval = float(self.thresholds.get('flood_interval_seconds', 5))
        dq = self._user_message_times[user_id]
        dq.append(now)
        while dq and (now - dq[0]) > flood_interval:
            dq.popleft()
        return len(dq) >= flood_count

    def _detect_repeat(self, user_id: int, content: str, now: float) -> bool:
        repeat_same = int(self.thresholds.get('repeat_same_content', 3))
        repeat_interval = float(self.thresholds.get('repeat_interval_seconds', 12))
        dq = self._user_recent_contents[user_id]
        dq.append((content, now))
        # Conta quantas iguais dentro da janela
        count = 0
        for txt, ts in list(dq):
            if (now - ts) <= repeat_interval and txt == content:
                count += 1
        return count >= repeat_same

    # ----------------- Logging & Punir -----------------
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
            duration = int(self.punish_cfg.get('duration_seconds', 300))
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
            try:
                await member.timeout(until, reason=reason)
            except Exception:
                if self.debug:
                    print('[automod_spam] Falha timeout')
        # Espaço futuro para: mutechat, ban, etc.

    async def _handle_violation(self, message: discord.Message, vtype: str, reason: str):
        member = message.author
        # Deleta
        try:
            await message.delete()
        except Exception:
            return
        # Aviso
        if self.action in ('delete_warn', 'delete_punish'):
            warn_msg = self.warn_cfg.get('message', '{user} violação: {reason}')
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
                    await member.send(f"Você gerou {vtype}: {reason}")
                except Exception:
                    pass
        # Punição se configurado
        await self._apply_punishment(member, f"{vtype}: {reason}")
        await self._log(message.guild, member, vtype, reason)

    # ----------------- Event -----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.enabled:
            return
        if self._ignored(message):
            return
        content = message.content or ''
        now = asyncio.get_event_loop().time()

        # Flood
        if self._detect_flood(message.author.id, now):
            reason = self.msgs.get('type_flood', 'flood de mensagens')
            return await self._handle_violation(message, 'flood', reason)

        # Repetição
        if content and self._detect_repeat(message.author.id, content, now):
            reason = self.msgs.get('type_repeat', 'mensagens repetidas')
            return await self._handle_violation(message, 'repeat', reason)

        # Menções excessivas
        max_mentions = int(self.thresholds.get('max_mentions', 6))
        if max_mentions > 0 and self._detect_mentions_excess(message, max_mentions):
            reason = self.msgs.get('type_mentions', 'menções excessivas')
            return await self._handle_violation(message, 'mentions', reason)

        # Emojis
        max_emojis = int(self.thresholds.get('max_emojis', 15))
        if max_emojis > 0:
            ec = self._count_emojis(content)
            if ec >= max_emojis:
                reason = self.msgs.get('type_emojis', 'excesso de emojis') + f" ({ec} >= {max_emojis})"
                return await self._handle_violation(message, 'emojis', reason)

        # CAPS
        min_caps_len = int(self.thresholds.get('min_caps_length', 15))
        if len(content) >= min_caps_len:
            ratio_trigger = float(self.thresholds.get('caps_ratio_trigger', 0.7))
            ratio = self._caps_ratio(content)
            if ratio >= ratio_trigger:
                reason = self.msgs.get('type_caps', 'excesso de CAPS') + f" (ratio={ratio:.2f})"
                return await self._handle_violation(message, 'caps', reason)

        if self.debug:
            # Log simplificado debug
            if self.log_channel_id:
                ch = message.guild.get_channel(self.log_channel_id)
                if isinstance(ch, discord.TextChannel):
                    try:
                        await ch.send(f"[spam debug] mensagem ok de {message.author}: len={len(content)}")
                    except Exception:
                        pass

    # ----------------- Commands -----------------
    @commands.command(name='automodspamreload')
    async def automod_spam_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config automod_spam recarregada.')

    @commands.command(name='automodspamstatus')
    async def automod_spam_status(self, ctx: commands.Context):
        t = self.thresholds
        lines = [self.msgs.get('status_header', 'AntiSpam/AntiFlood — resumo')]
        lines.append(self.msgs.get('status_main', 'Enabled: {enabled} | Ação: {action} | Canal log: {log_channel_id}').format(
            enabled=self.enabled, action=self.action, log_channel_id=self.log_channel_id
        ))
        lines.append(self.msgs.get('status_thresholds', 'Flood: {flood_messages}/{flood_interval}s | Repetição: {repeat_same}/{repeat_interval}s | Caps: {caps_ratio} (min {caps_min}) | Mentions: {mentions} | Emojis: {emojis}').format(
            flood_messages=t.get('flood_messages'), flood_interval=t.get('flood_interval_seconds'),
            repeat_same=t.get('repeat_same_content'), repeat_interval=t.get('repeat_interval_seconds'),
            caps_ratio=t.get('caps_ratio_trigger'), caps_min=t.get('min_caps_length'),
            mentions=t.get('max_mentions'), emojis=t.get('max_emojis')
        ))
        await ctx.reply('\n'.join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoModSpam(bot))
