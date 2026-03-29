"""
Discord Bot — Economy + Blackjack + RSS Feed
Single-file | Python 3.11+ | Railway-ready
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json, os, random, re
from datetime import datetime, timezone
from typing import Optional
import aiohttp
import feedparser

# ═══════════════════════════════════════════════════════
#  CONFIG  ── edit before running, or set env vars
# ═══════════════════════════════════════════════════════
TOKEN = os.environ.get("TOKEN")
RSS_FEED_URL   = RSS_FEED_URL = "https://rss.app/feeds/YCoPXpqs4XeLV87I.xml"
RSS_CHANNEL_ID =RSS_CHANNEL_ID = 1419941517103075454
DATA_FILE      = "economy.json"

# ═══════════════════════════════════════════════════════
#  SHOP CATALOGUE
# ═══════════════════════════════════════════════════════
SHOP_ITEMS: dict[str, dict] = {
    "Lucky Coin":   {"price": 500,    "daily_bonus": 100,   "emoji": "🪙", "desc": "A touch of extra luck"},
    "Golden Watch": {"price": 1_500,  "daily_bonus": 250,   "emoji": "⌚", "desc": "Time is money"},
    "Diamond Ring": {"price": 5_000,  "daily_bonus": 750,   "emoji": "💍", "desc": "Flex on the server"},
    "Briefcase":    {"price": 10_000, "daily_bonus": 1_500, "emoji": "💼", "desc": "Business class income"},
    "Yacht":        {"price": 50_000, "daily_bonus": 5_000, "emoji": "🛥️",  "desc": "The ultimate flex"},
}

# ═══════════════════════════════════════════════════════
#  CARD HELPERS
# ═══════════════════════════════════════════════════════
SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def build_deck() -> list[str]:
    return [f"{r}{s}" for s in SUITS for r in RANKS]

def card_val(card: str) -> int:
    rank = card[:-2]
    if rank in ("J", "Q", "K"): return 10
    if rank == "A":              return 11
    return int(rank)

def hand_total(hand: list[str]) -> int:
    total = sum(card_val(c) for c in hand)
    aces  = sum(1 for c in hand if c.startswith("A"))
    while total > 21 and aces:
        total -= 10
        aces  -= 1
    return total

def draw_card(deck: list[str]) -> str:
    return deck.pop(random.randrange(len(deck)))

def hand_display(hand: list[str]) -> str:
    return "  ".join(hand)

# ═══════════════════════════════════════════════════════
#  DATA LAYER
# ═══════════════════════════════════════════════════════
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

_DEFAULTS = {
    "cash": 500, "bank": 0, "items": [],
    "last_daily": 0, "last_work": 0, "last_crime": 0, "last_rob": 0,
}

def get_user(data: dict, uid: int) -> dict:
    key = str(uid)
    if key not in data:
        data[key] = dict(_DEFAULTS)
    else:
        for k, v in _DEFAULTS.items():
            data[key].setdefault(k, v)
    return data[key]

def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()

def remaining(last: float, cooldown: int) -> float:
    return max(0.0, cooldown - (now_ts() - last))

def fmt_time(sec: float) -> str:
    s = int(sec)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    parts: list[str] = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

def daily_bonus_total(user: dict) -> int:
    return sum(SHOP_ITEMS[i]["daily_bonus"] for i in user["items"] if i in SHOP_ITEMS)

def resolve_amount(raw: str, cash: int) -> Optional[int]:
    if raw.strip().lower() == "all":
        return cash
    try:
        return int(raw)
    except ValueError:
        return None

# ═══════════════════════════════════════════════════════
#  BOT + GLOBAL STATE
# ═══════════════════════════════════════════════════════
intents = discord.Intents.default()
bot     = commands.Bot(command_prefix="!", intents=intents)
tree    = bot.tree

active_games: dict[int, dict] = {}
posted_rss:   set[str]        = set()

# ═══════════════════════════════════════════════════════
#  RSS TASK
# ═══════════════════════════════════════════════════════
@tasks.loop(seconds=60)
async def rss_task():
    if not RSS_CHANNEL_ID:
        return
    channel = bot.get_channel(RSS_CHANNEL_ID)
    if channel is None:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RSS_FEED_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                raw = await resp.text()
    except Exception:
        return

    feed    = feedparser.parse(raw)
    entries = [e for e in feed.entries if e.get("id", e.get("link", "")) not in posted_rss]

    for entry in reversed(entries[:5]):
        eid     = entry.get("id", entry.get("link", ""))
        title   = entry.get("title", "No title")[:256]
        link    = entry.get("link", "")
        summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:400] or "No description."

        embed = discord.Embed(
            title=f"📰 {title}", url=link, description=summary,
            color=0x5865F2, timestamp=datetime.now(timezone.utc),
        )
        media = entry.get("media_content") or entry.get("media_thumbnail")
        if isinstance(media, list) and media and media[0].get("url"):
            embed.set_image(url=media[0]["url"])
        embed.set_footer(text="📡 RSS Feed Update")
        try:
            await channel.send(embed=embed)
        except Exception:
            pass
        posted_rss.add(eid)

# ═══════════════════════════════════════════════════════
#  BLACKJACK — EMBED BUILDER
# ═══════════════════════════════════════════════════════
def bj_embed(game: dict, title: str = "🎴 Blackjack", color: int = 0xF1C40F) -> discord.Embed:
    pv = hand_total(game["player"])
    if game["reveal"]:
        dv_str    = str(hand_total(game["dealer"]))
        d_display = hand_display(game["dealer"])
    else:
        dv_str    = "?"
        d_display = f"{game['dealer'][0]}  🂠"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name=f"♠️ Your Hand  ({pv})",          value=hand_display(game["player"]), inline=False)
    embed.add_field(name=f"🃏 Dealer's Hand  ({dv_str})",   value=d_display,                   inline=False)
    embed.add_field(name="💰 Bet",       value=f"${game['bet']:,}",       inline=True)
    embed.add_field(name="💵 Your Cash", value=f"${game['cash_snap']:,}", inline=True)
    return embed

# ═══════════════════════════════════════════════════════
#  BLACKJACK — BUTTONS
# ═══════════════════════════════════════════════════════
class BjView(discord.ui.View):
    def __init__(self, uid: int, data: dict):
        super().__init__(timeout=120)
        self.uid  = uid
        self.data = data

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.uid:
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return False
        return True

    def lock_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id != "bj_help":
                child.disabled = True

    async def resolve(self, interaction: discord.Interaction, outcome: str):
        game = active_games.get(self.uid)
        if not game:
            return
        game["reveal"] = True
        data = load_data()
        user = get_user(data, self.uid)

        if outcome == "win":
            user["cash"] += game["bet"]
            title, color = f"🎉 You Win!  +${game['bet']:,}", 0x2ECC71
        elif outcome == "bj":
            prize = int(game["bet"] * 1.5)
            user["cash"] += prize
            title, color = f"🎰 Blackjack!  +${prize:,}", 0xF1C40F
        elif outcome == "lose":
            title, color = f"😭 You Lose!  -${game['bet']:,}", 0xE74C3C
        else:  # push
            user["cash"] += game["bet"]
            title, color = "🤝 Push — Bet Returned", 0x95A5A6

        game["cash_snap"] = user["cash"]
        save_data(data)
        self.lock_buttons()
        active_games.pop(self.uid, None)
        await interaction.response.edit_message(embed=bj_embed(game, title, color), view=self)

    async def dealer_turn(self, interaction: discord.Interaction):
        game = active_games[self.uid]
        while hand_total(game["dealer"]) < 17:
            game["dealer"].append(draw_card(game["deck"]))
        pv, dv = hand_total(game["player"]), hand_total(game["dealer"])
        if   dv > 21 or pv > dv: outcome = "win"
        elif pv < dv:             outcome = "lose"
        else:                     outcome = "push"
        await self.resolve(interaction, outcome)

    @discord.ui.button(label="Hit 🟢",         style=discord.ButtonStyle.success,   custom_id="bj_hit")
    async def hit(self, interaction: discord.Interaction, _: discord.ui.Button):
        game = active_games[self.uid]
        game["player"].append(draw_card(game["deck"]))
        pv = hand_total(game["player"])
        if   pv > 21: await self.resolve(interaction, "lose")
        elif pv == 21: await self.dealer_turn(interaction)
        else: await interaction.response.edit_message(embed=bj_embed(game), view=self)

    @discord.ui.button(label="Stand 🔵",        style=discord.ButtonStyle.primary,   custom_id="bj_stand")
    async def stand(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.dealer_turn(interaction)

    @discord.ui.button(label="Double Down 🔴",  style=discord.ButtonStyle.danger,    custom_id="bj_double")
    async def double_down(self, interaction: discord.Interaction, _: discord.ui.Button):
        game = active_games[self.uid]
        data = load_data()
        user = get_user(data, self.uid)
        if user["cash"] < game["bet"]:
            await interaction.response.send_message("❌ Not enough cash to double down!", ephemeral=True)
            return
        user["cash"]      -= game["bet"]
        game["bet"]       *= 2
        game["cash_snap"]  = user["cash"]
        save_data(data)
        game["player"].append(draw_card(game["deck"]))
        if hand_total(game["player"]) > 21:
            await self.resolve(interaction, "lose")
        else:
            await self.dealer_turn(interaction)

    @discord.ui.button(label="Help ⚪",         style=discord.ButtonStyle.secondary, custom_id="bj_help")
    async def help_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        e = discord.Embed(title="🎴 Blackjack — How to Play", color=0x3498DB)
        e.add_field(name="🃏 Goal",    value="Beat the dealer. Get closer to **21** without busting.", inline=False)
        e.add_field(name="🂠 Cards",   value="**Ace** = 1 or 11  |  **J/Q/K** = 10  |  Others = face value", inline=False)
        e.add_field(name="🎮 Buttons", value=(
            "**Hit 🟢** — Draw a card\n"
            "**Stand 🔵** — End turn; dealer plays\n"
            "**Double Down 🔴** — Double bet, 1 card, auto-stand\n"
            "**Help ⚪** — Show this (only visible to you)"
        ), inline=False)
        e.add_field(name="🏆 Payouts", value=(
            "**Win** → +100% bet\n"
            "**Blackjack** → +150% bet\n"
            "**Tie** → bet returned\n"
            "**Lose** → bet lost"
        ), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def on_timeout(self):
        game = active_games.pop(self.uid, None)
        if not game:
            return
        data = load_data()
        user = get_user(data, self.uid)
        user["cash"] += game["bet"]   # refund on timeout
        save_data(data)

# ═══════════════════════════════════════════════════════
#  SLASH COMMANDS — ECONOMY
# ═══════════════════════════════════════════════════════

@tree.command(name="cash", description="💰 Check your wallet and bank balance")
async def cmd_cash(interaction: discord.Interaction):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    e    = discord.Embed(title="💰 Your Balance", color=0xF1C40F)
    e.set_thumbnail(url=interaction.user.display_avatar.url)
    e.add_field(name="👛 Cash",  value=f"${u['cash']:,}",            inline=True)
    e.add_field(name="🏦 Bank",  value=f"${u['bank']:,}",            inline=True)
    e.add_field(name="💎 Total", value=f"${u['cash']+u['bank']:,}",  inline=True)
    await interaction.response.send_message(embed=e)


@tree.command(name="dep", description="🏦 Deposit cash into your bank")
@app_commands.describe(amount="Amount to deposit (or 'all')")
async def cmd_dep(interaction: discord.Interaction, amount: str):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    amt  = resolve_amount(amount, u["cash"])
    if amt is None:
        return await interaction.response.send_message("❌ Enter a valid number or `all`.", ephemeral=True)
    if amt <= 0:
        return await interaction.response.send_message("❌ Must be a positive amount.", ephemeral=True)
    if amt > u["cash"]:
        return await interaction.response.send_message(f"❌ You only have **${u['cash']:,}** in cash.", ephemeral=True)
    u["cash"] -= amt
    u["bank"]  += amt
    save_data(data)
    e = discord.Embed(title="🏦 Deposit Successful!", color=0x2ECC71)
    e.add_field(name="➕ Deposited", value=f"${amt:,}",       inline=True)
    e.add_field(name="👛 Cash",      value=f"${u['cash']:,}", inline=True)
    e.add_field(name="🏦 Bank",      value=f"${u['bank']:,}", inline=True)
    await interaction.response.send_message(embed=e)


@tree.command(name="daily", description="🎁 Claim your daily reward (24h cooldown)")
async def cmd_daily(interaction: discord.Interaction):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    rem  = remaining(u["last_daily"], 86_400)
    if rem > 0:
        return await interaction.response.send_message(f"⏳ Come back in **{fmt_time(rem)}**.", ephemeral=True)
    base  = 200
    bonus = daily_bonus_total(u)
    total = base + bonus
    u["cash"]      += total
    u["last_daily"] = now_ts()
    save_data(data)
    e = discord.Embed(title="🎁 Daily Reward Claimed!", color=0xF1C40F)
    e.add_field(name="💵 Base",  value=f"${base:,}",  inline=True)
    e.add_field(name="✨ Bonus", value=f"${bonus:,}", inline=True)
    e.add_field(name="💰 Total", value=f"${total:,}", inline=True)
    e.set_footer(text="Come back in 24 hours! 🕐")
    await interaction.response.send_message(embed=e)


@tree.command(name="work", description="💼 Work a shift for cash (30 min cooldown)")
async def cmd_work(interaction: discord.Interaction):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    rem  = remaining(u["last_work"], 1_800)
    if rem > 0:
        return await interaction.response.send_message(f"⏳ You're exhausted! Rest for **{fmt_time(rem)}**.", ephemeral=True)
    jobs = [
        ("delivered pizzas 🍕",     random.randint(80,  200)),
        ("fixed computers 💻",       random.randint(100, 300)),
        ("walked dogs 🐶",           random.randint(50,  150)),
        ("drove Uber 🚗",            random.randint(90,  250)),
        ("freelanced a logo 🎨",     random.randint(150, 400)),
        ("sold lemonade 🍋",         random.randint(40,  120)),
        ("streamed video games 🎮",  random.randint(60,  180)),
        ("tutored students 📚",      random.randint(100, 280)),
    ]
    job, earned = random.choice(jobs)
    u["cash"]     += earned
    u["last_work"]  = now_ts()
    save_data(data)
    e = discord.Embed(title="💼 Work Complete!", description=f"You {job} and earned **${earned:,}**! 🎉", color=0x3498DB)
    e.add_field(name="👛 New Balance", value=f"${u['cash']:,}")
    await interaction.response.send_message(embed=e)


@tree.command(name="crime", description="🦹 Attempt a crime for big money (1h cooldown)")
async def cmd_crime(interaction: discord.Interaction):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    rem  = remaining(u["last_crime"], 3_600)
    if rem > 0:
        return await interaction.response.send_message(f"⏳ Lay low for **{fmt_time(rem)}**.", ephemeral=True)
    u["last_crime"] = now_ts()
    if random.random() < 0.45:
        earned = random.randint(300, 1_000)
        u["cash"] += earned
        acts = [
            "robbed a vending machine 🏧", "hacked a crypto wallet 💻",
            "scammed a crypto bro 🐸",     "pickpocketed a tourist 🎒",
            "fenced stolen watches ⌚",
        ]
        e = discord.Embed(title="🦹 Crime Succeeded!", description=f"You {random.choice(acts)} and pocketed **${earned:,}**!", color=0x2ECC71)
    else:
        fine = max(1, int(u["cash"] * 0.25))
        u["cash"] -= fine
        e = discord.Embed(title="🚔 Busted!", description=f"You got caught and paid **${fine:,}** in fines! 😭", color=0xE74C3C)
    save_data(data)
    e.add_field(name="👛 Balance", value=f"${u['cash']:,}")
    await interaction.response.send_message(embed=e)


@tree.command(name="rob", description="🔫 Rob another user (risky!)")
@app_commands.describe(target="User to rob")
async def cmd_rob(interaction: discord.Interaction, target: discord.Member):
    data = load_data()
    u    = get_user(data, interaction.user.id)

    if target.bot:
        penalty = max(1, int(u["cash"] * 0.01))
        u["cash"] -= penalty
        save_data(data)
        e = discord.Embed(title="🤖 Nice Try!", description=f"🤖 Hey! Don't rob my friends! I took **${penalty:,}** from you as punishment. 😤", color=0xE74C3C)
        return await interaction.response.send_message(embed=e)

    if target.id == interaction.user.id:
        return await interaction.response.send_message("🤦 You can't rob yourself!", ephemeral=True)

    rem = remaining(u["last_rob"], 3_600)
    if rem > 0:
        return await interaction.response.send_message(f"⏳ Hiding from cops — wait **{fmt_time(rem)}**.", ephemeral=True)

    victim = get_user(data, target.id)
    if victim["cash"] == 0:
        return await interaction.response.send_message(f"💸 **{target.display_name}** is broke. Nothing to steal!", ephemeral=True)

    u["last_rob"] = now_ts()
    if random.random() < 0.45:
        pct    = random.uniform(0.10, 0.30)
        stolen = max(1, int(victim["cash"] * pct))
        victim["cash"] -= stolen
        u["cash"]      += stolen
        e = discord.Embed(title="🔫 Robbery Successful!", description=f"You robbed **{target.display_name}** and stole **${stolen:,}**! 💰", color=0x2ECC71)
    else:
        fine = max(1, int(u["cash"] * 0.10))
        u["cash"] -= fine
        e = discord.Embed(title="🚔 Caught Red-Handed!", description=f"You failed to rob **{target.display_name}** and lost **${fine:,}**. 😭", color=0xE74C3C)
    save_data(data)
    e.add_field(name="👛 Your Balance", value=f"${u['cash']:,}")
    await interaction.response.send_message(embed=e)


@tree.command(name="roulette", description="🎡 Bet on red or black — double or nothing!")
@app_commands.describe(amount="Bet amount or 'all'", color="red or black")
@app_commands.choices(color=[
    app_commands.Choice(name="🔴 Red",   value="red"),
    app_commands.Choice(name="⚫ Black", value="black"),
])
async def cmd_roulette(interaction: discord.Interaction, amount: str, color: str = "red"):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    bet  = resolve_amount(amount, u["cash"])
    if bet is None or bet <= 0:
        return await interaction.response.send_message("❌ Enter a valid positive amount or `all`.", ephemeral=True)
    if bet > u["cash"]:
        return await interaction.response.send_message(f"❌ Not enough cash! You have **${u['cash']:,}**.", ephemeral=True)
    result = random.choice(["red", "black"])
    label  = "🔴 Red" if result == "red" else "⚫ Black"
    if result == color:
        u["cash"] += bet
        e = discord.Embed(title=f"🎡 Roulette — {label}!", description=f"🎉 Correct! You won **${bet:,}**!", color=0x2ECC71)
    else:
        u["cash"] -= bet
        e = discord.Embed(title=f"🎡 Roulette — {label}!", description=f"😭 Wrong! You lost **${bet:,}**.", color=0xE74C3C)
    save_data(data)
    e.add_field(name="👛 Balance", value=f"${u['cash']:,}")
    await interaction.response.send_message(embed=e)


@tree.command(name="shop", description="🛒 Browse the item shop")
async def cmd_shop(interaction: discord.Interaction):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    e    = discord.Embed(title="🛒 Item Shop", description="Use `/buy <item name>` to purchase!", color=0xE67E22)
    for name, info in SHOP_ITEMS.items():
        status = "✅ **Owned**" if name in u["items"] else f"**${info['price']:,}**"
        e.add_field(
            name  = f"{info['emoji']} {name}",
            value = f"{info['desc']}\nPrice: {status}\nDaily Bonus: +${info['daily_bonus']:,}",
            inline= True,
        )
    await interaction.response.send_message(embed=e)


@tree.command(name="buy", description="🛍️ Buy an item from the shop")
@app_commands.describe(item="Name of the item to buy")
async def cmd_buy(interaction: discord.Interaction, item: str):
    matched = next((k for k in SHOP_ITEMS if k.lower() == item.lower()), None)
    if not matched:
        return await interaction.response.send_message(f"❌ **{item}** not found. Use `/shop`.", ephemeral=True)
    data = load_data()
    u    = get_user(data, interaction.user.id)
    if matched in u["items"]:
        return await interaction.response.send_message(f"❌ You already own **{matched}**!", ephemeral=True)
    price = SHOP_ITEMS[matched]["price"]
    if u["cash"] < price:
        return await interaction.response.send_message(f"❌ Need **${price:,}** — you have **${u['cash']:,}**.", ephemeral=True)
    u["cash"] -= price
    u["items"].append(matched)
    save_data(data)
    info = SHOP_ITEMS[matched]
    e = discord.Embed(title="🛍️ Purchase Complete!", color=0x2ECC71)
    e.add_field(name=f"{info['emoji']} Item",  value=matched,                         inline=True)
    e.add_field(name="💸 Spent",               value=f"${price:,}",                  inline=True)
    e.add_field(name="✨ Daily Bonus",          value=f"+${info['daily_bonus']:,}/day",inline=True)
    e.add_field(name="👛 Remaining Cash",       value=f"${u['cash']:,}",              inline=True)
    await interaction.response.send_message(embed=e)


@tree.command(name="inventory", description="🎒 View your owned items")
async def cmd_inventory(interaction: discord.Interaction):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    e    = discord.Embed(title="🎒 Your Inventory", color=0x9B59B6)
    e.set_thumbnail(url=interaction.user.display_avatar.url)
    if not u["items"]:
        e.description = "Your inventory is empty! Visit `/shop` to buy items."
    else:
        total_bonus = 0
        for name in u["items"]:
            if name in SHOP_ITEMS:
                info = SHOP_ITEMS[name]
                e.add_field(name=f"{info['emoji']} {name}", value=f"+${info['daily_bonus']:,}/day", inline=True)
                total_bonus += info["daily_bonus"]
        e.set_footer(text=f"Total daily bonus from items: +${total_bonus:,}")
    await interaction.response.send_message(embed=e)


@tree.command(name="leaderboard", description="🏆 Top 10 richest users by total wealth")
async def cmd_leaderboard(interaction: discord.Interaction):
    data = load_data()
    if not data:
        e = discord.Embed(title="🏆 Leaderboard", description="No users yet. Start with `/daily`!", color=0xF1C40F)
        return await interaction.response.send_message(embed=e)

    ranked = sorted(
        data.items(),
        key    = lambda kv: kv[1].get("cash", 0) + kv[1].get("bank", 0),
        reverse= True,
    )[:10]

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines: list[str] = []
    for i, (uid, udata) in enumerate(ranked):
        total  = udata.get("cash", 0) + udata.get("bank", 0)
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        name   = member.display_name if member else f"User {uid}"
        lines.append(f"{medals[i]} **{name}** — ${total:,}")

    e = discord.Embed(title="🏆 Wealth Leaderboard — Top 10", description="\n".join(lines), color=0xF1C40F)
    e.set_footer(text="Total wealth = Cash + Bank")
    await interaction.response.send_message(embed=e)

# ═══════════════════════════════════════════════════════
#  BLACKJACK COMMAND
# ═══════════════════════════════════════════════════════
@tree.command(name="blackjack", description="🎴 Play Blackjack against the dealer")
@app_commands.describe(amount="Bet amount or 'all'")
async def cmd_blackjack(interaction: discord.Interaction, amount: str):
    data = load_data()
    u    = get_user(data, interaction.user.id)
    bet  = resolve_amount(amount, u["cash"])

    if interaction.user.id in active_games:
        return await interaction.response.send_message("🎴 You already have an active game! Finish it first.", ephemeral=True)
    if bet is None or bet <= 0:
        return await interaction.response.send_message("❌ Enter a valid positive amount or `all`.", ephemeral=True)
    if bet > u["cash"]:
        return await interaction.response.send_message(f"❌ Not enough cash! You have **${u['cash']:,}**.", ephemeral=True)

    u["cash"] -= bet
    save_data(data)

    deck = build_deck()
    random.shuffle(deck)
    game: dict = {
        "player":    [draw_card(deck), draw_card(deck)],
        "dealer":    [draw_card(deck), draw_card(deck)],
        "deck":      deck,
        "bet":       bet,
        "cash_snap": u["cash"],
        "reveal":    False,
    }
    active_games[interaction.user.id] = game

    # natural blackjack on deal
    if hand_total(game["player"]) == 21:
        game["reveal"] = True
        prize = int(bet * 1.5)
        u["cash"] += bet + prize
        save_data(data)
        game["cash_snap"] = u["cash"]
        active_games.pop(interaction.user.id, None)
        return await interaction.response.send_message(embed=bj_embed(game, f"🎰 Natural Blackjack!  +${prize:,}", 0xF1C40F))

    view = BjView(interaction.user.id, data)
    await interaction.response.send_message(embed=bj_embed(game), view=view)

# ═══════════════════════════════════════════════════════
#  ADMIN COMMANDS
# ═══════════════════════════════════════════════════════
def is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.user.guild_permissions.administrator)

@tree.command(name="givecash", description="🏧 [Admin] Give cash to a user")
@app_commands.describe(target="Recipient", amount="Amount of cash to give")
async def cmd_givecash(interaction: discord.Interaction, target: discord.Member, amount: int):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
    data = load_data()
    u    = get_user(data, target.id)
    u["cash"] += amount
    save_data(data)
    e = discord.Embed(title="🏧 Cash Given!", description=f"Gave **${amount:,}** to {target.mention}.", color=0x2ECC71)
    e.add_field(name="💰 Their New Cash", value=f"${u['cash']:,}")
    await interaction.response.send_message(embed=e)

@tree.command(name="giveitem", description="🎁 [Admin] Give an item to a user")
@app_commands.describe(target="Recipient", item="Item name")
async def cmd_giveitem(interaction: discord.Interaction, target: discord.Member, item: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
    matched = next((k for k in SHOP_ITEMS if k.lower() == item.lower()), None)
    if not matched:
        return await interaction.response.send_message(f"❌ Item **{item}** not found.", ephemeral=True)
    data = load_data()
    u    = get_user(data, target.id)
    if matched in u["items"]:
        return await interaction.response.send_message(f"❌ {target.display_name} already owns **{matched}**.", ephemeral=True)
    u["items"].append(matched)
    save_data(data)
    info = SHOP_ITEMS[matched]
    e = discord.Embed(title="🎁 Item Given!", description=f"Gave **{info['emoji']} {matched}** to {target.mention}.", color=0x2ECC71)
    await interaction.response.send_message(embed=e)

# ═══════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════
@bot.event
async def on_ready():
    await tree.sync()
    if not rss_task.is_running():
        rss_task.start()
    print(f"✅  Logged in as {bot.user}")
    print(f"    Guilds  : {len(bot.guilds)}")
    print(f"    RSS URL : {RSS_FEED_URL}")
    print(f"    RSS Chan: {RSS_CHANNEL_ID}")

# ═══════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    bot.run(TOKEN)
