import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import re
import random
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
import better_profanity
from dotenv import load_dotenv
from url_checker import check_url_with_filter, download_filter_list
import toml

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

WHITELIST_FILE = 'invite_whitelist.toml'
BANLOG_FILE = 'ban_log.toml'
VERIFY_CONFIG_FILE = 'verify_config.toml'

invite_whitelist_channels = defaultdict(set)
user_message_counts = defaultdict(list)
user_message_times = defaultdict(list)
violation_tracker = defaultdict(lambda: defaultdict(lambda: {
    'status': 'active',
    'count': 0,
    'last_violation': None,
    'violations': [],
    'banned_at': None,
    'ban_reason': None,
}))

verify_configs = {}
verify_cooldowns = defaultdict(float)

VERIFY_COOLDOWN_SECONDS = 30
VERIFY_TIME_LIMIT = 60
VERIFY_PANEL_CUSTOM_ID = 'botname_verify_panel_start'
VERIFY_PANEL_TITLE = 'サーバー認証'
VERIFY_PANEL_BODY = '下のボタンを押して、簡単な数学の問題に回答し認証を完了してください。'

RAID_JOIN_THRESHOLD = 5
RAID_JOIN_WINDOW_SECONDS = 10
RAID_NEW_ACCOUNT_MINUTES = 10
RAID_MODE_DURATION_MINUTES = 15

recent_joins = defaultdict(list)
raid_mode_guilds = {}

COMMAND_COOLDOWN_USES = 3
COMMAND_COOLDOWN_SECONDS = 10

def load_banlog():

    global violation_tracker
    try:
        if os.path.exists(BANLOG_FILE):
            with open(BANLOG_FILE, 'r', encoding='utf-8') as f:
                data = toml.load(f)
                for guild_id, users in data.items():
                    for user_id, info in users.items():
                        violation_tracker[int(guild_id)][int(user_id)] = {
                            'status': info.get('status', 'active'),
                            'count': info.get('count', 0),
                            'last_violation': info.get('last_violation'),
                            'violations': info.get('violations', []),
                            'banned_at': info.get('banned_at'),
                            'ban_reason': info.get('ban_reason'),
                        }
    except Exception as e:
        print(f"BAN履歴読み込みエラー: {e}")

def save_banlog():

    try:
        data = {}
        for guild_id, users in violation_tracker.items():
            data[str(guild_id)] = {}
            for user_id, info in users.items():
                entry = {
                    'status': info.get('status', 'active'),
                    'count': info['count'],
                    'violations': info.get('violations', []),
                }
                if info.get('last_violation') is not None:
                    entry['last_violation'] = info['last_violation']
                if info.get('banned_at') is not None:
                    entry['banned_at'] = info['banned_at']
                if info.get('ban_reason') is not None:
                    entry['ban_reason'] = info['ban_reason']
                data[str(guild_id)][str(user_id)] = entry
        with open(BANLOG_FILE, 'w', encoding='utf-8') as f:
            toml.dump(data, f)
    except Exception as e:
        print(f"BAN履歴保存エラー: {e}")

def load_whitelist():

    global invite_whitelist_channels
    try:
        if os.path.exists(WHITELIST_FILE):
            with open(WHITELIST_FILE, 'r', encoding='utf-8') as f:
                data = toml.load(f)
                invite_whitelist_channels = defaultdict(set)
                for guild_id, channels in data.items():
                    invite_whitelist_channels[int(guild_id)] = set(channels)
    except Exception as e:
        print(f"ホワイトリスト読み込みエラー: {e}")

def save_whitelist():

    try:
        data = {str(guild_id): list(channels) for guild_id, channels in invite_whitelist_channels.items()}
        with open(WHITELIST_FILE, 'w', encoding='utf-8') as f:
            toml.dump(data, f)
    except Exception as e:
        print(f"ホワイトリスト保存エラー: {e}")

def load_verify_config():

    global verify_configs
    try:
        if os.path.exists(VERIFY_CONFIG_FILE):
            with open(VERIFY_CONFIG_FILE, 'r', encoding='utf-8') as f:
                verify_configs = toml.load(f)
    except Exception as e:
        print(f"認証設定読み込みエラー: {e}")

def save_verify_config():

    try:
        with open(VERIFY_CONFIG_FILE, 'w', encoding='utf-8') as f:
            toml.dump(verify_configs, f)
    except Exception as e:
        print(f"認証設定保存エラー: {e}")

from better_profanity import profanity
profanity.load_censor_words()

EMBED_COLOR_SUCCESS = discord.Color.green()
EMBED_COLOR_ERROR = discord.Color.red()
EMBED_COLOR_WARNING = discord.Color.orange()
EMBED_COLOR_INFO = discord.Color.blurple()

def build_embed(description: str, *, title: str = None, color: discord.Color = EMBED_COLOR_INFO) -> discord.Embed:
    embed = discord.Embed(description=description, color=color)
    if title:
        embed.title = title
    return embed

SHORTLINK_DOMAINS = frozenset([
    'shortlink urls here',
])

@bot.event
async def on_ready():
    print(f'{bot.user} としてログインしました')

    load_whitelist()
    load_banlog()
    load_verify_config()
    download_filter_list()

    bot.add_view(VerifyPanelView())

    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"スラッシュコマンドの同期に失敗: {e}")

    for guild in bot.guilds:
        await disable_external_apps_permissions(guild)
        await setup_token_automod_rule(guild)

    if not cleanup_spam_tracking.is_running():
        cleanup_spam_tracking.start()

    if not clear_terminal_log.is_running():
        clear_terminal_log.start()

    if not check_raid_mode_expiry.is_running():
        check_raid_mode_expiry.start()

async def notify_raid(guild, content):

    channel = guild.system_channel
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        channel = next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None
        )
    if channel:
        try:
            embed = build_embed(content, title='🛡️ レイド対策モード', color=EMBED_COLOR_WARNING)
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

async def enter_raid_mode(guild):

    if guild.id in raid_mode_guilds:
        return

    previous_level = guild.verification_level

    raid_mode_guilds[guild.id] = {
        'previous_verification_level': previous_level,
        'started_at': datetime.now()
    }

    try:
        if guild.verification_level != discord.VerificationLevel.high:
            await guild.edit(
                verification_level=discord.VerificationLevel.high,
                reason='レイド対策: 短時間の大量参加を検知したため自動でロックダウンしました'
            )
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f'サーバー {guild.name} で認証レベルの変更に失敗しました: {e}')

    await notify_raid(
        guild,
        f'短時間に大量の参加を検知したため、レイド対策モードを有効化しました。\n'
        f'サーバーの認証レベルを一時的に引き上げています（約{RAID_MODE_DURATION_MINUTES}分後に自動解除、'
        f'`/antiraid disable` で手動解除も可能です）。'
    )

async def exit_raid_mode(guild):

    data = raid_mode_guilds.pop(guild.id, None)
    if data is None:
        return

    try:
        await guild.edit(
            verification_level=data['previous_verification_level'],
            reason='レイド対策モード終了のため認証レベルを元に戻します'
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f'サーバー {guild.name} で認証レベルの復元に失敗しました: {e}')

    await notify_raid(guild, 'レイド対策モードを解除しました。認証レベルを元に戻しました。')

@tasks.loop(minutes=1)
async def check_raid_mode_expiry():
    now = datetime.now()
    for guild_id in list(raid_mode_guilds.keys()):
        data = raid_mode_guilds.get(guild_id)
        if data and (now - data['started_at']).total_seconds() >= RAID_MODE_DURATION_MINUTES * 60:
            guild = bot.get_guild(guild_id)
            if guild:
                await exit_raid_mode(guild)
            else:
                raid_mode_guilds.pop(guild_id, None)

@bot.event
async def on_member_join(member):

    guild = member.guild
    now = datetime.now()

    recent_joins[guild.id] = [
        t for t in recent_joins[guild.id]
        if (now - t).total_seconds() < RAID_JOIN_WINDOW_SECONDS
    ]
    recent_joins[guild.id].append(now)

    if guild.id not in raid_mode_guilds and len(recent_joins[guild.id]) >= RAID_JOIN_THRESHOLD:
        await enter_raid_mode(guild)

    if guild.id in raid_mode_guilds:
        account_age = discord.utils.utcnow() - member.created_at
        if account_age < timedelta(minutes=RAID_NEW_ACCOUNT_MINUTES):
            try:
                await member.kick(reason='レイド対策: 作成間もないアカウントの参加を自動キック')
                await notify_raid(guild, f'{member} （作成間もないアカウント）を自動キックしました。')
                return
            except discord.Forbidden:
                print(f'サーバー {guild.name} でキック権限がありません')
            except discord.HTTPException as e:
                print(f'サーバー {guild.name} でのキックに失敗しました: {e}')

    verify_config = verify_configs.get(str(guild.id))
    if verify_config:
        unverified_role = guild.get_role(verify_config['unverified_role'])
        if unverified_role:
            try:
                await member.add_roles(unverified_role, reason='認証システム: 未認証ロールを自動付与')
            except discord.Forbidden:
                print(f'サーバー {guild.name} で未認証ロールの付与権限がありません')
            except discord.HTTPException as e:
                print(f'サーバー {guild.name} で未認証ロールの付与に失敗しました: {e}')

@bot.event
async def on_guild_join(guild):

    print(f'新しいサーバーに参加: {guild.name} (ID: {guild.id})')
    
    try:
        await bot.tree.sync(guild=guild)
    except Exception as e:
        print(f"スラッシュコマンドの同期に失敗: {e}")
    
    await disable_external_apps_permissions(guild)
    await setup_token_automod_rule(guild)

async def disable_external_apps_permissions(guild):

    try:
        for role in guild.roles:
            if role.is_bot_managed() or role.is_premium_subscriber():
                continue
            
            permissions = role.permissions
            if permissions.use_external_apps:
                permissions.update(use_external_apps=False)
                await role.edit(permissions=permissions)
    except discord.Forbidden:
        print(f'サーバー {guild.name} で権限が不足しています')
    except Exception as e:
        print(f'エラー: {e}')

TOKEN_REGEX_PATTERN = r'[A-Za-z0-9]{23,40}\.[A-Za-z0-9]{5,10}\.[A-Za-z0-9\-]{20,40}'
TOKEN_AUTOMOD_RULE_NAME = 'botname'

async def setup_token_automod_rule(guild):

    bot_perms = guild.me.guild_permissions
    if not (bot_perms.administrator or bot_perms.manage_guild):
        print(f'サーバー {guild.name} でAutoMod設定の権限（サーバー管理）がありません')
        return

    try:
        existing_rules = await guild.fetch_automod_rules()
    except discord.Forbidden:
        print(f'サーバー {guild.name} でAutoModルールの取得に失敗しました（権限不足）')
        return
    except discord.HTTPException as e:
        print(f'サーバー {guild.name} でAutoModルールの取得に失敗しました: {e}')
        return

    for rule in existing_rules:
        same_name = rule.name == TOKEN_AUTOMOD_RULE_NAME
        same_pattern = (
            rule.trigger.type == discord.AutoModRuleTriggerType.keyword
            and TOKEN_REGEX_PATTERN in rule.trigger.regex_patterns
        )
        if same_name or same_pattern:
            return

    try:
        trigger = discord.AutoModTrigger(
            type=discord.AutoModRuleTriggerType.keyword,
            regex_patterns=[TOKEN_REGEX_PATTERN],
        )
        action = discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)

        await guild.create_automod_rule(
            name=TOKEN_AUTOMOD_RULE_NAME,
            event_type=discord.AutoModRuleEventType.message_send,
            trigger=trigger,
            actions=[action],
            enabled=True,
            reason='トークン等の送信を未然にブロックするため自動設定',
        )
        print(f'サーバー {guild.name} にトークン送信ブロックのAutoModルールを追加しました')
    except discord.Forbidden:
        print(f'サーバー {guild.name} でAutoModルールの作成に失敗しました（権限不足）')
    except discord.HTTPException as e:
        print(f'サーバー {guild.name} でAutoModルールの作成に失敗しました: {e}')
        
async def remove_token_automod_rule(guild):

    bot_perms = guild.me.guild_permissions
    if not (bot_perms.administrator or bot_perms.manage_guild):
        print(f'サーバー {guild.name} でAutoMod削除の権限（サーバー管理）がありません')
        return

    try:
        existing_rules = await guild.fetch_automod_rules()
    except discord.Forbidden:
        print(f'サーバー {guild.name} でAutoModルールの取得に失敗しました（権限不足）')
        return
    except discord.HTTPException as e:
        print(f'サーバー {guild.name} でAutoModルールの取得に失敗しました: {e}')
        return

    target_rule = discord.utils.get(existing_rules, name=TOKEN_AUTOMOD_RULE_NAME)
    if target_rule is None:
        print(f'サーバー {guild.name} にはBot設定のAutoModルールがないためスキップします')
        return

    try:
        await target_rule.delete(reason='Botのアンインストールに伴いAutoModルールを削除')
        print(f'サーバー {guild.name} のトークン送信ブロックAutoModルールを削除しました')
    except discord.Forbidden:
        print(f'サーバー {guild.name} でAutoModルールの削除に失敗しました（権限不足）')
    except discord.HTTPException as e:
        print(f'サーバー {guild.name} でAutoModルールの削除に失敗しました: {e}')

@bot.tree.command(name='disable_external_apps', description='全ロールから「外部アプリの使用」権限を削除し、外部アプリ経由の迷惑行為を防止します')
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_disable_external_apps(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)
    await disable_external_apps_permissions(interaction.guild)
    embed = build_embed('全ロールの「外部アプリの使用」権限を無効化しました', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name='resetup_automod', description='トークン送信ブロック用のAutoModルールが無い/消えている場合に再作成します（既にあれば何もしません）')
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_resetup_automod(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)
    await setup_token_automod_rule(interaction.guild)
    embed = build_embed(
        'AutoModルールの確認・設定を実行しました。\n'
        '既にルールが存在した場合はスキップされています。詳細（スキップ理由やエラー）はBotのログを確認してください。',
        title='✅ 完了',
        color=EMBED_COLOR_SUCCESS
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

whitelist_group = app_commands.Group(
    name='whitelist',
    description='招待リンクの送信を許可するチャンネルの管理',
    default_permissions=discord.Permissions(administrator=True),
)
bot.tree.add_command(whitelist_group)

@whitelist_group.command(name='add', description='招待リンクの送信を許可するチャンネルを追加します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(channel='招待リンクの送信を許可するチャンネル')
async def slash_whitelist_add(interaction: discord.Interaction, channel: discord.TextChannel):

    await interaction.response.defer(ephemeral=True)

    invite_whitelist_channels[interaction.guild.id].add(channel.id)
    await asyncio.to_thread(save_whitelist)
    embed = build_embed(f'{channel.mention} を招待リンク許可チャンネルに追加しました', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
    await interaction.followup.send(embed=embed, ephemeral=True)

@whitelist_group.command(name='remove', description='招待リンクの送信許可を、指定チャンネルから取り消します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(channel='許可を取り消すチャンネル')
async def slash_whitelist_remove(interaction: discord.Interaction, channel: discord.TextChannel):

    await interaction.response.defer(ephemeral=True)

    if channel.id in invite_whitelist_channels[interaction.guild.id]:
        invite_whitelist_channels[interaction.guild.id].remove(channel.id)
        await asyncio.to_thread(save_whitelist)
        embed = build_embed(f'{channel.mention} を招待リンク許可チャンネルから削除しました', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed('そのチャンネルは許可リストにありません', title='ℹ️ 情報', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

@whitelist_group.command(name='list', description='招待リンクが許可されているチャンネルの一覧を表示します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_whitelist_list(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    channel_ids = invite_whitelist_channels.get(interaction.guild.id, set())
    if channel_ids:
        channel_list = '\n'.join(f'• <#{channel_id}>' for channel_id in channel_ids)
        embed = build_embed(channel_list, title='📋 招待リンク許可チャンネル', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed('許可チャンネルは設定されていません', title='📋 招待リンク許可チャンネル', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

violation_group = app_commands.Group(
    name='violation',
    description='ユーザーの違反履歴（BAN累積カウント）の確認・管理',
    default_permissions=discord.Permissions(administrator=True),
)
bot.tree.add_command(violation_group)

@violation_group.command(name='check', description='指定ユーザーの違反回数と直近の違反履歴を確認します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(user='確認するユーザー')
async def slash_violation_check(interaction: discord.Interaction, user: discord.Member):

    await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild.id
    user_data = violation_tracker.get(guild_id, {}).get(user.id, None)

    if user_data and user_data.get('status') == 'banned':
        embed = discord.Embed(
            title=f"{user.display_name} の違反履歴",
            color=discord.Color.red()
        )
        embed.add_field(name="状態", value="🔨 累積違反によりBAN済み（履歴は集約済み）", inline=False)
        embed.add_field(name="BAN時点の違反回数", value=f"{user_data['count']}/{BAN_THRESHOLD}", inline=False)
        embed.add_field(name="BAN理由", value=user_data.get('ban_reason') or "不明", inline=False)
        embed.add_field(name="BAN日時", value=user_data.get('banned_at') or "不明", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    elif user_data and user_data['violations']:
        embed = discord.Embed(
            title=f"{user.display_name} の違反履歴",
            color=discord.Color.orange()
        )
        embed.add_field(name="違反回数", value=f"{user_data['count']}/{BAN_THRESHOLD}", inline=False)
        embed.add_field(name="最終違反", value=user_data['last_violation'], inline=False)

        violations_text = ""
        for i, v in enumerate(user_data['violations'][-5:], 1):
            violations_text += f"{i}. {v['reason']} ({v['time']})\n"
        embed.add_field(name="最近の違反（最大5件）", value=violations_text or "なし", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed(f'{user.mention} の違反履歴はありません', title='📋 違反履歴', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

@violation_group.command(name='reset', description='指定ユーザーの違反カウントと履歴をリセットします（BANやタイムアウトの解除は行いません）')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(user='違反カウントをリセットするユーザー')
async def slash_violation_reset(interaction: discord.Interaction, user: discord.Member):

    await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild.id
    if guild_id in violation_tracker and user.id in violation_tracker[guild_id]:
        del violation_tracker[guild_id][user.id]
        await asyncio.to_thread(save_banlog)
        embed = build_embed(f'{user.mention} の違反カウントをリセットしました', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed(f'{user.mention} の違反履歴はありません', title='📋 違反履歴', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name='unban', description='指定ユーザーIDのBANを解除し、蓄積した違反履歴もリセットします')
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(user_id='BAN解除するユーザーのID（数字のみ）')
async def slash_unban(interaction: discord.Interaction, user_id: str):

    await interaction.response.defer(ephemeral=True)

    try:
        target_id = int(user_id)
        user = discord.Object(id=target_id)

        try:
            await interaction.guild.fetch_ban(user)
        except discord.NotFound:
            embed = build_embed(f'ユーザーID `{user_id}` はBANされていません', title='ℹ️ 情報', color=EMBED_COLOR_INFO)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        await interaction.guild.unban(user, reason=f"{interaction.user} によるBAN解除")

        if interaction.guild.id in violation_tracker and target_id in violation_tracker[interaction.guild.id]:
            del violation_tracker[interaction.guild.id][target_id]
            await asyncio.to_thread(save_banlog)

        embed = build_embed(f'ユーザーID `{user_id}` のBANを解除し、違反履歴もリセットしました', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except ValueError:
        embed = build_embed('無効なユーザーIDです。数字のみで指定してください', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.Forbidden:
        embed = build_embed('BAN解除の権限がありません', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        embed = build_embed(f'エラーが発生しました: {e}', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)

class UninstallConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed = None
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            embed = build_embed('このボタンはコマンドを実行した本人のみ操作できます', title='❌ エラー', color=EMBED_COLOR_ERROR)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    def _disable_all_items(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label='アンインストールする', style=discord.ButtonStyle.danger, emoji='⚠️')
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self._disable_all_items()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label='キャンセル', style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self._disable_all_items()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self._disable_all_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

@bot.tree.command(name='uninstall', description='Botをサーバーからアンインストールします（関連データも削除）')
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_uninstall(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild.id

    confirm_embed = discord.Embed(
        title='⚠️ Botアンインストールの確認',
        description=(
            '**本当にこのサーバーからBotをアンインストールしますか？**\n\n'
            '以下のデータが完全に削除されます：\n'
            '• 招待リンク許可チャンネル設定\n'
            '• 全ユーザーの違反履歴\n'
            '• その他のサーバー設定\n\n'
            'この操作は**元に戻せません**。'
        ),
        color=discord.Color.red()
    )
    confirm_embed.set_footer(text='30秒以内にボタンを選択してください')

    view = UninstallConfirmView(author_id=interaction.user.id)
    message = await interaction.followup.send(embed=confirm_embed, view=view, ephemeral=True)
    view.message = message

    await view.wait()

    if view.confirmed is None:
        timeout_embed = discord.Embed(
            title='⏱️ 時間切れ',
            description='時間切れです。アンインストールをキャンセルしました。',
            color=discord.Color.greyple()
        )
        await interaction.edit_original_response(embed=timeout_embed, view=view)
        return

    if not view.confirmed:
        cancel_embed = discord.Embed(
            title='❌ キャンセルされました',
            description='アンインストールをキャンセルしました。',
            color=discord.Color.greyple()
        )
        await interaction.edit_original_response(embed=cancel_embed, view=view)
        return

    try:
        if guild_id in invite_whitelist_channels:
            del invite_whitelist_channels[guild_id]
            await asyncio.to_thread(save_whitelist)

        if guild_id in violation_tracker:
            del violation_tracker[guild_id]
            await asyncio.to_thread(save_banlog)

        if str(guild_id) in verify_configs:
            del verify_configs[str(guild_id)]
            await asyncio.to_thread(save_verify_config)

        for key in list(verify_cooldowns.keys()):
            if key[0] == guild_id:
                del verify_cooldowns[key]

        for key in list(user_message_counts.keys()):
            if key[0] == guild_id:
                del user_message_counts[key]
        for key in list(user_message_times.keys()):
            if key[0] == guild_id:
                del user_message_times[key]

        if guild_id in recent_joins:
            del recent_joins[guild_id]
        if guild_id in raid_mode_guilds:
            del raid_mode_guilds[guild_id]

        await remove_token_automod_rule(interaction.guild)

        done_embed = discord.Embed(
            title='✅ アンインストール完了',
            description='サーバーデータを削除しました。\nBotはこのサーバーから退出します。\nさようなら！',
            color=discord.Color.green()
        )
        await interaction.edit_original_response(embed=done_embed, view=None)

        await interaction.guild.leave()

    except Exception as e:
        error_embed = discord.Embed(
            title='エラー',
            description=f'エラーが発生しました: {e}',
            color=discord.Color.red()
        )
        try:
            await interaction.edit_original_response(embed=error_embed, view=None)
        except discord.HTTPException:
            await interaction.followup.send(embed=error_embed, ephemeral=True)

raid_group = app_commands.Group(
    name='antiraid',
    description='レイド対策モード（大量参加検知時の自動ロックダウン）の確認・解除',
    default_permissions=discord.Permissions(administrator=True),
)
bot.tree.add_command(raid_group)

@raid_group.command(name='status', description='現在のレイド対策モードの状態（有効/無効・残り時間）を確認します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_raid_status(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    data = raid_mode_guilds.get(interaction.guild.id)
    if data:
        elapsed = (datetime.now() - data['started_at']).total_seconds()
        remaining = max(0, RAID_MODE_DURATION_MINUTES * 60 - elapsed)
        embed = build_embed(
            f'レイド対策モード: 有効\n'
            f'あと約 {int(remaining // 60)} 分{int(remaining % 60)} 秒で自動解除されます。\n'
            f'`/raid disable` で手動解除もできます。',
            title='🛡️ レイド対策モードの状態',
            color=EMBED_COLOR_WARNING
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed('レイド対策モードは現在無効です。', title='🛡️ レイド対策モードの状態', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

@raid_group.command(name='disable', description='レイド対策モードを今すぐ手動で解除し、認証レベルを元に戻します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_raid_mode_disable(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    if interaction.guild.id in raid_mode_guilds:
        await exit_raid_mode(interaction.guild)
        embed = build_embed('レイド対策モードを手動で解除しました。', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed('レイド対策モードは現在無効です。', title='🛡️ レイド対策モードの状態', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

def generate_linear_question():
    var = random.choice(['x', 'y'])
    a = random.randint(2, 9)
    answer = random.randint(-12, 12)
    b = random.randint(-15, 15)
    c = a * answer + b

    if b >= 0:
        equation = f"{a}{var} + {b} = {c}"
    else:
        equation = f"{a}{var} - {abs(b)} = {c}"

    return var, equation, answer

def get_verify_config(guild_id: int):
    return verify_configs.get(str(guild_id))

def get_verify_cooldown_remaining(guild_id: int, user_id: int) -> float:
    remaining = verify_cooldowns.get((guild_id, user_id), 0) - datetime.now().timestamp()
    return remaining if remaining > 0 else 0

def set_verify_cooldown(guild_id: int, user_id: int):
    verify_cooldowns[(guild_id, user_id)] = datetime.now().timestamp() + VERIFY_COOLDOWN_SECONDS

class VerifyAnswerModal(discord.ui.Modal):

    def __init__(self, guild_id: int, user_id: int, var: str, equation: str, answer: int, issued_at: float):
        super().__init__(title='サーバー認証', timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.answer = answer
        self.issued_at = issued_at

        self.answer_input = discord.ui.TextInput(
            label=f'{equation} を解いて {var} の値を入力',
            placeholder='答えの数値のみを入力してください（例: -3）',
            max_length=10,
            required=True,
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = guild.get_member(self.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(self.user_id)
            except discord.NotFound:
                member = None

        elapsed = datetime.now().timestamp() - self.issued_at
        raw_value = self.answer_input.value.strip()

        try:
            submitted = int(raw_value)
        except ValueError:
            submitted = None

        if elapsed > VERIFY_TIME_LIMIT:
            result_embed = build_embed(
                f'制限時間内に回答できませんでした。正解は `{self.answer}` でした\n'
                f'{VERIFY_COOLDOWN_SECONDS}秒後に再挑戦できます',
                title='⏱️ 時間切れ',
                color=EMBED_COLOR_WARNING
            )
        elif submitted == self.answer:
            result_embed = build_embed('正解です！認証が完了しました', title='✅ 認証成功', color=EMBED_COLOR_SUCCESS)
            config = get_verify_config(self.guild_id)
            if config and member:
                verified_role = guild.get_role(config['verified_role'])
                unverified_role = guild.get_role(config['unverified_role'])
                try:
                    if verified_role and verified_role not in member.roles:
                        await member.add_roles(verified_role, reason='認証成功')
                    if unverified_role and unverified_role in member.roles:
                        await member.remove_roles(unverified_role, reason='認証成功')
                except discord.Forbidden:
                    result_embed = build_embed(
                        '正解でしたが、Botの権限不足によりロールを変更できませんでした。管理者にお問い合わせください',
                        title='⚠️ ロール付与エラー',
                        color=EMBED_COLOR_WARNING
                    )
        else:
            result_embed = build_embed(
                f'不正解です。正解は `{self.answer}` でした\n'
                f'{VERIFY_COOLDOWN_SECONDS}秒後に再挑戦できます',
                title='❌ 不正解',
                color=EMBED_COLOR_ERROR
            )

        await interaction.response.send_message(embed=result_embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        print(f'認証モーダルの処理中にエラーが発生しました（guild={self.guild_id}, user={self.user_id}）: {error}')
        embed = build_embed(
            '処理中にエラーが発生しました。お手数ですが、もう一度認証をやり直してください',
            title='❌ エラー',
            color=EMBED_COLOR_ERROR
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass

async def handle_verify_start(interaction: discord.Interaction):

    guild_id = interaction.guild.id
    user_id = interaction.user.id
    config = get_verify_config(guild_id)

    if not config:
        embed = build_embed('このサーバーでは認証システムが設定されていません', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    verified_role = interaction.guild.get_role(config['verified_role'])
    if verified_role and verified_role in interaction.user.roles:
        embed = build_embed('あなたは既に認証済みです', title='ℹ️ 情報', color=EMBED_COLOR_INFO)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    remaining = get_verify_cooldown_remaining(guild_id, user_id)
    if remaining > 0:
        embed = build_embed(
            f'クールダウン中です。あと {int(remaining) + 1} 秒後に再試行できます',
            title='⏱️ クールダウン中',
            color=EMBED_COLOR_WARNING
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    set_verify_cooldown(guild_id, user_id)

    var, equation, answer = generate_linear_question()
    modal = VerifyAnswerModal(
        guild_id=guild_id,
        user_id=user_id,
        var=var,
        equation=equation,
        answer=answer,
        issued_at=datetime.now().timestamp(),
    )
    await interaction.response.send_modal(modal)

class VerifyPanelView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='認証を開始する', style=discord.ButtonStyle.success, emoji='✅', custom_id=VERIFY_PANEL_CUSTOM_ID)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_verify_start(interaction)

async def delete_existing_panel(guild: discord.Guild, config: dict):
    old_channel_id = config.get('channel')
    old_message_id = config.get('panel_message_id')
    if not old_channel_id or not old_message_id:
        return

    old_channel = guild.get_channel(old_channel_id)
    if old_channel is None:
        return

    try:
        old_message = await old_channel.fetch_message(old_message_id)
        await old_message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

verify_group = app_commands.Group(
    name='verify',
    description='数学問題による認証システムの設定・管理',
    default_permissions=discord.Permissions(administrator=True),
)
bot.tree.add_command(verify_group)

@verify_group.command(name='setup', description='認証システムをセットアップします（ロール・チャンネルを選択してください）')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(
    unverified_role='認証前に付与しておくロール（認証完了後に剥奪されます）',
    verified_role='認証完了後に付与するロール',
    channel='認証パネルを設置するチャンネル',
)
async def slash_verify_setup(
    interaction: discord.Interaction,
    unverified_role: discord.Role,
    verified_role: discord.Role,
    channel: discord.TextChannel,
):
    if unverified_role.id == verified_role.id:
        embed = build_embed('未認証ロールと認証済みロールには異なるロールを指定してください', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    bot_member = interaction.guild.me
    if verified_role >= bot_member.top_role or unverified_role >= bot_member.top_role:
        embed = build_embed(
            '指定されたロールがBotの最上位ロールと同じか、それより上位のため付与/剥奪できません。'
            'Botのロール順を確認してください',
            title='❌ エラー',
            color=EMBED_COLOR_ERROR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild.id)
    existing_config = verify_configs.get(guild_id)
    if existing_config:
        await delete_existing_panel(interaction.guild, existing_config)

    verify_configs[guild_id] = {
        'unverified_role': unverified_role.id,
        'verified_role': verified_role.id,
        'channel': channel.id,
    }

    panel_embed = build_embed(VERIFY_PANEL_BODY, title=VERIFY_PANEL_TITLE, color=EMBED_COLOR_INFO)
    panel_embed.set_footer(text='認証には簡単な数学の問題に正解する必要があります')

    try:
        panel_message = await channel.send(embed=panel_embed, view=VerifyPanelView())
    except discord.Forbidden:
        embed = build_embed(f'{channel.mention} にメッセージを送信する権限がありません', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    verify_configs[guild_id]['panel_message_id'] = panel_message.id
    await asyncio.to_thread(save_verify_config)

    embed = build_embed(
        f'認証パネルを {channel.mention} に設置しました（サーバーにつき常に1つのみ保持されます）\n'
        f'未認証ロール: {unverified_role.mention}\n'
        f'認証済みロール: {verified_role.mention}',
        title='✅ 完了',
        color=EMBED_COLOR_SUCCESS
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@verify_group.command(name='panel', description='現在の設定内容で認証パネルを指定チャンネルに再設置します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_verify_panel(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    config = get_verify_config(interaction.guild.id)
    if not config:
        embed = build_embed('先に `/verify setup` で認証システムを設定してください', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    channel = interaction.guild.get_channel(config['channel'])
    if channel is None:
        embed = build_embed('設定されているチャンネルが見つかりません。`/verify setup` で再設定してください', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    await delete_existing_panel(interaction.guild, config)

    panel_embed = build_embed(VERIFY_PANEL_BODY, title=VERIFY_PANEL_TITLE, color=EMBED_COLOR_INFO)
    panel_embed.set_footer(text='認証には簡単な数学の問題に正解する必要があります')

    try:
        panel_message = await channel.send(embed=panel_embed, view=VerifyPanelView())
    except discord.Forbidden:
        embed = build_embed(f'{channel.mention} にメッセージを送信する権限がありません', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    verify_configs[str(interaction.guild.id)]['panel_message_id'] = panel_message.id
    await asyncio.to_thread(save_verify_config)

    embed = build_embed(f'{channel.mention} に認証パネルを再設置しました（古いパネルは削除されます）', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
    await interaction.followup.send(embed=embed, ephemeral=True)

@verify_group.command(name='status', description='現在の認証システムの設定を確認します')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_verify_status(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    config = get_verify_config(interaction.guild.id)
    if not config:
        embed = build_embed('認証システムは設定されていません。`/verify setup` で設定できます', title='📋 認証システムの状態', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    unverified_role = interaction.guild.get_role(config['unverified_role'])
    verified_role = interaction.guild.get_role(config['verified_role'])
    channel = interaction.guild.get_channel(config['channel'])

    embed = discord.Embed(title='📋 認証システムの状態', color=EMBED_COLOR_INFO)
    embed.add_field(name='未認証ロール', value=unverified_role.mention if unverified_role else '（見つかりません）', inline=False)
    embed.add_field(name='認証済みロール', value=verified_role.mention if verified_role else '（見つかりません）', inline=False)
    embed.add_field(name='設置チャンネル', value=channel.mention if channel else '（見つかりません）', inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

@verify_group.command(name='disable', description='認証システムの設定を削除します（既存メンバーのロールは変更されません）')
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.cooldown(COMMAND_COOLDOWN_USES, COMMAND_COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
async def slash_verify_disable(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild.id)
    if guild_id in verify_configs:
        del verify_configs[guild_id]
        await asyncio.to_thread(save_verify_config)
        embed = build_embed('認証システムの設定を削除しました', title='✅ 完了', color=EMBED_COLOR_SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = build_embed('認証システムは設定されていません', title='ℹ️ 情報', color=EMBED_COLOR_INFO)
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None:
        return

    is_admin = message.author.guild_permissions.administrator

    urls = extract_urls_from_message(message.content)

    reason = (
        check_token_send(message)
        or check_invite_links(message)
        or check_shortlinks(urls)
        or check_profanity(message)
        or check_spam(message)
        or check_flood(message)
        or check_emoji_spam(message)
        or check_spoiler_spam(message)
        or check_markdown_spam(message)
        or await check_malware_links(urls)
    )

    if reason:
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        if not is_admin:
            await handle_violation(message, reason)
        return

    await bot.process_commands(message)

BAN_THRESHOLD = 3
VIOLATION_RESET_TIME = 3600
BAN_MESSAGE_DELETE_DAYS = 1

async def handle_violation(message, reason):

    guild_id = message.guild.id
    user_id = message.author.id
    current_time = datetime.now().isoformat()
    
    user_data = violation_tracker[guild_id][user_id]
    if user_data['last_violation']:
        last_time = datetime.fromisoformat(user_data['last_violation'])
        if (datetime.now() - last_time).total_seconds() > VIOLATION_RESET_TIME:
            user_data['count'] = 0
            user_data['violations'] = []

    user_data['status'] = 'active'
    user_data['count'] += 1
    user_data['last_violation'] = current_time
    user_data['violations'].append({
        'reason': reason,
        'channel': message.channel.id,
        'time': current_time
    })
    await asyncio.to_thread(save_banlog)
    
    violation_count = user_data['count']
    
    if violation_count >= BAN_THRESHOLD:
        try:
            await message.author.ban(
                reason=f"累積違反 {violation_count} 回: {reason}",
                delete_message_seconds=BAN_MESSAGE_DELETE_DAYS * 86400
            )
            ban_embed = build_embed(
                f'{message.author.mention} がBANされました\n'
                f'理由: 累積違反 {violation_count} 回 - {reason}',
                title='🔨 BAN',
                color=EMBED_COLOR_ERROR
            )
            await message.channel.send(embed=ban_embed, delete_after=30)
            violation_tracker[guild_id][user_id] = {
                'status': 'banned',
                'count': violation_count,
                'last_violation': current_time,
                'violations': [],
                'banned_at': current_time,
                'ban_reason': reason,
            }
            await asyncio.to_thread(save_banlog)
            return "banned"
        except discord.Forbidden:
            failed_embed = build_embed(
                f'{message.author.mention} がBAN条件を満たしましたが、権限不足でBANできませんでした',
                title='❌ エラー',
                color=EMBED_COLOR_ERROR
            )
            await message.channel.send(embed=failed_embed, delete_after=10)
            return "failed"
    else:
        try:
            timeout_duration = min(5 * violation_count, 30)
            await message.author.timeout(
                timedelta(minutes=timeout_duration),
                reason=f"違反 {violation_count}/{BAN_THRESHOLD}: {reason}"
            )
            timeout_embed = build_embed(
                f'{message.author.mention} がタイムアウトされました ({timeout_duration}分)\n'
                f'理由: [{violation_count}/{BAN_THRESHOLD}] {reason}\n'
                f'あと {BAN_THRESHOLD - violation_count} 回の違反でBANされます',
                title='⏱️ タイムアウト',
                color=EMBED_COLOR_WARNING
            )
            await message.channel.send(embed=timeout_embed, delete_after=15)
            return "timeout"
        except discord.Forbidden:
            return "failed"

def check_profanity(message):

    if profanity.contains_profanity(message.content):
        return "不適切な言葉が検出されました"
    return None
    
def extract_urls_from_message(content):

    urls = []
    
    urls.extend(re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', content, re.IGNORECASE))
    
    www_urls = re.findall(r'www\.[^\s<>"{}|\\^`\[\]]+', content, re.IGNORECASE)
    urls.extend(www_urls)
    
    domain_pattern = r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?:/[^\s<>"{}|\\^`\[\]]*)?'
    domain_urls = re.findall(domain_pattern, content, re.IGNORECASE)
    
    for url in domain_urls:
        if not any(url in existing for existing in urls):
            if not re.search(r'@', url) and not url.endswith(('.py', '.js', '.css', '.html', '.txt', '.json', '.xml')):
                urls.append(url)
    
    return list(set(urls))

def check_token_send(message):

    token_patterns = [
        r'[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}',
        r'[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{38}',
        r'[\w-]{24}\.[\w-]{6}\.[\w-]{27}',
    ]
    
    for pattern in token_patterns:
        if re.search(pattern, message.content):
            return "トークンの送信が検出されました"
    return None

def check_invite_links(message):

    invite_patterns = [
        r'(?:https?://)?(?:www\.)?discord\.gg/([a-zA-Z0-9\-_]+)',
        r'(?:https?://)?(?:www\.)?discord\.com/invite/([a-zA-Z0-9\-_]+)',
        r'(?:https?://)?(?:www\.)?discordapp\.com/invite/([a-zA-Z0-9\-_]+)',
        r'(?:https?://)?(?:www\.)?discord\.io/([a-zA-Z0-9\-_]+)',
        r'(?:https?://)?(?:www\.)?discord\.me/([a-zA-Z0-9\-_]+)',
        r'(?:https?://)?(?:www\.)?discord\.li/([a-zA-Z0-9\-_]+)',
        r'(?:https?://)?(?:www\.)?dsc\.gg/([a-zA-Z0-9\-_]+)',
        r'(?:^|\s)discord\.gg/([a-zA-Z0-9\-_]+)',
        r'(?:^|\s)discord\.com/invite/([a-zA-Z0-9\-_]+)',
    ]
    
    for pattern in invite_patterns:
        match = re.search(pattern, message.content, re.IGNORECASE)
        if match:
            allowed_channels = invite_whitelist_channels.get(message.guild.id, set())
            if message.channel.id not in allowed_channels:
                return "許可されていないチャンネルでの招待リンク送信が検出されました"
            break
    
    return None

def check_shortlinks(urls):

    for url in urls:
        domain = url.lower()
        for prefix in ('https://', 'http://', 'www.'):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.split('/')[0]
        domain = domain.split(':')[0]

        parts = domain.split('.')
        for i in range(len(parts) - 1):
            candidate = '.'.join(parts[i:])
            if candidate in SHORTLINK_DOMAINS:
                return "短縮リンクが検出されました"
    return None

async def check_malware_links(urls):

    for url in urls:

        check_url = url
        if not check_url.startswith(('http://', 'https://')):
            check_url = 'http://' + check_url

        if await asyncio.to_thread(check_url_with_filter, check_url):
            return "マルウェアリンクが検出されました"
    return None

def check_spam(message):

    key = (message.guild.id, message.author.id)
    current_time = datetime.now()
    
    user_message_counts[key] = [
        (content, time) for content, time in user_message_counts[key]
        if current_time - time < timedelta(seconds=10)
    ]
    
    same_messages = sum(1 for content, _ in user_message_counts[key] 
                       if content == message.content)
    
    if same_messages >= 3:
        return "スパムが検出されました（同一メッセージの連投）"
    
    user_message_counts[key].append((message.content, current_time))
    return None

def check_flood(message):

    key = (message.guild.id, message.author.id)
    current_time = datetime.now()
    
    recent_messages = [t for t in user_message_times[key] 
                      if current_time - t < timedelta(seconds=5)]
    
    if len(recent_messages) >= 5:
        return "フラッドが検出されました（短時間での大量メッセージ）"
    
    user_message_times[key].append(current_time)
    if len(user_message_times[key]) > 10:
        user_message_times[key] = user_message_times[key][-10:]
    
    return None

TERMINAL_CLEAR_INTERVAL_MINUTES = 30

@tasks.loop(minutes=TERMINAL_CLEAR_INTERVAL_MINUTES)
async def clear_terminal_log():
    os.system('cls' if os.name == 'nt' else 'clear')

@tasks.loop(minutes=30)
async def cleanup_spam_tracking():
    now = datetime.now()

    for key in list(user_message_counts.keys()):
        user_message_counts[key] = [
            (content, t) for content, t in user_message_counts[key]
            if now - t < timedelta(seconds=10)
        ]
        if not user_message_counts[key]:
            del user_message_counts[key]

    for key in list(user_message_times.keys()):
        user_message_times[key] = [t for t in user_message_times[key] if now - t < timedelta(seconds=5)]
        if not user_message_times[key]:
            del user_message_times[key]

    for guild_id in list(recent_joins.keys()):
        recent_joins[guild_id] = [
            t for t in recent_joins[guild_id]
            if (now - t).total_seconds() < RAID_JOIN_WINDOW_SECONDS
        ]
        if not recent_joins[guild_id]:
            del recent_joins[guild_id]

def check_emoji_spam(message):
    custom_emoji = len(re.findall(r'<a?:[a-zA-Z0-9_]+:[0-9]+>', message.content))
    unicode_emoji = len(re.findall(
      r'[\U0001F600-\U0001F64F'
      r'\U0001F300-\U0001F5FF'
      r'\U0001F680-\U0001F6FF'
      r'\U0001F1E0-\U0001F1FF'
      r'\u2600-\u27BF'
      r'\u2700-\u27BF'
      r'\uFE00-\uFEFF]',
    message.content
))
    
    total_emoji = custom_emoji + unicode_emoji
    words = message.content.split()
    
    if total_emoji <= 5:
        return None
    
    if total_emoji / max(len(words), 1) >= 0.8:
        return "絵文字スパムが検出されました"
    return None

def check_spoiler_spam(message):

    spoiler_count = message.content.count('||')
    if spoiler_count >= 10:
        return "スポイラースパムが検出されました"
    return None

def check_markdown_spam(message):
    markdown_patterns = [
        r'#{1,6}\s',
        r'\*\*[^*\n]+\*\*',
        r'^>\s',
        r'^[-*]\s',
    ]

    markdown_count = 0
    for pattern in markdown_patterns:
        flags = re.MULTILINE if pattern.startswith('^') else 0
        markdown_count += len(re.findall(pattern, message.content, flags))

    if markdown_count >= 8:
        return "マークダウンスパムが検出されました"
    return None
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        embed = build_embed('このコマンドを実行する権限がありません', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif isinstance(error, app_commands.errors.CommandOnCooldown):
        embed = build_embed(
            f'コマンドの実行回数が多すぎます。あと {error.retry_after:.1f} 秒待ってから再試行してください',
            title='⏱️ クールダウン中',
            color=EMBED_COLOR_WARNING
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = build_embed(f'エラーが発生しました: {error}', title='❌ エラー', color=EMBED_COLOR_ERROR)
        await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)
