import discord
from discord.ext import commands
import asyncio
import json
import io
from pathlib import Path
from typing import Dict, Any, List
from config_loader import config_manager

DATA_DIR = Path(__file__).parent.parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
POSTS_FILE = DATA_DIR / 'insta_posts.json'
_file_lock = asyncio.Lock()

def load_posts() -> Dict[str, Any]:
    if not POSTS_FILE.exists():
        return {}
    try:
        with POSTS_FILE.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

async def save_posts(data: Dict[str, Any]):
    async with _file_lock:
        tmp = POSTS_FILE.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(POSTS_FILE)

class LikeCommentView(discord.ui.View):
    def __init__(self, cog: 'InstaCog', post_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.post_id = post_id
        self.refresh_buttons()

    def refresh_buttons(self):
        post = self.cog.posts.get(str(self.post_id), {})
        likes = len(post.get('likes', []))
        comments = len(post.get('comments', []))
        cfg_btn = self.cog.cfg.get('buttons', {})
        self.clear_items()
        self.add_item(discord.ui.Button(label=f"{cfg_btn.get('like_label', '❤️')} {likes}", custom_id=f"insta_like:{self.post_id}", style=discord.ButtonStyle.secondary))
        # Comentários com contador igual ao botão de like
        self.add_item(discord.ui.Button(label=f"{cfg_btn.get('comment_label', '💬 Comentar')} {comments}", custom_id=f"insta_comment:{self.post_id}", style=discord.ButtonStyle.primary))
        self.add_item(discord.ui.Button(label=f"{cfg_btn.get('show_likes_label', '📖 Likes')}", custom_id=f"insta_showlikes:{self.post_id}:0", style=discord.ButtonStyle.secondary))
        self.add_item(discord.ui.Button(label=f"{cfg_btn.get('show_comments_label', '📝 Comentários')}", custom_id=f"insta_showcomments:{self.post_id}:0", style=discord.ButtonStyle.secondary))
        self.add_item(discord.ui.Button(label=f"{cfg_btn.get('delete_label', '🗑️ Excluir')}", custom_id=f"insta_delete:{self.post_id}", style=discord.ButtonStyle.danger))

class CommentModal(discord.ui.Modal, title="Novo Comentário"):
    comentario = discord.ui.TextInput(label="Comentário", placeholder="Digite seu comentário", max_length=300)

    def __init__(self, cog: 'InstaCog', post_id: int):
        super().__init__()
        self.cog = cog
        self.post_id = post_id

    async def on_submit(self, interaction: discord.Interaction):
        post = self.cog.posts.get(str(self.post_id))
        if not post:
            return await interaction.response.send_message("Post não encontrado.", ephemeral=True)
        post['comments'].append({
            'user_id': interaction.user.id,
            'content': str(self.comentario.value),
            'timestamp': int(discord.utils.utcnow().timestamp())
        })
        await save_posts(self.cog.posts)
        # Atualiza view no post
        msg = await self.cog.fetch_post_message(interaction.guild, self.post_id)
        if msg:
            view = LikeCommentView(self.cog, self.post_id)
            try:
                await msg.edit(view=view)
            except Exception:
                pass
        await interaction.response.send_message("Comentário adicionado!", ephemeral=True)

class InstaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('insta')
        self.cfg = self.raw_cfg.get('insta', {})
        self.posts: Dict[str, Any] = load_posts()
        self.webhook_cache: Dict[int, discord.Webhook] = {}
        self.pagination_size = self.cfg.get('pagination_size', 5)
        # Registra views persistentes para posts existentes (permite interações após restart)
        for pid in list(self.posts.keys()):
            try:
                self.bot.add_view(LikeCommentView(self, int(pid)))
            except Exception:
                pass

    def refresh_config(self):
        try:
            self.raw_cfg = config_manager.reload_cog('insta')
        except Exception:
            pass
        self.cfg = self.raw_cfg.get('insta', {})
        self.pagination_size = self.cfg.get('pagination_size', 5)

    async def get_or_create_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        if channel.id in self.webhook_cache:
            return self.webhook_cache[channel.id]
        hooks = await channel.webhooks()
        name = self.cfg.get('webhook_name', 'InstaFeed')
        for h in hooks:
            if h.name == name:
                self.webhook_cache[channel.id] = h
                return h
        try:
            new_hook = await channel.create_webhook(name=name, reason="Webhook para feed insta")
            self.webhook_cache[channel.id] = new_hook
            return new_hook
        except discord.Forbidden:
            return None

    def is_target_channel(self, channel_id: int) -> bool:
        return channel_id in (self.cfg.get('male_channel_id'), self.cfg.get('female_channel_id'))

    async def fetch_post_message(self, guild: discord.Guild, post_id: int) -> discord.Message | None:
        post = self.posts.get(str(post_id))
        if not post:
            return None
        channel = guild.get_channel(post.get('channel_id'))
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            return await channel.fetch_message(post_id)
        except Exception:
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not self.is_target_channel(message.channel.id):
            return
        # Restrições: só anexos, sem texto significativo
        if message.content.strip():
            try:
                await message.delete()
            except Exception:
                pass
            return
        max_att = self.cfg.get('max_attachments', 1)
        if len(message.attachments) == 0 or len(message.attachments) > max_att:
            try:
                await message.delete()
            except Exception:
                pass
            return
        att = message.attachments[0]
        allow_images = self.cfg.get('allow_images', True)
        allow_videos = self.cfg.get('allow_videos', True)
        if att.content_type:
            if att.content_type.startswith('image/') and not allow_images:
                await message.delete(); return
            if att.content_type.startswith('video/') and not allow_videos:
                await message.delete(); return
        # Envia via webhook
        webhook = await self.get_or_create_webhook(message.channel)
        if webhook is None:
            return
        # Ler anexo e reenviar via webhook preservando nome/avatar
        try:
            file_bytes = await att.read()
            file_stream = io.BytesIO(file_bytes)
            file = discord.File(file_stream, filename=att.filename)
        except Exception:
            return
        avatar_url = message.author.display_avatar.url
        # Monta embed para garantir avatar visível como autor
        embed_color = self.cfg.get('embed_color')
        try:
            color = discord.Color(int(str(embed_color), 16)) if embed_color else discord.Color.blurple()
        except Exception:
            color = discord.Color.blurple()
        embed = discord.Embed(color=color)
        embed.set_author(name=message.author.display_name, icon_url=avatar_url)
        # Usa attachment dentro do embed (exibição padronizada)
        embed.set_image(url=f"attachment://{att.filename}")
        # Inclui mention do autor para permitir acesso rápido ao perfil real (webhook não abre perfil do usuário verdadeiro)
        sent = await webhook.send(content=message.author.mention, username=message.author.display_name, avatar_url=avatar_url, embed=embed, file=file, wait=True)
        # Armazena post
        self.posts[str(sent.id)] = {
            'author_id': message.author.id,
            'channel_id': message.channel.id,
            'likes': [],
            'comments': []
        }
        await save_posts(self.posts)
        # Aplica view
        view = LikeCommentView(self, sent.id)
        try:
            await sent.edit(view=view)
        except Exception:
            pass
        # Deleta original
        try:
            await message.delete()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data:
            return
        custom_id = interaction.data.get('custom_id')
        if not custom_id:
            return
        if custom_id.startswith('insta_like:'):
            post_id = int(custom_id.split(':')[1])
            post = self.posts.get(str(post_id))
            if not post:
                return await interaction.response.send_message('Post não encontrado.', ephemeral=True)
            uid = interaction.user.id
            if uid in post['likes']:
                post['likes'].remove(uid)
                msg_txt = 'Like removido.'
            else:
                post['likes'].append(uid)
                msg_txt = 'Você curtiu.'
            await save_posts(self.posts)
            # Atualiza view usando resposta da interação (mais confiável com mensagens de webhook)
            view = LikeCommentView(self, post_id)
            # Primeiro tenta editar como resposta direta; se já respondido, edita original
            try:
                await interaction.response.edit_message(view=view)
            except discord.InteractionResponded:
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    # Fallback final: busca a mensagem pelo ID
                    msg = await self.fetch_post_message(interaction.guild, post_id)
                    if msg:
                        try:
                            await msg.edit(view=view)
                        except Exception:
                            pass
            # Envia confirmação epheméral via followup para não conflitar com edição da mensagem original
            try:
                await interaction.followup.send(msg_txt, ephemeral=True)
            except Exception:
                pass
        elif custom_id.startswith('insta_comment:'):
            post_id = int(custom_id.split(':')[1])
            modal = CommentModal(self, post_id)
            await interaction.response.send_modal(modal)
        elif custom_id.startswith('insta_showlikes:'):
            _, pid, page = custom_id.split(':')
            post_id = int(pid); page = int(page)
            post = self.posts.get(str(post_id))
            if not post:
                return await interaction.response.send_message('Post não encontrado.', ephemeral=True)
            likes: List[int] = post.get('likes', [])
            size = self.pagination_size
            total_pages = max(1, (len(likes) + size - 1) // size)
            page = max(0, min(page, total_pages - 1))
            slice_ids = likes[page*size:(page+1)*size]
            lines = []
            for uid in slice_ids:
                user = interaction.guild.get_member(uid)
                mention = user.mention if user else f"<@{uid}>"
                lines.append(mention)
            desc = '\n'.join(lines) if lines else 'Sem likes.'
            embed = discord.Embed(title='Likes do post', description=desc, color=discord.Color.dark_theme())
            embed.set_footer(text=f'Página {page+1}/{total_pages} - Total: {len(likes)}')
            view = discord.ui.View()
            prev_id = f"insta_showlikes:{post_id}:{page-1}"
            next_id = f"insta_showlikes:{post_id}:{page+1}"
            view.add_item(discord.ui.Button(label='Voltar', custom_id=prev_id, disabled=page==0))
            view.add_item(discord.ui.Button(label='Próximo', custom_id=next_id, disabled=page>=total_pages-1))
            await interaction.response.send_message(embed=embed, ephemeral=True, view=view)
        elif custom_id.startswith('insta_showcomments:'):
            _, pid, page = custom_id.split(':')
            post_id = int(pid); page = int(page)
            post = self.posts.get(str(post_id))
            if not post:
                return await interaction.response.send_message('Post não encontrado.', ephemeral=True)
            comments: List[Dict[str, Any]] = post.get('comments', [])
            size = self.pagination_size
            total_pages = max(1, (len(comments) + size - 1) // size)
            page = max(0, min(page, total_pages - 1))
            slice_comments = comments[page*size:(page+1)*size]
            lines = []
            for c in slice_comments:
                user = interaction.guild.get_member(c['user_id'])
                mention = user.mention if user else f"<@{c['user_id']}>"
                lines.append(f"{mention}: {c['content']}")
            desc = '\n'.join(lines) if lines else 'Sem comentários.'
            embed = discord.Embed(title='Comentários do post', description=desc, color=discord.Color.blurple())
            embed.set_footer(text=f'Página {page+1}/{total_pages} - Total: {len(comments)} comentários')
            view = discord.ui.View()
            prev_id = f"insta_showcomments:{post_id}:{page-1}"
            next_id = f"insta_showcomments:{post_id}:{page+1}"
            view.add_item(discord.ui.Button(label='Voltar', custom_id=prev_id, disabled=page==0))
            view.add_item(discord.ui.Button(label='Próximo', custom_id=next_id, disabled=page>=total_pages-1))
            await interaction.response.send_message(embed=embed, ephemeral=True, view=view)
        elif custom_id.startswith('insta_delete:'):
            post_id = int(custom_id.split(':')[1])
            post = self.posts.get(str(post_id))
            if not post:
                return await interaction.response.send_message('Post não encontrado.', ephemeral=True)
            if post.get('author_id') != interaction.user.id:
                return await interaction.response.send_message('Você não é o autor deste post.', ephemeral=True)
            # Deleta mensagem e registro
            msg = await self.fetch_post_message(interaction.guild, post_id)
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass
            self.posts.pop(str(post_id), None)
            await save_posts(self.posts)
            await interaction.response.send_message('Post excluído.', ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(InstaCog(bot))