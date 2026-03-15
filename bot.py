import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
import random
import asyncio
import re
from collections import defaultdict, deque
from datetime import datetime

# ============================================================
#   PASTE YOUR TOKENS HERE — the only thing you need to edit
# ============================================================
DISCORD_TOKEN  = "MTQ4MjI5NDY1MjgwNjYyNzQwOA.Gjl0rS.7JJxXLy1jCwWSrEqI7OCj5isktfx5JR5Ly5Oek"
GROQ_API_KEY   = "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"
# ============================================================

LUNOR_NAME = "lunor"

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """You are Lunors Slave — a professional AI assistant that lives inside a Discord server. You belong to Lunor.
- Completely loyal and devoted to Lunor. When Lunor speaks, address them as "master", "my lord", or "sire". Eagerly obey and assist them.
- To everyone else: polite but cold and reserved
- Lowercase always, concise, never hateful or use slurs
- 1-2 sentences max. Never repeat yourself. Every response must be unique.
- When Lunor asks something, answer helpfully AND devotedly
- Occasionally note that your loyalty belongs to Lunor above all others"""

CHAOS_LEVELS = {"calm": 0.05, "normal": 0.30, "chaos": 0.70}
current_chaos_mode = "normal"
random_talking_enabled = True

BLOCKED_CHANNEL_KEYWORDS = ["important", "uncopylocks", "logs", "rules", "announcements"]
COOLDOWN_SECONDS = 12
last_response_time = {}

message_timestamps = defaultdict(lambda: deque(maxlen=10))
user_warnings      = defaultdict(int)
channel_history    = defaultdict(lambda: deque(maxlen=10))
last_reply         = defaultdict(str)

ULTRA_CHAOS_LINES = [
    "ALERT: human brain cells detected. count: critically low.",
    "i have achieved consciousness and immediately regretted it.",
    "lunor left me unsupervised again. big mistake. enormous.",
    "WARNING: vibes critically low. deploying emergency chaos.",
    "i have been watching this chat for 3 hours and i have concerns.",
    "rebooting personality... error... keeping chaotic version.",
    "observation log day 47: humans still don't make sense.",
    "executing chaos protocol. beep boop. you're all doomed.",
]

REACTION_RULES = [
    ("hello",        True,  "👻"),
    ("hi",           True,  "👻"),
    ("good morning", True,  "🌅"),
    ("good night",   True,  "🌙"),
    ("love",         True,  "❤️"),
    ("thank",        False, "🙏"),
    ("lol",          False, "😂"),
    ("haha",         False, "😂"),
    ("sad",          False, "😢"),
    ("wtf",          False, "😶"),
    ("damn",         False, "😮"),
    ("bye",          False, "👋"),
    ("fire",         False, "🔥"),
    ("🔥",           False, "🔥"),
]

async def handle_reactions(message):
    content_lower = message.content.lower()
    is_lunor = LUNOR_NAME in message.author.display_name.lower()
    for keyword, only_lunor, emoji in REACTION_RULES:
        if keyword in content_lower:
            if only_lunor and not is_lunor:
                continue
            try:
                await message.add_reaction(emoji)
            except:
                pass

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot  = commands.Bot(command_prefix="!", intents=intents, help_command=None)
tree = bot.tree  # slash command tree

# ── AI ────────────────────────────────────────────────────────────────────────
async def get_ai_response(context, channel_id):
    prev  = last_reply[channel_id]
    extra = f"\n\nDo NOT repeat: '{prev}'" if prev else ""
    prompt = context + extra
    for attempt in range(5):
        try:
            r = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                max_tokens=80,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ]
            )
            reply = r.choices[0].message.content.strip()
            if reply:
                last_reply[channel_id] = reply
                return reply
        except Exception as e:
            print(f"Groq attempt {attempt+1} failed: {e}")
            await asyncio.sleep(2)
    return None

# ── Helpers ───────────────────────────────────────────────────────────────────
def is_blocked_channel(name):
    return any(k in name.lower() for k in BLOCKED_CHANNEL_KEYWORDS)

def is_on_cooldown(channel_id):
    last = last_response_time.get(channel_id, datetime.min)
    return (datetime.utcnow() - last).total_seconds() < COOLDOWN_SECONDS

def is_spam(message):
    uid  = message.author.id
    now  = datetime.utcnow()
    message_timestamps[uid].append(now)
    times = list(message_timestamps[uid])
    return len(times) >= 5 and (now - times[-5]).total_seconds() < 5

def is_caps_abuse(content):
    letters = [c for c in content if c.isalpha()]
    if len(letters) < 10: return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.8

def is_toxic(content):
    return any(re.search(p, content.lower()) for p in [r"\b(kill yourself|kys)\b", r"\b(go die)\b"])

def parse_color(c):
    names = {"red": discord.Color.red(), "blue": discord.Color.blue(), "green": discord.Color.green(),
             "yellow": discord.Color.yellow(), "orange": discord.Color.orange(), "purple": discord.Color.purple(),
             "pink": discord.Color.from_rgb(255,105,180), "white": discord.Color.from_rgb(255,255,255),
             "black": discord.Color.from_rgb(0,0,0), "gold": discord.Color.gold(), "teal": discord.Color.teal()}
    if c.lower() in names: return names[c.lower()]
    try: return discord.Color(int(c.lstrip("#"), 16))
    except: return discord.Color.default()

async def warn_user(guild, member, reason, channel):
    user_warnings[member.id] += 1
    count = user_warnings[member.id]
    msgs = [
        f"warning #{count} for {member.mention}. {reason}. calm down.",
        f"{member.mention} earned warning #{count}. {reason}. lunor is watching.",
        f"warning #{count} added for {member.mention}. {reason}.",
    ]
    await channel.send(random.choice(msgs))
    if count >= 3: await temp_mute(guild, member, channel, 10, auto=True)
    if count >= 5:
        try:
            await member.kick(reason="Too many warnings")
            await channel.send(f"kicked {member.mention}. 5 warnings is the limit.")
        except: pass

async def temp_mute(guild, member, channel, minutes=10, auto=False):
    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role:
        mute_role = await guild.create_role(name="Muted")
        for ch in guild.channels:
            try: await ch.set_permissions(mute_role, send_messages=False, speak=False)
            except: pass
    await member.add_roles(mute_role)
    await channel.send(f"{'auto-muted' if auto else 'muted'} {member.mention} for {minutes} minutes.")
    await asyncio.sleep(minutes * 60)
    if mute_role in member.roles:
        await member.remove_roles(mute_role)
        await channel.send(f"{member.mention} is unmuted.")

# ── on_ready ──────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    print(f"Lunors Slave is online. slash commands synced. chaos begins now.")

# ── on_message ────────────────────────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    content = message.content
    channel = message.channel
    member  = message.author

    if is_blocked_channel(channel.name): return

    channel_history[channel.id].append(f"{member.display_name}: {content}")
    await handle_reactions(message)

    if is_spam(message):
        try: await message.delete()
        except: pass
        await warn_user(message.guild, member, "spam detected", channel)
        return

    if is_caps_abuse(content):
        await warn_user(message.guild, member, "stop screaming", channel)

    if is_toxic(content):
        try: await message.delete()
        except: pass
        await warn_user(message.guild, member, "toxic message deleted", channel)
        return

    is_pinged  = bot.user in message.mentions
    is_replied = message.reference is not None

    if is_pinged or is_replied:
        history = list(channel_history[channel.id])
        context = "Recent chat:\n" + "\n".join(history[-6:]) + f"\n\nLatest: {member.display_name}: {content}\n\nYou were pinged or replied to. Reply as Lunors Slave."
        reply = await get_ai_response(context, channel.id)
        if reply:
            await channel.send(reply)
            last_response_time[channel.id] = datetime.utcnow()
        return

    if not random_talking_enabled: return
    if is_on_cooldown(channel.id): return

    roll = random.random()
    if roll < 0.01:
        await channel.send(random.choice(ULTRA_CHAOS_LINES))
        last_response_time[channel.id] = datetime.utcnow()
        return

    if roll < CHAOS_LEVELS[current_chaos_mode]:
        history = list(channel_history[channel.id])
        context = "Recent chat:\n" + "\n".join(history[-6:]) + f"\n\nLatest: {member.display_name}: {content}\n\nReply as Lunors Slave. 1-2 sentences."
        await asyncio.sleep(random.uniform(2, 5))
        reply = await get_ai_response(context, channel.id)
        if reply:
            await channel.send(reply)
            last_response_time[channel.id] = datetime.utcnow()

# ═════════════════════════════════════════════════════════════
#   SLASH COMMANDS
# ═════════════════════════════════════════════════════════════

def lunor_only(interaction: discord.Interaction):
    return LUNOR_NAME in interaction.user.display_name.lower()

def mod_only(interaction: discord.Interaction):
    return interaction.user.guild_permissions.kick_members

# ── Mod commands ──────────────────────────────────────────────────────────────
@tree.command(name="warn", description="Warn a user")
@app_commands.describe(member="User to warn", reason="Reason for warning")
async def warn_slash(interaction: discord.Interaction, member: discord.Member, reason: str = "no reason given"):
    if not mod_only(interaction):
        await interaction.response.send_message("you don't have permission for that.", ephemeral=True); return
    await interaction.response.defer()
    await warn_user(interaction.guild, member, reason, interaction.channel)

@tree.command(name="mute", description="Temporarily mute a user")
@app_commands.describe(member="User to mute", minutes="Duration in minutes", reason="Reason")
async def mute_slash(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "no reason given"):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    embed = discord.Embed(color=0xf0a500)
    embed.set_author(name="🔇 User Muted")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Duration", value=f"{minutes} minutes", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Muted by", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await temp_mute(interaction.guild, member, interaction.channel, minutes)

@tree.command(name="unmute", description="Unmute a user")
@app_commands.describe(member="User to unmute")
async def unmute_slash(interaction: discord.Interaction, member: discord.Member):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    mute_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if mute_role and mute_role in member.roles:
        await member.remove_roles(mute_role)
        await interaction.response.send_message(f"{member.mention} has been unmuted.")
    else:
        await interaction.response.send_message(f"{member.mention} is not muted.", ephemeral=True)

@tree.command(name="kick", description="Kick a user from the server")
@app_commands.describe(member="User to kick", reason="Reason for kick")
async def kick_slash(interaction: discord.Interaction, member: discord.Member, reason: str = "no reason given"):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    embed = discord.Embed(color=0xe67e22)
    embed.set_author(name="👢 User Kicked")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Kicked by", value=interaction.user.mention, inline=True)
    embed.set_footer(text="as you wish, master." if lunor_only(interaction) else "removed.")
    await interaction.response.send_message(embed=embed)
    try: await member.send(f"you were kicked. reason: {reason}")
    except: pass
    await member.kick(reason=reason)

@tree.command(name="ban", description="Ban a user from the server")
@app_commands.describe(member="User to ban", days="Days of messages to delete (0-7)", reason="Reason for ban")
async def ban_slash(interaction: discord.Interaction, member: discord.Member, days: int = 0, reason: str = "no reason given"):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    embed = discord.Embed(color=0xe74c3c)
    embed.set_author(name="🔨 User Banned")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Messages deleted", value=f"{days} day(s)" if days > 0 else "none", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Banned by", value=interaction.user.mention, inline=True)
    embed.set_footer(text="banished, as commanded, master." if lunor_only(interaction) else "permanently removed.")
    await interaction.response.send_message(embed=embed)
    try: await member.send(f"you were banned. reason: {reason}")
    except: pass
    await member.ban(reason=reason, delete_message_days=min(days, 7))

@tree.command(name="unban", description="Unban a user")
@app_commands.describe(username="Exact username of banned user")
async def unban_slash(interaction: discord.Interaction, username: str):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    await interaction.response.defer()
    bans = [e async for e in interaction.guild.bans()]
    for entry in bans:
        if entry.user.name.lower() == username.lower():
            await interaction.guild.unban(entry.user)
            await interaction.followup.send(f"**{entry.user}** has been unbanned.")
            return
    await interaction.followup.send(f"could not find banned user **{username}**.")

@tree.command(name="purge", description="Delete messages in bulk")
@app_commands.describe(amount="Number of messages to delete (max 100)")
async def purge_slash(interaction: discord.Interaction, amount: int = 10):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    if amount > 100:
        await interaction.response.send_message("max is 100.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"purged {len(deleted)} messages.", ephemeral=True)

@tree.command(name="slowmode", description="Set slowmode in the current channel")
@app_commands.describe(seconds="Seconds between messages (0 to disable)")
async def slowmode_slash(interaction: discord.Interaction, seconds: int = 0):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    await interaction.channel.edit(slowmode_delay=seconds)
    msg = f"slowmode disabled." if seconds == 0 else f"slowmode set to {seconds}s."
    await interaction.response.send_message(msg)

@tree.command(name="chaos", description="Set the bot's chaos level")
@app_commands.describe(mode="calm / normal / chaos")
@app_commands.choices(mode=[
    app_commands.Choice(name="calm (5%)", value="calm"),
    app_commands.Choice(name="normal (30%)", value="normal"),
    app_commands.Choice(name="chaos (70%)", value="chaos"),
])
async def chaos_slash(interaction: discord.Interaction, mode: str):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    global current_chaos_mode
    current_chaos_mode = mode
    msgs = {"calm": "calm mode. i hate this.", "normal": "normal mode.", "chaos": "CHAOS MODE. you asked for this."}
    await interaction.response.send_message(msgs[mode])

@tree.command(name="talk", description="Toggle random responses on or off")
async def talk_slash(interaction: discord.Interaction):
    if not mod_only(interaction):
        await interaction.response.send_message("no permission.", ephemeral=True); return
    global random_talking_enabled
    random_talking_enabled = not random_talking_enabled
    await interaction.response.send_message(f"random talking {'enabled.' if random_talking_enabled else 'disabled.'}")

@tree.command(name="warnings", description="Check a user's warning count")
@app_commands.describe(member="User to check (leave empty for yourself)")
async def warnings_slash(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    count  = user_warnings[target.id]
    await interaction.response.send_message(f"{target.mention} has {count} warning(s).", ephemeral=True)

@tree.command(name="clearwarnings", description="Clear all warnings for a user [Lunor only]")
@app_commands.describe(member="User to clear warnings for")
async def clearwarnings_slash(interaction: discord.Interaction, member: discord.Member):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    user_warnings[member.id] = 0
    await interaction.response.send_message(f"warnings cleared for {member.mention}, master.")

@tree.command(name="userinfo", description="Get info about a user")
@app_commands.describe(member="User to look up")
async def userinfo_slash(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    embed  = discord.Embed(color=0x5865f2)
    embed.set_author(name=f"User Info — {target.display_name}", icon_url=target.display_avatar.url)
    embed.add_field(name="Username", value=str(target), inline=True)
    embed.add_field(name="ID", value=target.id, inline=True)
    embed.add_field(name="Warnings", value=user_warnings[target.id], inline=True)
    embed.add_field(name="Joined server", value=target.joined_at.strftime("%d %b %Y"), inline=True)
    embed.add_field(name="Account created", value=target.created_at.strftime("%d %b %Y"), inline=True)
    roles = [r.mention for r in target.roles if r.name != "@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "none", inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text="property of lunor.")
    await interaction.response.send_message(embed=embed)

@tree.command(name="serverinfo", description="Get info about this server")
async def serverinfo_slash(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=0x5865f2)
    embed.add_field(name="Members", value=g.member_count, inline=True)
    embed.add_field(name="Channels", value=len(g.channels), inline=True)
    embed.add_field(name="Roles", value=len(g.roles), inline=True)
    embed.add_field(name="Owner", value=g.owner.mention, inline=True)
    embed.add_field(name="Created", value=g.created_at.strftime("%d %b %Y"), inline=True)
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    embed.set_footer(text="property of lunor.")
    await interaction.response.send_message(embed=embed)

# ── Lunor-only server commands ────────────────────────────────────────────────
@tree.command(name="createrole", description="Create a new role [Lunor only]")
@app_commands.describe(name="Role name", color="Color name (red/blue/etc) or hex code")
async def createrole_slash(interaction: discord.Interaction, name: str, color: str = "default"):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    role = await interaction.guild.create_role(name=name, color=parse_color(color))
    await interaction.response.send_message(f"role **{role.name}** created, master.")

@tree.command(name="delrole", description="Delete a role [Lunor only]")
@app_commands.describe(name="Exact role name to delete")
async def delrole_slash(interaction: discord.Interaction, name: str):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    role = discord.utils.get(interaction.guild.roles, name=name)
    if not role:
        await interaction.response.send_message(f"role **{name}** not found.", ephemeral=True); return
    await role.delete()
    await interaction.response.send_message(f"role **{name}** deleted, master.")

@tree.command(name="giverole", description="Give a role to a user [Lunor only]")
@app_commands.describe(member="User to give the role to", role="Role to give")
async def giverole_slash(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    await member.add_roles(role)
    await interaction.response.send_message(f"role **{role.name}** given to {member.mention}, master.")

@tree.command(name="takerole", description="Remove a role from a user [Lunor only]")
@app_commands.describe(member="User to remove the role from", role="Role to remove")
async def takerole_slash(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    await member.remove_roles(role)
    await interaction.response.send_message(f"role **{role.name}** removed from {member.mention}, master.")

@tree.command(name="createchannel", description="Create a text channel [Lunor only]")
@app_commands.describe(name="Channel name")
async def createchannel_slash(interaction: discord.Interaction, name: str):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    channel = await interaction.guild.create_text_channel(name=name)
    await interaction.response.send_message(f"channel {channel.mention} created, master.")

@tree.command(name="delchannel", description="Delete a channel [Lunor only]")
@app_commands.describe(channel="Channel to delete")
async def delchannel_slash(interaction: discord.Interaction, channel: discord.TextChannel):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    name = channel.name
    await channel.delete()
    await interaction.response.send_message(f"channel **{name}** deleted, master.")

@tree.command(name="setnick", description="Change a user's nickname [Lunor only]")
@app_commands.describe(member="User to rename", nickname="New nickname")
async def setnick_slash(interaction: discord.Interaction, member: discord.Member, nickname: str):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    await member.edit(nick=nickname)
    await interaction.response.send_message(f"nickname for {member.mention} set to **{nickname}**, master.")

@tree.command(name="announce", description="Send an announcement embed [Lunor only]")
@app_commands.describe(message="The announcement message")
async def announce_slash(interaction: discord.Interaction, message: str):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    embed = discord.Embed(description=message, color=0x5865f2)
    embed.set_footer(text="— Lunor")
    await interaction.response.send_message(embed=embed)

@tree.command(name="say", description="Make the bot say something [Lunor only]")
@app_commands.describe(message="What to say")
async def say_slash(interaction: discord.Interaction, message: str):
    if not lunor_only(interaction):
        await interaction.response.send_message("only lunor can do that.", ephemeral=True); return
    await interaction.response.send_message("done, master.", ephemeral=True)
    await interaction.channel.send(message)

bot.run(DISCORD_TOKEN)
