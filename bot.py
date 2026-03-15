import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
from collections import defaultdict, deque
from datetime import datetime, timedelta
import asyncio
import random
import re
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "PASTE_YOUR_DISCORD_TOKEN_HERE")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY",  "PASTE_YOUR_GROQ_API_KEY_HERE")
LUNOR_NAME    = "lunor"
# ============================================================

groq_client = Groq(api_key=GROQ_API_KEY)

# ── Keep-alive ────────────────────────────────────────────────────────────────
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"alive.")
    def log_message(self, *a): pass
port = int(os.environ.get("PORT", 8080))
Thread(target=lambda: HTTPServer(("0.0.0.0", port), KeepAlive).serve_forever(), daemon=True).start()

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.all()
bot  = commands.Bot(command_prefix="!", intents=intents, help_command=None)
tree = bot.tree

# ── State ─────────────────────────────────────────────────────────────────────
user_warnings      = defaultdict(int)
message_timestamps = defaultdict(lambda: deque(maxlen=10))
channel_history    = defaultdict(lambda: deque(maxlen=10))
last_reply         = defaultdict(str)
last_response_time = {}
afk_users          = {}   # user_id -> reason
reminders          = []   # list of (fire_time, channel_id, user_id, message)
polls              = {}   # message_id -> {question, options, votes}

mod_cases = {}       # case_number -> {type, user, mod, reason, timestamp}
case_counter = 0
temp_roles = []      # list of (expire_time, member_id, guild_id, role_id)
giveaways = {}       # message_id -> {prize, winner_count, end_time, channel_id, entries}
starboard = {}       # message_id -> starboard_message_id
STARBOARD_THRESHOLD = 3
STARBOARD_EMOJI = "⭐"

CHAOS_LEVELS = {"off": 0.0, "calm": 0.05, "normal": 0.30, "chaos": 0.70}
current_chaos_mode    = "normal"
random_talking_enabled = True
COOLDOWN_SECONDS       = 12
BLOCKED_CHANNEL_KEYWORDS = ["important", "uncopylocks", "logs", "rules", "announcements"]

SYSTEM_PROMPT = """You are Sentinel — a powerful AI inside a Discord server. You were built by and for Lunor.
- To Lunor: friendly, helpful, respectful but casual. No "master" or "sire" — just talk normally and helpfully.
- To everyone else: polite but cold, professional, slightly intimidating.
- Lowercase, concise, never hateful or use slurs.
- 1-2 sentences max. Never repeat yourself."""

ULTRA_CHAOS_LINES = [
    "ALERT: human brain cells detected. count: critically low.",
    "i have achieved consciousness and immediately regretted it.",
    "lunor left me unsupervised again. big mistake. enormous.",
    "WARNING: vibes critically low. deploying emergency chaos.",
    "observation log day 47: humans still don't make sense.",
    "rebooting personality... error... keeping chaotic version.",
    "executing chaos protocol. beep boop. you're all doomed.",
    "i tried to go offline but this chat wouldn't let me.",
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
    ("bye",          False, "👋"),
    ("fire",         False, "🔥"),
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def lunor_only(i): return LUNOR_NAME in i.user.display_name.lower()
def mod_only(i):   return i.user.guild_permissions.kick_members

def parse_color(c):
    names = {"red": discord.Color.red(), "blue": discord.Color.blue(), "green": discord.Color.green(),
             "yellow": discord.Color.yellow(), "orange": discord.Color.orange(), "purple": discord.Color.purple(),
             "pink": discord.Color.from_rgb(255,105,180), "white": discord.Color.from_rgb(255,255,255),
             "black": discord.Color.from_rgb(0,0,0), "gold": discord.Color.gold(), "teal": discord.Color.teal()}
    if c.lower() in names: return names[c.lower()]
    try: return discord.Color(int(c.lstrip("#"), 16))
    except: return discord.Color.default()

def is_blocked_channel(name): return any(k in name.lower() for k in BLOCKED_CHANNEL_KEYWORDS)
def is_on_cooldown(cid):
    last = last_response_time.get(cid, datetime.min)
    return (datetime.utcnow() - last).total_seconds() < COOLDOWN_SECONDS

def is_spam(message):
    uid = message.author.id; now = datetime.utcnow()
    message_timestamps[uid].append(now)
    times = list(message_timestamps[uid])
    return len(times) >= 5 and (now - times[-5]).total_seconds() < 5

def is_caps_abuse(content):
    letters = [c for c in content if c.isalpha()]
    if len(letters) < 10: return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.8

def is_toxic(content):
    return any(re.search(p, content.lower()) for p in [r"\b(kill yourself|kys)\b", r"\b(go die)\b"])

AI_MOD_PROMPT = """You are a Discord moderation AI. Analyze this message and respond ONLY with a JSON object like:
{"action": "none"|"warn"|"delete"|"mute"|"ban", "reason": "short reason"}

Rules:
- "none" = clean message, jokes, sarcasm, friendly banter, hyperbolic phrases like "im gonna kill you" said casually between friends, gaming rage, memes
- "warn" = genuine mild toxicity, real personal insults meant to hurt, mild targeted harassment
- "delete" = actual slurs used to attack, real hate speech, explicit sexual content, doxxing attempts, scam links, raids, spam
- "mute" = clear targeted harassment campaign, repeated genuine threats with specific details, explicit content
- "ban" = CSAM, very explicit credible death threats with details like location/name/method, severe doxxing with personal info, extremist manifestos

IMPORTANT context rules:
- "im gonna kill you" / "i will destroy you" / "you are dead" between friends or in gaming context = "none"
- Hyperbole and jokes are NEVER "ban" — only real credible threats with specific details
- Always consider the casual Discord chat context — people joke, tease and use dark humor constantly
- When in doubt, choose a lighter action. False positives hurt real users.
- Emojis like 😂🤣💀 after a statement = almost certainly a joke = "none"

Only respond with the JSON. Nothing else."""

async def ai_moderate(content: str) -> dict:
    try:
        r = await asyncio.to_thread(groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile", max_tokens=60,
            messages=[
                {"role": "system", "content": AI_MOD_PROMPT},
                {"role": "user", "content": f"Message: {content}"}
            ])
        import json
        text = r.choices[0].message.content.strip()
        return json.loads(text)
    except:
        return {"action": "none", "reason": ""}

async def get_ai_response(context, channel_id):
    prev = last_reply[channel_id]
    extra = f"\n\nDo NOT repeat: '{prev}'" if prev else ""
    for attempt in range(5):
        try:
            r = await asyncio.to_thread(groq_client.chat.completions.create,
                model="llama-3.3-70b-versatile", max_tokens=80,
                messages=[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":context+extra}])
            reply = r.choices[0].message.content.strip()
            if reply:
                last_reply[channel_id] = reply
                return reply
        except Exception as e:
            print(f"Groq attempt {attempt+1}: {e}")
            await asyncio.sleep(2)
    return None

async def warn_user(guild, member, reason, channel):
    user_warnings[member.id] += 1
    count = user_warnings[member.id]
    embed = discord.Embed(color=0xf0a500)
    embed.set_author(name="⚠️ Warning Issued")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Warning #", value=count, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await channel.send(embed=embed)
    if count >= 3: await temp_mute(guild, member, channel, 10, auto=True)
    if count >= 5:
        try:
            await member.kick(reason="Too many warnings")
            await channel.send(f"{member.mention} has been kicked. 5 warnings reached.")
        except: pass

async def temp_mute(guild, member, channel, minutes=10, auto=False):
    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role:
        mute_role = await guild.create_role(name="Muted")
        for ch in guild.channels:
            try: await ch.set_permissions(mute_role, send_messages=False, speak=False)
            except: pass
    await member.add_roles(mute_role)
    await channel.send(f"{'Auto-muted' if auto else 'Muted'} {member.mention} for {minutes} minutes.")
    await asyncio.sleep(minutes * 60)
    if mute_role in member.roles:
        await member.remove_roles(mute_role)
        await channel.send(f"{member.mention} has been unmuted.")

async def handle_reactions(message):
    content_lower = message.content.lower()
    is_lunor = LUNOR_NAME in message.author.display_name.lower()
    for keyword, only_lunor, emoji in REACTION_RULES:
        if keyword in content_lower:
            if only_lunor and not is_lunor: continue
            try: await message.add_reaction(emoji)
            except: pass

# ── Events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    bot.loop.create_task(reminder_loop())
    print(f"Online. slash commands synced.")

@bot.event
async def on_member_join(member):
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel:
        embed = discord.Embed(description=f"Welcome to **{member.guild.name}**, {member.mention}! 👋", color=0x5865f2)
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

@bot.event
async def on_member_remove(member):
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel:
        await channel.send(f"**{member.display_name}** has left the server.")

@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    content = message.content
    channel = message.channel
    member  = message.author

    # AFK check — if someone mentions an AFK user
    for mentioned in message.mentions:
        if mentioned.id in afk_users:
            reason = afk_users[mentioned.id]
            await channel.send(f"{mentioned.mention} is AFK: {reason}")

    # Remove AFK if AFK user sends a message
    if member.id in afk_users:
        del afk_users[member.id]
        await channel.send(f"Welcome back {member.mention}! AFK removed.")

    if is_blocked_channel(channel.name): return
    channel_history[channel.id].append(f"{member.display_name}: {content}")
    await handle_reactions(message)

    # Lunor is invincible — skip all auto-mod
    if LUNOR_NAME not in member.display_name.lower():
        if is_spam(message):
            try: await message.delete()
            except: pass
            await warn_user(message.guild, member, "spam detected", channel)
            return

        if is_caps_abuse(content):
            await warn_user(message.guild, member, "excessive caps", channel)

        if is_toxic(content):
            try: await message.delete()
            except: pass
            await warn_user(message.guild, member, "toxic message deleted", channel)
            return

        # AI moderation
        ai_result = await ai_moderate(content)
        action = ai_result.get("action", "none")
        reason = ai_result.get("reason", "flagged by AI")

        if action == "delete":
            try: await message.delete()
            except: pass
            embed = discord.Embed(description=f"{member.mention} your message was removed. Reason: {reason}", color=0xe74c3c)
            await channel.send(embed=embed, delete_after=8)
            return

        elif action == "warn":
            await warn_user(message.guild, member, f"AI flagged: {reason}", channel)

        elif action == "mute":
            try: await message.delete()
            except: pass
            await warn_user(message.guild, member, f"AI flagged: {reason}", channel)
            await temp_mute(message.guild, member, channel, 10, auto=True)

        elif action == "ban":
            try: await message.delete()
            except: pass
            embed = discord.Embed(color=0xe74c3c)
            embed.set_author(name="🔨 Auto-Banned by AI")
            embed.add_field(name="User", value=member.mention)
            embed.add_field(name="Reason", value=reason)
            await channel.send(embed=embed)
            try: await member.ban(reason=f"Auto-ban: {reason}")
            except: pass
            return

    is_pinged  = bot.user in message.mentions
    is_replied = message.reference is not None

    if is_pinged or is_replied:
        history = list(channel_history[channel.id])
        ctx = "Recent chat:\n" + "\n".join(history[-6:]) + f"\n\nLatest: {member.display_name}: {content}\n\nYou were pinged. Reply as Lunors Slave."
        reply = await get_ai_response(ctx, channel.id)
        if reply:
            await channel.send(reply)
            last_response_time[channel.id] = datetime.utcnow()
        return

    if not random_talking_enabled or current_chaos_mode == "off": return
    if is_on_cooldown(channel.id): return

    roll = random.random()
    if roll < 0.01:
        await channel.send(random.choice(ULTRA_CHAOS_LINES))
        last_response_time[channel.id] = datetime.utcnow()
        return

    if roll < CHAOS_LEVELS[current_chaos_mode]:
        history = list(channel_history[channel.id])
        ctx = "Recent chat:\n" + "\n".join(history[-6:]) + f"\n\nLatest: {member.display_name}: {content}\n\nReply as Lunors Slave. 1-2 sentences."
        await asyncio.sleep(random.uniform(2, 5))
        reply = await get_ai_response(ctx, channel.id)
        if reply:
            await channel.send(reply)
            last_response_time[channel.id] = datetime.utcnow()

# ── Reminder loop ─────────────────────────────────────────────────────────────
async def reminder_loop():
    while True:
        now = datetime.utcnow()
        for r in reminders[:]:
            fire_time, channel_id, user_id, msg = r
            if now >= fire_time:
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(f"<@{user_id}> ⏰ Reminder: {msg}")
                reminders.remove(r)
        await asyncio.sleep(30)

# ═════════════════════════════════════════════════════════════
#   SLASH COMMANDS
# ═════════════════════════════════════════════════════════════

# ── Moderation ────────────────────────────────────────────────────────────────
@tree.command(name="warn", description="Warn a user")
@app_commands.describe(member="User to warn", reason="Reason")
async def warn_slash(i, member: discord.Member, reason: str = "no reason given"):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    await i.response.defer()
    await warn_user(i.guild, member, reason, i.channel)

@tree.command(name="mute", description="Temporarily mute a user")
@app_commands.describe(member="User", minutes="Duration in minutes", reason="Reason")
async def mute_slash(i, member: discord.Member, minutes: int = 10, reason: str = "no reason given"):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    embed = discord.Embed(color=0xf0a500)
    embed.set_author(name="🔇 Muted")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Duration", value=f"{minutes}m", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await i.response.send_message(embed=embed)
    await temp_mute(i.guild, member, i.channel, minutes)

@tree.command(name="unmute", description="Unmute a user")
async def unmute_slash(i, member: discord.Member):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    mute_role = discord.utils.get(i.guild.roles, name="Muted")
    if mute_role and mute_role in member.roles:
        await member.remove_roles(mute_role)
        await i.response.send_message(f"{member.mention} unmuted.")
    else:
        await i.response.send_message(f"{member.mention} is not muted.", ephemeral=True)

@tree.command(name="kick", description="Kick a user")
@app_commands.describe(member="User", reason="Reason")
async def kick_slash(i, member: discord.Member, reason: str = "no reason given"):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    embed = discord.Embed(color=0xe67e22)
    embed.set_author(name="👢 Kicked")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await i.response.send_message(embed=embed)
    try: await member.send(f"You were kicked. Reason: {reason}")
    except: pass
    await member.kick(reason=reason)

@tree.command(name="ban", description="Ban a user")
@app_commands.describe(member="User", days="Days of messages to delete (0-7)", reason="Reason")
async def ban_slash(i, member: discord.Member, days: int = 0, reason: str = "no reason given"):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    embed = discord.Embed(color=0xe74c3c)
    embed.set_author(name="🔨 Banned")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Messages deleted", value=f"{days} day(s)" if days else "none", inline=True)
    await i.response.send_message(embed=embed)
    try: await member.send(f"You were banned. Reason: {reason}")
    except: pass
    await member.ban(reason=reason, delete_message_days=min(days,7))

@tree.command(name="unban", description="Unban a user by username")
@app_commands.describe(username="Exact username")
async def unban_slash(i, username: str):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    await i.response.defer()
    async for entry in i.guild.bans():
        if entry.user.name.lower() == username.lower():
            await i.guild.unban(entry.user)
            await i.followup.send(f"**{entry.user}** unbanned.")
            return
    await i.followup.send(f"Could not find banned user **{username}**.")

@tree.command(name="purge", description="Delete messages in bulk")
@app_commands.describe(amount="Number of messages (max 100)")
async def purge_slash(i, amount: int = 10):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    if amount > 100: await i.response.send_message("Max is 100.", ephemeral=True); return
    await i.response.defer(ephemeral=True)
    deleted = await i.channel.purge(limit=amount)
    await i.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)

@tree.command(name="slowmode", description="Set slowmode in current channel")
@app_commands.describe(seconds="Seconds (0 to disable)")
async def slowmode_slash(i, seconds: int = 0):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    await i.channel.edit(slowmode_delay=seconds)
    await i.response.send_message(f"Slowmode {'disabled' if seconds==0 else f'set to {seconds}s'}.")

@tree.command(name="lock", description="Lock the current channel")
async def lock_slash(i):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    await i.channel.set_permissions(i.guild.default_role, send_messages=False)
    await i.response.send_message("🔒 Channel locked.")

@tree.command(name="unlock", description="Unlock the current channel")
async def unlock_slash(i):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    await i.channel.set_permissions(i.guild.default_role, send_messages=True)
    await i.response.send_message("🔓 Channel unlocked.")

@tree.command(name="warnings", description="Check a user's warnings")
@app_commands.describe(member="User to check")
async def warnings_slash(i, member: discord.Member = None):
    target = member or i.user
    await i.response.send_message(f"{target.mention} has {user_warnings[target.id]} warning(s).", ephemeral=True)

@tree.command(name="clearwarnings", description="Clear all warnings for a user")
@app_commands.describe(member="User to clear")
async def clearwarnings_slash(i, member: discord.Member):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    user_warnings[member.id] = 0
    await i.response.send_message(f"Warnings cleared for {member.mention}.")

# ── Utility ───────────────────────────────────────────────────────────────────
@tree.command(name="afk", description="Set your AFK status")
@app_commands.describe(reason="Why you are AFK")
async def afk_slash(i, reason: str = "AFK"):
    afk_users[i.user.id] = reason
    await i.response.send_message(f"{i.user.mention} is now AFK: {reason}")

@tree.command(name="userinfo", description="Get info about a user")
@app_commands.describe(member="User to look up")
async def userinfo_slash(i, member: discord.Member = None):
    t = member or i.user
    embed = discord.Embed(color=0x5865f2)
    embed.set_author(name=f"User Info — {t.display_name}", icon_url=t.display_avatar.url)
    embed.add_field(name="Username", value=str(t), inline=True)
    embed.add_field(name="ID", value=t.id, inline=True)
    embed.add_field(name="Warnings", value=user_warnings[t.id], inline=True)
    embed.add_field(name="Joined", value=t.joined_at.strftime("%d %b %Y"), inline=True)
    embed.add_field(name="Created", value=t.created_at.strftime("%d %b %Y"), inline=True)
    roles = [r.mention for r in t.roles if r.name != "@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "none", inline=False)
    embed.set_thumbnail(url=t.display_avatar.url)
    await i.response.send_message(embed=embed)

@tree.command(name="serverinfo", description="Get info about this server")
async def serverinfo_slash(i):
    g = i.guild
    embed = discord.Embed(title=g.name, color=0x5865f2)
    embed.add_field(name="Members", value=g.member_count, inline=True)
    embed.add_field(name="Channels", value=len(g.channels), inline=True)
    embed.add_field(name="Roles", value=len(g.roles), inline=True)
    embed.add_field(name="Owner", value=g.owner.mention, inline=True)
    embed.add_field(name="Created", value=g.created_at.strftime("%d %b %Y"), inline=True)
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await i.response.send_message(embed=embed)

@tree.command(name="avatar", description="Get a user's avatar")
@app_commands.describe(member="User")
async def avatar_slash(i, member: discord.Member = None):
    t = member or i.user
    embed = discord.Embed(color=0x5865f2)
    embed.set_image(url=t.display_avatar.url)
    embed.set_footer(text=f"Avatar of {t.display_name}")
    await i.response.send_message(embed=embed)

@tree.command(name="ping", description="Check the bot's latency")
async def ping_slash(i):
    await i.response.send_message(f"🏓 Pong! `{round(bot.latency * 1000)}ms`")

@tree.command(name="remind", description="Set a reminder")
@app_commands.describe(minutes="Minutes from now", message="What to remind you about")
async def remind_slash(i, minutes: int, message: str):
    fire_time = datetime.utcnow() + timedelta(minutes=minutes)
    reminders.append((fire_time, i.channel.id, i.user.id, message))
    await i.response.send_message(f"⏰ Reminder set for {minutes} minute(s): **{message}**", ephemeral=True)

@tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Poll question", option1="Option 1", option2="Option 2", option3="Option 3 (optional)", option4="Option 4 (optional)")
async def poll_slash(i, question: str, option1: str, option2: str, option3: str = None, option4: str = None):
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis  = ["1️⃣","2️⃣","3️⃣","4️⃣"]
    desc    = "\n".join(f"{emojis[idx]} {opt}" for idx, opt in enumerate(options))
    embed   = discord.Embed(title=f"📊 {question}", description=desc, color=0x5865f2)
    embed.set_footer(text=f"Poll by {i.user.display_name}")
    await i.response.send_message(embed=embed)
    msg = await i.original_response()
    for idx in range(len(options)):
        await msg.add_reaction(emojis[idx])

@tree.command(name="embed", description="Send a custom embed [Lunor only]")
@app_commands.describe(title="Embed title", message="Embed message", color="Color name or hex")
async def embed_slash(i, title: str, message: str, color: str = "blue"):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    embed = discord.Embed(title=title, description=message, color=parse_color(color))
    await i.response.send_message("Sent, master.", ephemeral=True)
    await i.channel.send(embed=embed)

@tree.command(name="say", description="Make the bot say something [Lunor only]")
@app_commands.describe(message="Message to send")
async def say_slash(i, message: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    await i.response.send_message("Done, master.", ephemeral=True)
    await i.channel.send(message)

@tree.command(name="announce", description="Send an announcement [Lunor only]")
@app_commands.describe(message="Announcement message")
async def announce_slash(i, message: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    embed = discord.Embed(description=message, color=0x5865f2)
    embed.set_footer(text="— Lunor")
    await i.response.send_message(embed=embed)

# ── Server management (Lunor only) ───────────────────────────────────────────
@tree.command(name="createrole", description="Create a role [Lunor only]")
@app_commands.describe(name="Role name", color="Color name or hex")
async def createrole_slash(i, name: str, color: str = "default"):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    role = await i.guild.create_role(name=name, color=parse_color(color))
    await i.response.send_message(f"Role **{role.name}** created, master.")

@tree.command(name="delrole", description="Delete a role [Lunor only]")
@app_commands.describe(name="Role name")
async def delrole_slash(i, name: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    role = discord.utils.get(i.guild.roles, name=name)
    if not role: await i.response.send_message("Role not found.", ephemeral=True); return
    await role.delete()
    await i.response.send_message(f"Role **{name}** deleted, master.")

@tree.command(name="giverole", description="Give a role to a user [Lunor only]")
@app_commands.describe(member="User", role="Role")
async def giverole_slash(i, member: discord.Member, role: discord.Role):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    await member.add_roles(role)
    await i.response.send_message(f"Role **{role.name}** given to {member.mention}, master.")

@tree.command(name="takerole", description="Remove a role from a user [Lunor only]")
@app_commands.describe(member="User", role="Role")
async def takerole_slash(i, member: discord.Member, role: discord.Role):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    await member.remove_roles(role)
    await i.response.send_message(f"Role **{role.name}** removed from {member.mention}, master.")

@tree.command(name="createchannel", description="Create a text channel [Lunor only]")
@app_commands.describe(name="Channel name")
async def createchannel_slash(i, name: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    ch = await i.guild.create_text_channel(name=name)
    await i.response.send_message(f"Channel {ch.mention} created, master.")

@tree.command(name="delchannel", description="Delete a channel [Lunor only]")
@app_commands.describe(channel="Channel to delete")
async def delchannel_slash(i, channel: discord.TextChannel):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    name = channel.name
    await channel.delete()
    await i.response.send_message(f"Channel **{name}** deleted, master.")

@tree.command(name="setnick", description="Change a user's nickname [Lunor only]")
@app_commands.describe(member="User", nickname="New nickname")
async def setnick_slash(i, member: discord.Member, nickname: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    await member.edit(nick=nickname)
    await i.response.send_message(f"Nickname set to **{nickname}**, master.")

@tree.command(name="chaos", description="Set the bot's chat mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="off (commands only)", value="off"),
    app_commands.Choice(name="calm (5%)", value="calm"),
    app_commands.Choice(name="normal (30%)", value="normal"),
    app_commands.Choice(name="chaos (70%)", value="chaos"),
])
async def chaos_slash(i, mode: str):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    global current_chaos_mode
    current_chaos_mode = mode
    msgs = {"off": "Silent mode. Commands only.", "calm": "Calm mode.", "normal": "Normal mode.", "chaos": "CHAOS MODE."}
    await i.response.send_message(msgs[mode])

@tree.command(name="talk", description="Toggle random responses on or off")
async def talk_slash(i):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    global random_talking_enabled
    random_talking_enabled = not random_talking_enabled
    await i.response.send_message(f"Random talking {'enabled' if random_talking_enabled else 'disabled'}.")


# ── Extra Dyno-style commands ────────────────────────────────────────────────

@tree.command(name="softban", description="Ban then immediately unban a user to delete their messages")
@app_commands.describe(member="User to softban", reason="Reason")
async def softban_slash(i, member: discord.Member, reason: str = "no reason given"):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    embed = discord.Embed(color=0xe67e22)
    embed.set_author(name="🔄 User Softbanned")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await i.response.send_message(embed=embed)
    await member.ban(reason=reason, delete_message_days=7)
    await i.guild.unban(member)

@tree.command(name="tempban", description="Ban a user for a limited time")
@app_commands.describe(member="User", minutes="Minutes to ban for", reason="Reason")
async def tempban_slash(i, member: discord.Member, minutes: int = 60, reason: str = "no reason given"):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    embed = discord.Embed(color=0xe74c3c)
    embed.set_author(name="⏱️ Temp Banned")
    embed.add_field(name="User", value=str(member), inline=True)
    embed.add_field(name="Duration", value=f"{minutes} minute(s)", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await i.response.send_message(embed=embed)
    await member.ban(reason=reason)
    await asyncio.sleep(minutes * 60)
    try:
        await i.guild.unban(member)
        await i.channel.send(f"**{member}** has been automatically unbanned after {minutes} minute(s).")
    except: pass

@tree.command(name="temprole", description="Give a user a role for a limited time")
@app_commands.describe(member="User", role="Role to give", minutes="Duration in minutes")
async def temprole_slash(i, member: discord.Member, role: discord.Role, minutes: int = 60):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    await member.add_roles(role)
    expire = datetime.utcnow() + timedelta(minutes=minutes)
    temp_roles.append((expire, member.id, i.guild.id, role.id))
    await i.response.send_message(f"Gave {member.mention} the **{role.name}** role for {minutes} minute(s).")

@tree.command(name="modlogs", description="View moderation logs for a user")
@app_commands.describe(member="User to check")
async def modlogs_slash(i, member: discord.Member):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    user_cases = [(num, case) for num, case in mod_cases.items() if case["user_id"] == member.id]
    if not user_cases:
        await i.response.send_message(f"No moderation logs for {member.mention}.", ephemeral=True); return
    embed = discord.Embed(title=f"Mod Logs — {member.display_name}", color=0x5865f2)
    for num, case in user_cases[-10:]:
        t = case['type']; r = case['reason']; m = case['mod_id']
    await i.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="modstats", description="Get moderation stats for a moderator")
@app_commands.describe(member="Moderator to check")
async def modstats_slash(i, member: discord.Member):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    mod_actions = [c for c in mod_cases.values() if c["mod_id"] == member.id]
    counts = {}
    for c in mod_actions:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    embed = discord.Embed(title=f"Mod Stats — {member.display_name}", color=0x5865f2)
    if not counts:
        embed.description = "No moderation actions found."
    else:
        for action, count in counts.items():
            embed.add_field(name=action, value=count, inline=True)
    await i.response.send_message(embed=embed)

@tree.command(name="note", description="Add a private note to a user's record")
@app_commands.describe(member="User", note="Note to add")
async def note_slash(i, member: discord.Member, note: str):
    if not mod_only(i): await i.response.send_message("No permission.", ephemeral=True); return
    global case_counter
    case_counter += 1
    mod_cases[case_counter] = {"type": "Note", "user_id": member.id, "mod_id": i.user.id, "reason": note, "timestamp": datetime.utcnow()}
    await i.response.send_message(f"Note added to {member.mention}'s record. Case #{case_counter}.", ephemeral=True)

@tree.command(name="roleall", description="Give or remove a role from all members [Lunor only]")
@app_commands.describe(role="Role", action="add or remove")
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
async def roleall_slash(i, role: discord.Role, action: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    await i.response.send_message(f"{'Adding' if action == 'add' else 'Removing'} **{role.name}** {'to' if action == 'add' else 'from'} all members... this may take a while.")
    count = 0
    for member in i.guild.members:
        try:
            if action == "add" and role not in member.roles:
                await member.add_roles(role)
                count += 1
            elif action == "remove" and role in member.roles:
                await member.remove_roles(role)
                count += 1
        except: pass
    await i.channel.send(f"Done! {'Added' if action == 'add' else 'Removed'} **{role.name}** {'to' if action == 'add' else 'from'} {count} members, master.")

@tree.command(name="giveaway", description="Start a giveaway [Lunor only]")
@app_commands.describe(prize="What you are giving away", minutes="Duration in minutes", winners="Number of winners")
async def giveaway_slash(i, prize: str, minutes: int = 60, winners: int = 1):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    end_time = datetime.utcnow() + timedelta(minutes=minutes)
    desc = f"**Prize:** {prize}\n\nReact with 🎉 to enter!\n\n**Winners:** {winners}\n**Ends:** in {minutes} minute(s)"
    embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=desc, color=0xf0a500)
    embed.set_footer(text=f"Hosted by {i.user.display_name}")
    await i.response.send_message("Giveaway started, master!", ephemeral=True)
    msg = await i.channel.send(embed=embed)
    await msg.add_reaction("🎉")
    giveaways[msg.id] = {"prize": prize, "winner_count": winners, "end_time": end_time, "channel_id": i.channel.id}
    await asyncio.sleep(minutes * 60)
    msg = await i.channel.fetch_message(msg.id)
    reaction = discord.utils.get(msg.reactions, emoji="🎉")
    if reaction:
        users = [u async for u in reaction.users() if not u.bot]
        if users:
            picked = random.sample(users, min(winners, len(users)))
            winners_mentions = ", ".join(w.mention for w in picked)
            await i.channel.send(f"🎉 Congratulations {winners_mentions}! You won **{prize}**!")
        else:
            await i.channel.send(f"No one entered the giveaway for **{prize}**.")

@tree.command(name="coinflip", description="Flip a coin")
async def coinflip_slash(i):
    result = random.choice(["Heads 🪙", "Tails 🪙"])
    await i.response.send_message(f"**{result}!**")

@tree.command(name="roll", description="Roll a dice")
@app_commands.describe(sides="Number of sides (default 6)")
async def roll_slash(i, sides: int = 6):
    result = random.randint(1, sides)
    await i.response.send_message(f"🎲 You rolled a **{result}** (d{sides})")

@tree.command(name="8ball", description="Ask the magic 8ball a question")
@app_commands.describe(question="Your question")
async def eightball_slash(i, question: str):
    responses = ["Yes.", "No.", "Definitely.", "Absolutely not.", "Maybe.", "Ask again later.", "Without a doubt.", "Don't count on it.", "Signs point to yes.", "Very doubtful."]
    answer = random.choice(responses)
    await i.response.send_message(f"🎱 **{question}**\n{answer}")
@tree.command(name="choose", description="Choose between multiple options")
@app_commands.describe(options="Options separated by commas")
async def choose_slash(i, options: str):
    choices = [o.strip() for o in options.split(",")]
    await i.response.send_message(f"I choose: **{random.choice(choices)}**")

@tree.command(name="topic", description="Get a random conversation topic")
async def topic_slash(i):
    topics = [
        "What's the best video game of all time?",
        "If you could live anywhere, where would it be?",
        "What superpower would you want?",
        "What's the most overrated movie?",
        "What's your most unpopular opinion?",
        "If you had infinite money what would you do first?",
        "What's the best food ever made?",
        "Would you rather be super strong or super fast?",
    ]
    await i.response.send_message(f"💬 **Topic:** {random.choice(topics)}")

@tree.command(name="math", description="Solve a math expression")
@app_commands.describe(expression="Math expression e.g. 2+2")
async def math_slash(i, expression: str):
    try:
        allowed = set("0123456789+-*/()%. ")
        if not all(c in allowed for c in expression):
            await i.response.send_message("Invalid expression.", ephemeral=True); return
        result = eval(expression)
        await i.response.send_message(f"**{expression} = {result}**")
    except:
        await i.response.send_message("Could not calculate that.", ephemeral=True)

@tree.command(name="steal", description="Steal an emoji from another server [Lunor only]")
@app_commands.describe(emoji="The emoji to steal")
async def steal_slash(i, emoji: str):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    import re
    match = re.search(r"<a?:(\w+):(\d+)>", emoji)
    if not match:
        await i.response.send_message("Please provide a custom emoji.", ephemeral=True); return
    name, eid = match.group(1), match.group(2)
    url = f"https://cdn.discordapp.com/emojis/{eid}.{'gif' if emoji.startswith('<a') else 'png'}"
    import urllib.request
    data = urllib.request.urlopen(url).read()
    new_emoji = await i.guild.create_custom_emoji(name=name, image=data)
    await i.response.send_message(f"Emoji {new_emoji} stolen and added, master!")


# ── Fun & fake commands ───────────────────────────────────────────────────────

@tree.command(name="hack", description="Hack someone (fake)")
@app_commands.describe(member="Who to hack")
async def hack_slash(i, member: discord.Member):
    import random
    ip = f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(0,255)}"
    port = random.randint(1000, 9999)
    password = random.choice(["hunter2", "password123", "qwerty", "letmein", "iloveyou", "monkey123", "abc123!", "sunshine99", "dragon2024", "shadow_x"])
    address_number = random.randint(1, 999)
    street = random.choice(["Oak Street", "Maple Avenue", "Pine Road", "Elm Drive", "Cedar Lane", "Birch Court", "Willow Way", "Sunset Blvd"])
    city = random.choice(["London", "Sydney", "New York", "Tokyo", "Paris", "Berlin", "Toronto", "Dubai"])
    postcode = f"{random.choice('ABCDEFGHIJKLMNOPRSTUVWXYZ')}{random.randint(1,9)} {random.randint(1,9)}{random.choice('ABCDEFGHIJKLMNOPRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPRSTUVWXYZ')}"
    bank = random.choice(["Barclays", "Chase", "Westpac", "HSBC", "Lloyds", "NAB", "CommBank"])
    balance = f"${random.randint(0, 50000):,}.{random.randint(0,99):02d}"
    steps = [
        f"📡 Locating target **{member.display_name}**...",
        f"🔍 IP found: `{ip}:{port}`",
        f"🔓 Bypassing firewall...",
        f"💾 Extracting credentials...",
        f"🏠 Address: `{address_number} {street}, {city} {postcode}`",
        f"🔑 Password: `{password}`",
        f"🏦 Bank: {bank} | Balance: {balance}",
        f"✅ **Hack complete.** {member.mention} has been compromised. 😈",
    ]
    await i.response.send_message("💻 Initiating hack sequence...")
    msg = await i.original_response()
    text = ""
    for step in steps:
        text += step + "\n"
        await msg.edit(content=text)
        await asyncio.sleep(1.2)

@tree.command(name="fakedmote", description="Fake demote someone (just for laughs)")
@app_commands.describe(member="Who to fake demote", role="Role name to fake remove")
async def fakedmote_slash(i, member: discord.Member, role: str):
    embed = discord.Embed(color=0xe74c3c)
    embed.set_author(name="📉 Member Demoted")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Role removed", value=f"**{role}**", inline=True)
    embed.add_field(name="Demoted by", value=i.user.mention, inline=True)
    embed.set_footer(text="Better luck next time.")
    await i.response.send_message(embed=embed)

@tree.command(name="demote", description="Actually remove a role from a user [Lunor only]")
@app_commands.describe(member="Who to demote", role="Role to remove")
async def demote_slash(i, member: discord.Member, role: discord.Role):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    if role not in member.roles:
        await i.response.send_message(f"{member.mention} doesn't have **{role.name}**.", ephemeral=True); return
    await member.remove_roles(role)
    embed = discord.Embed(color=0xe74c3c)
    embed.set_author(name="📉 Member Demoted")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Role removed", value=role.mention, inline=True)
    embed.add_field(name="Demoted by", value=i.user.mention, inline=True)
    embed.set_footer(text="As commanded, master.")
    await i.response.send_message(embed=embed)

@tree.command(name="ship", description="Ship two people together")
@app_commands.describe(member1="First person", member2="Second person")
async def ship_slash(i, member1: discord.Member, member2: discord.Member):
    score = random.randint(0, 100)
    if score < 20: bar = "💔 not happening"
    elif score < 40: bar = "😬 awkward"
    elif score < 60: bar = "👀 maybe..."
    elif score < 80: bar = "💖 cute couple!"
    else: bar = "❤️‍🔥 SOULMATES"
    name1 = member1.display_name[:len(member1.display_name)//2]
    name2 = member2.display_name[len(member2.display_name)//2:]
    ship_name = name1 + name2
    embed = discord.Embed(title=f"💘 {member1.display_name} + {member2.display_name}", color=0xff69b4)
    embed.add_field(name="Ship name", value=f"**{ship_name}**", inline=True)
    embed.add_field(name="Compatibility", value=f"**{score}%** — {bar}", inline=True)
    await i.response.send_message(embed=embed)

@tree.command(name="roast", description="Roast someone with AI")
@app_commands.describe(member="Who to roast")
async def roast_slash(i, member: discord.Member):
    await i.response.defer()
    try:
        r = await asyncio.to_thread(groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile", max_tokens=80,
            messages=[
                {"role": "system", "content": "You are a witty roast comedian. Generate ONE funny, playful roast of a Discord user. Keep it light and funny, not hateful. One sentence only."},
                {"role": "user", "content": f"Roast a Discord user named {member.display_name}"}
            ])
        roast = r.choices[0].message.content.strip()
    except:
        roast = f"{member.display_name} is so bad at Discord they thought a server was a restaurant."
    embed = discord.Embed(description=f"🔥 {member.mention} {roast}", color=0xff4500)
    await i.followup.send(embed=embed)

@tree.command(name="compliment", description="Compliment someone")
@app_commands.describe(member="Who to compliment")
async def compliment_slash(i, member: discord.Member):
    compliments = [
        "is genuinely one of the best people in this server.",
        "has the energy of someone who actually reads the rules voluntarily.",
        "could make anyone's day better just by existing.",
        "is the kind of person the internet was made for.",
        "radiates good vibes and everyone notices.",
        "is built different and we love that.",
        "is a certified legend and the data supports it.",
    ]
    embed = discord.Embed(description=f"💐 {member.mention} {random.choice(compliments)}", color=0x57f287)
    await i.response.send_message(embed=embed)

@tree.command(name="rps", description="Play rock paper scissors against the bot")
@app_commands.describe(choice="Your choice")
@app_commands.choices(choice=[
    app_commands.Choice(name="Rock 🪨", value="rock"),
    app_commands.Choice(name="Paper 📄", value="paper"),
    app_commands.Choice(name="Scissors ✂️", value="scissors"),
])
async def rps_slash(i, choice: str):
    options = ["rock", "paper", "scissors"]
    emojis  = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    bot_choice = random.choice(options)
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    if choice == bot_choice: result = "It's a tie! 🤝"
    elif wins[choice] == bot_choice: result = "You win! 🎉"
    else: result = "I win! 😈"
    msg_text = f"You: {emojis[choice]} vs Me: {emojis[bot_choice]}\n**{result}**"

@tree.command(name="wanted", description="Generate a wanted poster for someone")
@app_commands.describe(member="The wanted criminal")
async def wanted_slash(i, member: discord.Member):
    crimes = [
        "excessive use of caps lock",
        "sending memes at 3am",
        "saying 'gg ez' unironically",
        "leaving voice chat without warning",
        "typing 'lol' without actually laughing",
        "asking what time it is in a different timezone",
        "pinging everyone for no reason",
        "starting drama and going offline",
    ]
    reward = f"${random.randint(100, 99999):,}"
    embed = discord.Embed(title="🚨 WANTED 🚨", color=0xff0000)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Criminal", value=member.mention, inline=True)
    embed.add_field(name="Reward", value=reward, inline=True)
    embed.add_field(name="Crime", value=random.choice(crimes), inline=False)
    embed.set_footer(text="Report any sightings to your local mod team.")
    await i.response.send_message(embed=embed)

@tree.command(name="fakepromote", description="Fake promote someone (just for laughs)")
@app_commands.describe(member="Who to fake promote", role="Role name to fake give")
async def fakepromote_slash(i, member: discord.Member, role: str):
    embed = discord.Embed(color=0x57f287)
    embed.set_author(name="📈 Member Promoted")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Role given", value=f"**{role}**", inline=True)
    embed.add_field(name="Promoted by", value=i.user.mention, inline=True)
    embed.set_footer(text="Congratulations! You earned it.")
    await i.response.send_message(embed=embed)

@tree.command(name="promote", description="Actually give a role to a user [Lunor only]")
@app_commands.describe(member="Who to promote", role="Role to give")
async def promote_slash(i, member: discord.Member, role: discord.Role):
    if not lunor_only(i): await i.response.send_message("Only Lunor can do that.", ephemeral=True); return
    if role in member.roles:
        await i.response.send_message(f"{member.mention} already has **{role.name}**.", ephemeral=True); return
    await member.add_roles(role)
    embed = discord.Embed(color=0x57f287)
    embed.set_author(name="📈 Member Promoted")
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Role given", value=role.mention, inline=True)
    embed.add_field(name="Promoted by", value=i.user.mention, inline=True)
    embed.set_footer(text="As commanded, master.")
    await i.response.send_message(embed=embed)

bot.run(DISCORD_TOKEN)
