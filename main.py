import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta
import logging
import random
import string
import pytz
import asyncio
import io
import zipfile
import hashlib
from typing import Optional

# ═══════════════════════════════════════════════════════
#   ADMIN KONFIGURATION
# ═══════════════════════════════════════════════════════
ADMIN_USER_IDS = [
    1211683189186105434,
]

GERMANY_TZ = pytz.timezone('Europe/Berlin')

def get_now() -> datetime:
    return datetime.now(GERMANY_TZ)

def make_aware(dt: datetime) -> datetime:
    """Stellt sicher, dass ein datetime-Objekt timezone-aware ist (Europe/Berlin)."""
    if dt.tzinfo is None:
        return GERMANY_TZ.localize(dt)
    return dt.astimezone(GERMANY_TZ)

# ═══════════════════════════════════════════════════════
#   LOGGING
# ═══════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('insurance_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('InsuranceBot')

# ═══════════════════════════════════════════════════════
#   BOT SETUP
# ═══════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "insurance_data.json"
CONFIG_FILE = "bot_config.json"

# ═══════════════════════════════════════════════════════
#   STANDARD-KONFIGURATION
# ═══════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "log_channel_id": None,
    "kundenkontakt_category_id": None,
    "schadensmeldung_category_id": None,
    "auszahlung_channel_id": None,
    "kundenkontakt_channel_id": None,
    "schadensmeldung_channel_id": None,
    "steuer_prozent": 3.0,
    "insurance_types": {
        "Krankenversicherung (Privat)": {
            "price": 10000.00,
            "role": "Krankenversicherung (Privat)",
            "auszahlung_limit": 20000.00,
            "enabled": True
        },
        "Haftpflichtversicherung": {
            "price": 8000.00,
            "role": "Haftpflichtversicherung",
            "auszahlung_limit": 16000.00,
            "enabled": True
        },
        "Hausratversicherung": {
            "price": 7500.00,
            "role": "Hausratversicherung",
            "auszahlung_limit": 15000.00,
            "enabled": True
        },
        "Kfz-Versicherung": {
            "price": 7500.00,
            "role": "Kfz-Versicherung",
            "auszahlung_limit": 15000.00,
            "enabled": True
        },
        "Rechtsschutzversicherung": {
            "price": 10000.00,
            "role": "Rechtsschutzversicherung",
            "auszahlung_limit": 10000.00,
            "enabled": True
        },
        "Berufsunfähigkeitsversicherung": {
            "price": 7500.00,
            "role": "Berufsunfähigkeitsversicherung",
            "auszahlung_limit": 15000.00,
            "enabled": True
        },
        "Bußgeldversicherung": {
            "price": 12000.00,
            "role": "Bußgeldversicherung",
            "auszahlung_limit": 24000.00,
            "enabled": True
        }
    }
}

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        for key, val in DEFAULT_CONFIG.items():
            if key not in loaded:
                loaded[key] = val
        if "insurance_types" not in loaded:
            loaded["insurance_types"] = DEFAULT_CONFIG["insurance_types"]
        return loaded
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

config = load_config()

def get_insurance_types() -> dict:
    return {k: v for k, v in config.get("insurance_types", {}).items() if v.get("enabled", True)}

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        for key in ("schadensmeldungen", "pending_auszahlungen", "customers", "invoices"):
            if key not in d:
                d[key] = {}
        if "logs" not in d:
            d["logs"] = []
        logger.info("Daten erfolgreich geladen")
        return d
    logger.warning("Keine Datendatei gefunden, erstelle neue Datenstruktur")
    return {"customers": {}, "invoices": {}, "logs": [], "schadensmeldungen": {}, "pending_auszahlungen": {}}

def save_data(d: dict):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=4, ensure_ascii=False)
    global _last_data_hash
    _last_data_hash = _get_data_hash()

def _get_data_hash() -> str:
    if not os.path.exists(DATA_FILE):
        return ""
    with open(DATA_FILE, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

_last_data_hash: str = ""

# ═══════════════════════════════════════════════════════
#   ID GENERATOREN
# ═══════════════════════════════════════════════════════
def generate_customer_id() -> str:
    return f"VN-{get_now().strftime('%y')}{''.join(random.choices(string.digits, k=6))}"

def generate_invoice_id() -> str:
    return f"RE-{get_now().strftime('%y%m')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def generate_schaden_id() -> str:
    return f"SM-{get_now().strftime('%y%m')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def generate_auszahlung_id() -> str:
    return f"AZ-{get_now().strftime('%y%m')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

# ═══════════════════════════════════════════════════════
#   HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════
async def send_to_log_channel(guild: discord.Guild, embed: discord.Embed):
    if config.get("log_channel_id"):
        try:
            log_channel = guild.get_channel(config["log_channel_id"])
            if log_channel:
                await log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Fehler beim Senden an Log-Channel: {e}")

def create_backup() -> str | None:
    try:
        os.makedirs("backups", exist_ok=True)
        timestamp = get_now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"backups/backup_{timestamp}.json"
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data_to_backup = json.load(f)
            with open(backup_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_backup, f, indent=4, ensure_ascii=False)
        return backup_path
    except Exception as e:
        logger.error(f"Fehler beim Erstellen des Backups: {e}")
        return None

def add_log_entry(action: str, user_id: int, details: dict):
    log_entry = {
        "timestamp": get_now().isoformat(),
        "action": action,
        "user_id": user_id,
        "details": details
    }
    data['logs'].append(log_entry)
    save_data(data)

def get_verfuegbares_guthaben(customer_id: str, versicherung: str) -> float:
    insurance_types = get_insurance_types()
    limit = insurance_types.get(versicherung, {}).get("auszahlung_limit", 0.0)
    customer = data['customers'].get(customer_id, {})
    bereits_ausgezahlt = customer.get("auszahlungen", {}).get(versicherung, 0.0)
    return max(0.0, limit - bereits_ausgezahlt)

def create_zip_buffer() -> io.BytesIO:
    """Erstellt einen ZIP-Buffer mit den aktuellen Datenbankdateien."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(DATA_FILE):
            zf.write(DATA_FILE, arcname="insurance_data.json")
        if os.path.exists(CONFIG_FILE):
            zf.write(CONFIG_FILE, arcname="bot_config.json")
    buf.seek(0)
    return buf

# ═══════════════════════════════════════════════════════
#   FARBEN, ROLLEN & KONSTANTEN
# ═══════════════════════════════════════════════════════
COLOR_PRIMARY = 0x2C3E50
COLOR_SUCCESS = 0x27AE60
COLOR_WARNING = 0xE67E22
COLOR_ERROR   = 0xC0392B
COLOR_INFO    = 0x3498DB
COLOR_DAMAGE  = 0xE74C3C

MITARBEITER_ROLE_ID    = 1408800823571513537
LEITUNGSEBENE_ROLE_ID  = 1408797319134187601
FIRMENKONTOROLLE_ROLE_ID = 1474047313025433684
KUNDEN_ROLE_NAME       = "Versicherungsnehmer"

FOOTER_ICON  = "https://images-ext-1.discordapp.net/external/_NQeYTDa5DucCWkxX23sxJcv2_ZpH5VF1eYytQ7PV3Q/%3Fsize%3D4096/https/cdn.discordapp.com/avatars/1482775773873176659/2023112d4ceae38bbe94f085d2ffefe6.png?format=webp&quality=lossless&width=291&height=291"
AUTOMOD_ICON = "https://media.discordapp.net/attachments/1473692441726029874/1473692787156455474/1072-automod.png?ex=699722dc&is=6995d15c&hm=08ad340d3673e1f1076cbf73d235ea3b0e8ef10b07abb8d24ea66d85c6b59edb&=&format=webp&quality=lossless&width=250&height=250"

# ═══════════════════════════════════════════════════════
#   BERECHTIGUNGSPRÜFUNGEN
# ═══════════════════════════════════════════════════════
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_USER_IDS

def is_mitarbeiter(interaction: discord.Interaction) -> bool:
    mitarbeiter_role   = interaction.guild.get_role(MITARBEITER_ROLE_ID)
    leitungsebene_role = interaction.guild.get_role(LEITUNGSEBENE_ROLE_ID)
    return (
        (mitarbeiter_role and mitarbeiter_role in interaction.user.roles) or
        (leitungsebene_role and leitungsebene_role in interaction.user.roles) or
        is_admin(interaction)
    )

def is_leitungsebene(interaction: discord.Interaction) -> bool:
    leitungsebene_role = interaction.guild.get_role(LEITUNGSEBENE_ROLE_ID)
    return (leitungsebene_role and leitungsebene_role in interaction.user.roles) or is_admin(interaction)

def is_firmenkontorolle(interaction: discord.Interaction) -> bool:
    firmenkontorolle_role = interaction.guild.get_role(FIRMENKONTOROLLE_ROLE_ID)
    return (firmenkontorolle_role and firmenkontorolle_role in interaction.user.roles) or is_admin(interaction)

def build_error_embed(title: str, description: str, needed_permission: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(title=title, description=f"> {description}", color=COLOR_ERROR)
    e.set_author(name="Automatische Berechtigungsprüfung", icon_url=AUTOMOD_ICON)
    if needed_permission:
        e.add_field(name="<:7842privacy:1484250743991959552> Benötigte Berechtigung", value=f"> `{needed_permission}`", inline=False)
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    return e

# ═══════════════════════════════════════════════════════
#   KUNDENAKTE EMBED BUILDER
# ═══════════════════════════════════════════════════════
def build_kundenakte_embed(customer_id: str, customer: dict) -> discord.Embed:
    insurance_types = get_insurance_types()
    total_price = customer.get("total_monthly_price", 0.0)
    embed = discord.Embed(title="Versicherungsakte", color=COLOR_PRIMARY, timestamp=get_now())
    embed.add_field(
        name="__Versicherungsnehmer__",
        value=f"> <:7549member:1484250740535988266> - {customer['rp_name']}\n> <:4189search:1484250713315086479> - `{customer_id}`",
        inline=False
    )
    embed.add_field(
        name="__Zahlungsmethoden__",
        value=f"> <:8312card:1484250748467281951> - `{customer['hbpay_nummer']}`\n> <:9847public:1484250777307447417> - `{customer['economy_id']}`",
        inline=False
    )
    insurance_text = "\n".join(
        f"> {ins}\n> ▸ `{insurance_types.get(ins, {}).get('price', 0.0):,.2f} €/Monat`"
        for ins in customer.get("versicherungen", [])
    )
    embed.add_field(name="__Abgeschlossene Versicherungen__", value=insurance_text or "> Keine", inline=False)
    embed.add_field(
        name="__Gesamtbeitrag (monatlich)__",
        value=f"<:912926arrow:1484250786639646760> **`{total_price:,.2f} €`**",
        inline=False
    )
    embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    return embed

async def update_forum_thread_embed(guild: discord.Guild, customer_id: str, customer: dict):
    """Aktualisiert das erste Embed im Kunden-Forum-Thread."""
    thread_id = customer.get("thread_id")
    if not thread_id:
        return
    try:
        thread = guild.get_thread(thread_id)
        if not thread:
            try:
                thread = await guild.fetch_channel(thread_id)
            except Exception:
                logger.warning(f"Thread {thread_id} nicht gefunden.")
                return

        messages = []
        async for msg in thread.history(limit=5, oldest_first=True):
            messages.append(msg)

        for msg in messages:
            if msg.author.id == (guild.me.id if guild.me else bot.user.id) and msg.embeds:
                new_embed = build_kundenakte_embed(customer_id, customer)
                await msg.edit(embed=new_embed)
                logger.info(f"Forum-Thread für {customer_id} aktualisiert")
                return
    except Exception as e:
        logger.error(f"Fehler beim Aktualisieren des Forum-Threads: {e}")

# ═══════════════════════════════════════════════════════
#   ON READY
# ═══════════════════════════════════════════════════════
@bot.event
async def on_ready():
    logger.info(f'{bot.user} erfolgreich gestartet')
    global _last_data_hash
    _last_data_hash = _get_data_hash()

    bot.add_view(KundenkontaktView())
    bot.add_view(SchadensmeldungView())
    bot.add_view(TicketCloseView(0, ""))
    bot.add_view(AuszahlungActionView("dummy", "dummy", 0.0))
    logger.info("Persistente Views registriert")

    try:
        synced = await bot.tree.sync()
        logger.info(f'{len(synced)} Slash Commands synchronisiert')
    except Exception as e:
        logger.error(f'Fehler beim Synchronisieren der Commands: {e}')

    await asyncio.sleep(1)
    if not check_invoices.is_running():
        check_invoices.start()
    if not auto_backup.is_running():
        auto_backup.start()

# ═══════════════════════════════════════════════════════
#   DATEN LADEN
# ═══════════════════════════════════════════════════════
data = load_data()

# ═══════════════════════════════════════════════════════
#   PING / STATUS COMMAND
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="ping", description="Zeigt den Status und die Latenz des Bots an")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    customers = data.get('customers', {})
    aktive            = sum(1 for c in customers.values() if c.get('status') == 'aktiv')
    archiviert        = sum(1 for c in customers.values() if c.get('status') == 'archiviert')
    offene_rechnungen = sum(1 for inv in data.get('invoices', {}).values() if not inv.get('paid'))
    ausstehende_az    = sum(1 for az in data.get('pending_auszahlungen', {}).values() if az.get('status') == 'ausstehend')

    if latency_ms < 100:
        color      = COLOR_SUCCESS
        status_val = f"> <:3518checkmark:1484250693421367389> **`{latency_ms} ms`** — Ausgezeichnet"
    elif latency_ms < 200:
        color      = COLOR_WARNING
        status_val = f"> <:3684sync:1473009462628323523> **`{latency_ms} ms`** — Gut"
    else:
        color      = COLOR_ERROR
        status_val = f"> <:3518crossmark:1473009455473098894> **`{latency_ms} ms`** — Langsam"

    embed = discord.Embed(
        title="InsuranceGuard v3 — Systemstatus",
        color=color,
        timestamp=get_now()
    )
    embed.add_field(name="__Verbindung__", value=status_val, inline=False)
    embed.add_field(
        name="__Kunden__",
        value=f"> <:2004preview:1484250684734832660> - **`{aktive}` Kunden**\n> <:7842private:1484250745149849842> - `{archiviert}` ehem. Kunden",
        inline=True
    )
    embed.add_field(
        name="__Offene Vorgänge__",
        value=f"> <:6224mail:1484250731098673243> - **`{offene_rechnungen}`** Rechnungen\n <:8312card:1484250748467281951> - **`{ausstehende_az}`** Auszahlungen",
        inline=True
    )
    embed.add_field(name="__Serverzeit__", value=f"> `{get_now().strftime('%d.%m.%Y, %H:%M:%S Uhr')}`", inline=False)
    embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ═══════════════════════════════════════════════════════
#   KUNDEN-SUCHE NACH NAME
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="kunden-suchen", description="Sucht nach Kunden anhand des RP-Namens oder der Kunden-ID")
@app_commands.describe(suchbegriff="RP-Name oder Kunden-ID (Teilsuche möglich)")
async def kunden_suchen(interaction: discord.Interaction, suchbegriff: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können die Kundensuche verwenden.", "Mitarbeiter"),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        suchbegriff_lower = suchbegriff.lower().strip()
        treffer = [
            (cid, c) for cid, c in data['customers'].items()
            if (
                suchbegriff_lower in cid.lower() or
                suchbegriff_lower in c.get('rp_name', '').lower() or
                suchbegriff_lower in c.get('hbpay_nummer', '').lower() or
                suchbegriff_lower in c.get('economy_id', '').lower()
            )
        ]

        if not treffer:
            embed = discord.Embed(
                title="Keine Suchergebnisse!",
                description=f"> Für den Suchbegriff `{suchbegriff}` wurden keine Kunden gefunden.",
                color=COLOR_WARNING,
                timestamp=get_now()
            )
            embed.add_field(
                name="__Suchtipps__",
                value="> ▸ Kunden-ID: `VN-26123456`\n> ▸ RP-Name: `Max Mustermann`\n> ▸ Kartennummer oder Economy-ID",
                inline=False
            )
            embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        anzeige = treffer[:10]
        embed = discord.Embed(
            title="Suchergebnisse",
            description=(
                f"> <:4189search:1484250713315086479> - `{suchbegriff}`\n"
                f"> <:7549member:1484250740535988266> - `{len(treffer)}` Kunde(n)"
                + (f"\n> <:7842privacy:1484250743991959552> Zeige: `{len(anzeige)} von {len(treffer)}`" if len(treffer) > 10 else "")
            ),
            color=COLOR_PRIMARY,
            timestamp=get_now()
        )

        for customer_id, customer in anzeige:
            status      = customer.get('status', 'aktiv')
            status_text = "<:3518checkmark:1484250693421367389> Aktiv" if status == 'aktiv' else "<:7842privacy:1473009500775776256> Archiviert"
            thread_id   = customer.get('thread_id')
            versicherungen = "\n".join(f"> ▸ {ins}" for ins in customer.get('versicherungen', [])) or "> ▸ Keine"

            embed.add_field(
                name=f"__{customer['rp_name']}__",
                value=(
                    f"> <:4189search:1484250713315086479> - `{customer_id}`\n"
                    f"> <:8312card:1484250748467281951> - `{customer['hbpay_nummer']}`\n"
                    f"> <:9847public:1484250777307447417> - `{customer['economy_id']}`\n"
                    f"> <:1158refresh:1484250680918151392> - {status_text}\n"
                    f"> <:9654dollar:1484250776049291324> - `{customer.get('total_monthly_price', 0):,.2f} €/Monat`\n"
                    + versicherungen[:200] + "\n"
                    + (f"> <:2141file:1484250686232461352> - <#{thread_id}>" if thread_id else "> <:2141file:1484250686232461352> - *Keine Akte gefunden!*")
                ),
                inline=False
            )
        footer_text = f"Zeige {len(anzeige)} von {len(treffer)} Ergebnissen • Copyright © InsuranceGuard v3" if len(treffer) > 10 else "Copyright © InsuranceGuard v3"
        embed.set_footer(text=footer_text, icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Fehler Kundensuche: {e}", exc_info=True)
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

# ═══════════════════════════════════════════════════════
#   VERSICHERUNG KÜNDIGEN
# ═══════════════════════════════════════════════════════
class KuendigungSelect(discord.ui.Select):
    def __init__(self, customer_id: str, customer: dict):
        self.customer_id = customer_id
        self.customer_data = customer
        insurance_types = get_insurance_types()
        options = [
            discord.SelectOption(
                label=ins,
                description=f"Beitrag: {insurance_types.get(ins, {}).get('price', 0):,.2f} €/Mo",
                value=ins
            )
            for ins in customer.get("versicherungen", [])
        ]
        super().__init__(
            placeholder="Zu kündigende Versicherung wählen...",
            min_values=1,
            max_values=len(options),
            options=options,
            custom_id="kuendigung_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        for item in view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = False
        insurance_types = get_insurance_types()
        wegfall   = sum(insurance_types.get(ins, {}).get('price', 0) for ins in self.values)
        neu_total = max(0, data['customers'][self.customer_id]['total_monthly_price'] - wegfall)
        kuendig_text = "\n".join(f"> <:3518crossmark:1473009455473098894> {ins}" for ins in self.values)

        e = discord.Embed(
            title="Kündigung bestätigen",
            description="> Bitte prüfen Sie die Angaben und bestätigen Sie die Kündigung.",
            color=COLOR_WARNING,
            timestamp=get_now()
        )
        e.add_field(name="__Zu kündigende Versicherungen__", value=kuendig_text, inline=False)
        e.add_field(
            name="__Beitragsänderung__",
            value=(
                f"> <:3518crossmark:1473009455473098894> Wegfall: `-{wegfall:,.2f} €`\n"
                f"> <:912926arrow:1484250786639646760> Neuer Beitrag: **`{neu_total:,.2f} €`**"
            ),
            inline=False
        )
        e.set_footer(text="Diese Aktion kann nicht rückgängig gemacht werden • Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.response.edit_message(embed=e, view=view)

class KuendigungView(discord.ui.View):
    def __init__(self, customer_id: str, customer: dict):
        super().__init__(timeout=120)
        self.confirmed = False
        self._select = KuendigungSelect(customer_id, customer)
        self.add_item(self._select)

        btn = discord.ui.Button(label="Kündigung bestätigen", style=discord.ButtonStyle.danger, disabled=True, emoji="<:3518crossmark:1473009455473098894>")
        btn.callback = self._confirm
        self.add_item(btn)

    async def _confirm(self, interaction: discord.Interaction):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

@bot.tree.command(name="versicherung-kuendigen", description="Kündigt eine oder mehrere Versicherungen eines Kunden")
@app_commands.describe(customer_id="Versicherungsnehmer-ID des Kunden")
async def versicherung_kuendigen(interaction: discord.Interaction, customer_id: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Versicherungen kündigen.", "Mitarbeiter"),
            ephemeral=True
        )
        return

    if customer_id not in data['customers']:
        await interaction.response.send_message(
            embed=build_error_embed("Nicht gefunden!", f"Keine Kundenakte mit ID `{customer_id}`."),
            ephemeral=True
        )
        return

    customer = data['customers'][customer_id]
    if not customer.get("versicherungen"):
        await interaction.response.send_message(
            embed=discord.Embed(title="Keine Versicherungen!", description="> Dieser Kunde hat keine aktiven Versicherungen.", color=COLOR_INFO),
            ephemeral=True
        )
        return

    insurance_types = get_insurance_types()
    versicherungen_text = "\n".join(
        f"> <:3518checkmark:1484250693421367389> {ins} — `{insurance_types.get(ins, {}).get('price', 0):,.2f} €/Mo`"
        for ins in customer.get('versicherungen', [])
    )

    view = KuendigungView(customer_id, customer)
    e = discord.Embed(
        title="Versicherung kündigen",
        description="> Bitte wählen Sie die zu kündigenden Versicherungen aus dem Dropdown-Menü.",
        color=COLOR_WARNING,
        timestamp=get_now()
    )
    e.add_field(
        name="__Versicherungsnehmer__",
        value=f"> <:7549member:1484250740535988266> - {customer['rp_name']}\n> <:4189search:1484250713315086479> - `{customer_id}`",
        inline=False
    )
    e.add_field(name="__Aktive Versicherungen__", value=versicherungen_text or "> Keine", inline=False)
    e.add_field(
        name="__Aktueller Monatsbeitrag__",
        value=f"> <:912926arrow:1484250786639646760> **`{customer.get('total_monthly_price', 0):,.2f} €`**",
        inline=False
    )
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=e, view=view, ephemeral=True)
    await view.wait()

    if not view.confirmed:
        await interaction.edit_original_response(
            embed=discord.Embed(title="Kündigung abgebrochen.", color=COLOR_INFO),
            view=None
        )
        return

    to_cancel = view._select.values
    if not to_cancel:
        return

    insurance_types = get_insurance_types()
    wegfall = sum(insurance_types.get(ins, {}).get('price', 0) for ins in to_cancel)

    for ins in to_cancel:
        if ins in data['customers'][customer_id]['versicherungen']:
            data['customers'][customer_id]['versicherungen'].remove(ins)
        data['customers'][customer_id].get('auszahlungen', {}).pop(ins, None)

    new_total = sum(
        insurance_types.get(ins, {}).get('price', 0)
        for ins in data['customers'][customer_id]['versicherungen']
    )
    data['customers'][customer_id]['total_monthly_price'] = new_total
    save_data(data)

    await update_forum_thread_embed(interaction.guild, customer_id, data['customers'][customer_id])

    member = interaction.guild.get_member(customer['discord_user_id'])
    if member:
        for ins in to_cancel:
            role_name = insurance_types.get(ins, {}).get("role", ins)
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role and role in member.roles:
                await member.remove_roles(role)

        if not data['customers'][customer_id]['versicherungen']:
            kunden_role = discord.utils.get(interaction.guild.roles, name=KUNDEN_ROLE_NAME)
            if kunden_role and kunden_role in member.roles:
                await member.remove_roles(kunden_role)

    thread_id = customer.get('thread_id')
    if thread_id:
        try:
            thread = interaction.guild.get_thread(thread_id)
            if not thread:
                thread = await interaction.guild.fetch_channel(thread_id)
            if thread:
                vermerk = discord.Embed(title="Kündigungsvermerk", color=COLOR_ERROR, timestamp=get_now())
                vermerk.add_field(name="Gekündigte Versicherungen", value="\n".join(f"> ❌ {ins}" for ins in to_cancel), inline=False)
                vermerk.add_field(name="Neuer Monatsbeitrag", value=f"> `{new_total:,.2f} €`", inline=True)
                vermerk.add_field(name="Durchgeführt von", value=f"> {interaction.user.mention}", inline=True)
                vermerk.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
                await thread.send(embed=vermerk)
        except Exception as e:
            logger.error(f"Fehler beim Thread-Eintrag: {e}")

    if member:
        dm_embed = discord.Embed(title="Versicherungskündigung", description="> Folgende Versicherungen wurden aus Ihrem Vertrag entfernt.", color=COLOR_ERROR, timestamp=get_now())
        dm_embed.add_field(name="Gekündigte Versicherungen", value="\n".join(f"> ❌ {ins}" for ins in to_cancel), inline=False)
        dm_embed.add_field(name="Neuer Monatsbeitrag", value=f"> `{new_total:,.2f} €`", inline=False)
        dm_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        try:
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    add_log_entry("VERSICHERUNG_GEKUENDIGT", interaction.user.id, {
        "customer_id": customer_id,
        "gekuendigte_versicherungen": list(to_cancel),
        "neuer_beitrag": new_total
    })

    log_embed = discord.Embed(title="Versicherung(en) gekündigt!", color=COLOR_ERROR, timestamp=get_now())
    log_embed.add_field(name="__Versicherungsnehmer__", value=f"> <:7549member:1484250740535988266> - {customer['rp_name']}\n> <:4189search:1484250713315086479> - `{customer_id}`", inline=False)
    log_embed.add_field(name="__Gekündigte Versicherungen__", value="\n".join(f"> <:3518crossmark:1473009455473098894> {ins}" for ins in to_cancel), inline=False)
    log_embed.add_field(name="__Neuer Monatsbeitrag__", value=f"> `{new_total:,.2f} €`", inline=True)
    log_embed.add_field(name="<:7549member:1484250740535988266> Durchgeführt von", value=f"> {interaction.user.mention}\n> `{interaction.user.id}`", inline=True)
    log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await send_to_log_channel(interaction.guild, log_embed)

    gekuendigt_text = "\n".join(f"> <:3518crossmark:1473009455473098894> {ins}" for ins in to_cancel)
    success = discord.Embed(title="Versicherung(en) gekündigt!", description="> Die Kündigung wurde erfolgreich durchgeführt.", color=COLOR_SUCCESS, timestamp=get_now())
    success.add_field(name="__Versicherungsnehmer__", value=f"> <:7549member:1484250740535988266> - {customer['rp_name']}\n> <:4189search:1484250713315086479> - `{customer_id}`", inline=False)
    success.add_field(name="__Gekündigte Versicherungen__", value=gekuendigt_text, inline=False)
    success.add_field(name="", value="━━━━━━━━━━━━━━━━━━━━━━━━", inline=False)
    success.add_field(name="__Neuer Monatsbeitrag__", value=f"> <:912926arrow:1484250786639646760> **`{new_total:,.2f} €`**", inline=False)
    success.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.edit_original_response(embed=success, view=None)

# ═══════════════════════════════════════════════════════
#   RECHNUNGS-ÜBERSICHT — NUR LEITUNGSEBENE
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="rechnungen-uebersicht", description="Zeigt alle Rechnungen eines Kunden oder alle offenen Rechnungen")
@app_commands.describe(
    customer_id="Versicherungsnehmer-ID (leer lassen für alle offenen Rechnungen)",
    nur_offen="Nur offene Rechnungen anzeigen (Standard: True)"
)
async def rechnungen_uebersicht(interaction: discord.Interaction, customer_id: Optional[str] = None, nur_offen: bool = True):
    # GEÄNDERT: War is_mitarbeiter, jetzt is_leitungsebene
    if not is_leitungsebene(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur die Leitungsebene kann Rechnungsübersichten einsehen.", "Leitungsebene"),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        if customer_id and customer_id not in data['customers']:
            await interaction.followup.send(
                embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."),
                ephemeral=True
            )
            return

        gefiltert = {}
        for inv_id, inv in data['invoices'].items():
            if customer_id and inv['customer_id'] != customer_id:
                continue
            if nur_offen and inv.get('paid', False):
                continue
            gefiltert[inv_id] = inv

        if not gefiltert:
            msg = "Keine offenen Rechnungen" if nur_offen else "Keine Rechnungen"
            msg += f" für `{customer_id}`." if customer_id else " vorhanden."
            await interaction.followup.send(
                embed=discord.Embed(title="Keine Rechnungen", description=f"> {msg}", color=COLOR_INFO, timestamp=get_now()),
                ephemeral=True
            )
            return

        sorted_invoices = sorted(gefiltert.items(), key=lambda x: x[1].get('created_at', ''), reverse=True)

        embed = discord.Embed(
            title="Rechnungsübersicht",
            description=(
                (f"> <:7549member:1484250740535988266> Kunde: **{data['customers'][customer_id]['rp_name']}** (`{customer_id}`)\n" if customer_id else "") +
                f"> <:6224mail:1484250731098673243> Gefunden: **`{len(sorted_invoices)}`** Rechnung(en)\n" +
                f"> <:7842privacy:1484250743991959552> Filter: `{'Nur offen' if nur_offen else 'Alle'}`"
            ),
            color=COLOR_PRIMARY,
            timestamp=get_now()
        )
        embed.add_field(name="", value="━━━━━━━━━━━━━━━━━━━━━━━━", inline=False)

        total_offen = 0.0
        for inv_id, inv in sorted_invoices[:15]:
            cust = data['customers'].get(inv['customer_id'], {})
            due  = make_aware(datetime.fromisoformat(inv['due_date']))
            now  = get_now()

            if inv.get('paid'):
                status_text = "<:3518checkmark:1484250693421367389> Bezahlt"
            elif due < now:
                tage = (now - due).days
                status_text = f"<:3518crossmark:1473009455473098894> **{tage} Tag(e) überfällig!**"
                total_offen += inv['betrag']
            else:
                status_text = "<:3684sync:1473009462628323523> Ausstehend"
                total_offen += inv['betrag']

            mahnstufe = inv.get('reminder_count', 0)
            mahnung_text = (f"\n> <:7842privacy:1484250743991959552> Mahnstufe: `{mahnstufe}`" if mahnstufe > 0 else "")

            embed.add_field(
                name=f"__`{inv_id}`__",
                value=(
                    f"> <:7549member:1484250740535988266> - {cust.get('rp_name', '—')}\n"
                    f"> <:8312card:1473009505041256501> - `{inv['betrag']:,.2f} €`\n"
                    f"> <:4189search:1484250713315086479> - Fällig: `{due.strftime('%d.%m.%Y')}` \n"
                    f"> <:912926arrow:1484250786639646760> {status_text}"
                    f"{mahnung_text}"
                ),
                inline=True
            )

        embed.add_field(name="", value="━━━━━━━━━━━━━━━━━━━━━━━━", inline=False)
        embed.add_field(name="__Offene Summe__", value=f"> <:912926arrow:1484250786639646760> **`{total_offen:,.2f} €`**", inline=False)

        footer_text = f"Zeige 15 von {len(sorted_invoices)} Rechnungen • Copyright © InsuranceGuard v3" if len(sorted_invoices) > 15 else "Copyright © InsuranceGuard v3"
        embed.set_footer(text=footer_text, icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Fehler Rechnungsübersicht: {e}", exc_info=True)
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

# ═══════════════════════════════════════════════════════
#   ADMIN COMMANDS – Einstellungen
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="einstellung-steuer", description="[ADMIN] Setzt den globalen Steuersatz für Rechnungen")
@app_commands.describe(prozent="Steuersatz in Prozent (z.B. 5.0)")
async def set_steuer(interaction: discord.Interaction, prozent: float):
    if not is_admin(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Admins können den Steuersatz ändern.", "Admin"),
            ephemeral=True
        )
        return
    if not (0 <= prozent <= 100):
        await interaction.response.send_message(
            embed=build_error_embed("Ungültiger Wert!", "Der Steuersatz muss zwischen 0 und 100 liegen."),
            ephemeral=True
        )
        return
    config["steuer_prozent"] = prozent
    save_config(config)
    e = discord.Embed(title="Steuersatz aktualisiert!", description=f"> Neuer Steuersatz: **`{prozent}%`**", color=COLOR_SUCCESS)
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=e, ephemeral=True)
    add_log_entry("STEUERSATZ_GEAENDERT", interaction.user.id, {"neuer_steuersatz": prozent})

@bot.tree.command(name="einstellung-versicherung-neu", description="[ADMIN] Fügt eine neue Versicherungsart hinzu")
@app_commands.describe(
    name="Name der Versicherung",
    preis="Monatlicher Beitrag in €",
    auszahlungslimit="Maximaler Auszahlungsbetrag in €",
    rollenname="Name der Discord-Rolle (Standard: gleich wie Name)"
)
async def add_versicherung(interaction: discord.Interaction, name: str, preis: float, auszahlungslimit: float, rollenname: str = ""):
    if not is_admin(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Admins können Versicherungen hinzufügen.", "Admin"),
            ephemeral=True
        )
        return
    if name in config["insurance_types"]:
        await interaction.response.send_message(
            embed=build_error_embed("Bereits vorhanden!", f"Eine Versicherung `{name}` existiert bereits."),
            ephemeral=True
        )
        return
    role = rollenname if rollenname else name
    config["insurance_types"][name] = {"price": preis, "role": role, "auszahlung_limit": auszahlungslimit, "enabled": True}
    save_config(config)
    e = discord.Embed(title="Versicherung hinzugefügt!", color=COLOR_SUCCESS)
    e.add_field(name="Name", value=f"> `{name}`", inline=False)
    e.add_field(name="Preis", value=f"> `{preis:,.2f} €/Monat`", inline=True)
    e.add_field(name="Auszahlungslimit", value=f"> `{auszahlungslimit:,.2f} €`", inline=True)
    e.add_field(name="Rolle", value=f"> `{role}`", inline=True)
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=e, ephemeral=True)
    add_log_entry("VERSICHERUNG_HINZUGEFUEGT", interaction.user.id, {"name": name, "preis": preis, "limit": auszahlungslimit})

@bot.tree.command(name="einstellung-versicherung-edit", description="[ADMIN] Bearbeitet eine bestehende Versicherungsart")
@app_commands.describe(
    name="Name der Versicherung",
    neuer_preis="Neuer monatlicher Beitrag (optional, -1 = keine Änderung)",
    neues_limit="Neues Auszahlungslimit (optional, -1 = keine Änderung)",
    aktiviert="Versicherung aktivieren/deaktivieren (True/False, optional)"
)
async def edit_versicherung(interaction: discord.Interaction, name: str, neuer_preis: float = -1.0, neues_limit: float = -1.0, aktiviert: Optional[bool] = None):
    if not is_admin(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Admins können Versicherungen bearbeiten.", "Admin"),
            ephemeral=True
        )
        return
    if name not in config["insurance_types"]:
        await interaction.response.send_message(
            embed=build_error_embed("Nicht gefunden!", f"Keine Versicherung `{name}` vorhanden."),
            ephemeral=True
        )
        return
    changes = []
    if neuer_preis >= 0.0:
        config["insurance_types"][name]["price"] = neuer_preis
        changes.append(f"Preis: `{neuer_preis:,.2f} €`")
    if neues_limit >= 0.0:
        config["insurance_types"][name]["auszahlung_limit"] = neues_limit
        changes.append(f"Limit: `{neues_limit:,.2f} €`")
    if aktiviert is not None:
        config["insurance_types"][name]["enabled"] = aktiviert
        changes.append(f"Status: `{'Aktiv' if aktiviert else 'Deaktiviert'}`")
    if not changes:
        await interaction.response.send_message("Keine Änderungen angegeben.", ephemeral=True)
        return
    save_config(config)
    e = discord.Embed(title=f"Versicherung `{name}` aktualisiert!", description="\n".join(f"> {c}" for c in changes), color=COLOR_SUCCESS)
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=e, ephemeral=True)
    add_log_entry("VERSICHERUNG_BEARBEITET", interaction.user.id, {"name": name, "changes": changes})

@bot.tree.command(name="einstellung-versicherungen-liste", description="[ADMIN] Zeigt alle konfigurierten Versicherungen")
async def list_versicherungen(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Admins können Versicherungen einsehen.", "Admin"),
            ephemeral=True
        )
        return
    e = discord.Embed(title="Versicherungskonfiguration", color=COLOR_INFO, timestamp=get_now())
    steuer = config.get("steuer_prozent", 5.0)
    e.add_field(name="__Aktueller Steuersatz__", value=f"> `{steuer}%`", inline=False)
    for name, v in config.get("insurance_types", {}).items():
        status = "✅" if v.get("enabled", True) else "🚫"
        e.add_field(
            name=f"{status} {name}",
            value=f"> Preis: `{v['price']:,.2f} €/Mo`\n> Limit: `{v['auszahlung_limit']:,.2f} €`\n> Rolle: `{v['role']}`",
            inline=True
        )
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=e, ephemeral=True)

# ═══════════════════════════════════════════════════════
#   EINSTELLUNG KANÄLE — INKL. PANEL-SETUP
#   GEÄNDERT: kundenkontakt-setup & schadensmeldung-setup
#   wurden hier integriert und als eigenständige Commands entfernt
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="einstellung-kanaele", description="[ADMIN] Setzt alle Bot-Kanäle & Kategorien und richtet Panels ein")
@app_commands.describe(
    log_channel="Log-Kanal",
    kundenkontakt_panel="Kanal für Kundenkontakt-Panel (sendet Panel automatisch)",
    schadensmeldung_panel="Kanal für Schadensmeldungs-Panel (sendet Panel automatisch)",
    auszahlung_kanal="Kanal für Auszahlungsanträge",
    kundenkontakt_kategorie="Kategorie für Kundenkontakt-Tickets",
    schadensmeldung_kategorie="Kategorie für Schadensmeldungs-Tickets"
)
async def set_channels(
    interaction: discord.Interaction,
    log_channel: Optional[discord.TextChannel] = None,
    kundenkontakt_panel: Optional[discord.TextChannel] = None,
    schadensmeldung_panel: Optional[discord.TextChannel] = None,
    auszahlung_kanal: Optional[discord.TextChannel] = None,
    kundenkontakt_kategorie: Optional[discord.CategoryChannel] = None,
    schadensmeldung_kategorie: Optional[discord.CategoryChannel] = None
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Admins können Kanäle konfigurieren.", "Admin"),
            ephemeral=True
        )
        return

    # Keine Argumente → aktuelle Konfiguration anzeigen
    if not any([log_channel, kundenkontakt_panel, schadensmeldung_panel,
                auszahlung_kanal, kundenkontakt_kategorie, schadensmeldung_kategorie]):
        e = discord.Embed(title="Aktuelle Kanal-Konfiguration", color=COLOR_INFO, timestamp=get_now())
        def ch(cid): return f"<#{cid}>" if cid else "`Nicht gesetzt`"
        def cat(cid):
            c = interaction.guild.get_channel(cid) if cid else None
            return f"`{c.name}`" if c else "`Nicht gesetzt`"
        e.add_field(name="Log-Kanal", value=ch(config.get("log_channel_id")), inline=True)
        e.add_field(name="Auszahlungs-Kanal", value=ch(config.get("auszahlung_channel_id")), inline=True)
        e.add_field(name="KK-Panel", value=ch(config.get("kundenkontakt_channel_id")), inline=True)
        e.add_field(name="SM-Panel", value=ch(config.get("schadensmeldung_channel_id")), inline=True)
        e.add_field(name="KK-Kategorie", value=cat(config.get("kundenkontakt_category_id")), inline=True)
        e.add_field(name="SM-Kategorie", value=cat(config.get("schadensmeldung_category_id")), inline=True)
        e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    changes = []
    panel_results = []

    # Einfache Kanal-Zuweisungen
    if log_channel:
        config["log_channel_id"] = log_channel.id
        changes.append(f"Log-Kanal: {log_channel.mention}")
    if auszahlung_kanal:
        config["auszahlung_channel_id"] = auszahlung_kanal.id
        changes.append(f"Auszahlungs-Kanal: {auszahlung_kanal.mention}")
    if kundenkontakt_kategorie:
        config["kundenkontakt_category_id"] = kundenkontakt_kategorie.id
        changes.append(f"KK-Kategorie: `{kundenkontakt_kategorie.name}`")
    if schadensmeldung_kategorie:
        config["schadensmeldung_category_id"] = schadensmeldung_kategorie.id
        changes.append(f"SM-Kategorie: `{schadensmeldung_kategorie.name}`")

    # Kundenkontakt-Panel senden
    if kundenkontakt_panel:
        try:
            config["kundenkontakt_channel_id"] = kundenkontakt_panel.id
            embed_kk = discord.Embed(
                title="Kundenkontakt",
                description="Liebe Mitarbeiter:innen,\n> hier können sie mit unseren Kunden Kontakt aufnehmen.",
                color=COLOR_PRIMARY,
                timestamp=get_now()
            )
            embed_kk.add_field(
                name="__Wie funktioniert das System?__",
                value="> 1. Klicken Sie unten auf den Button!\n> 2. Geben Sie die Kunden-ID ein!\n> 3. Beschreiben Sie einen detaillierten Kontaktgrund!\n> 4. Ein privater Ticket-Channel wird erstellt!",
                inline=False
            )
            embed_kk.add_field(
                name="__Was muss ich beachten?__",
                value="> ▸ Gültige **Kunden-ID** erforderlich!\n> ▸ Kontaktgrund **detailliert** beschreiben!\n> ▸ Nur für **Mitarbeiter** und **Leitungsebene**!",
                inline=False
            )
            embed_kk.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await kundenkontakt_panel.send(embed=embed_kk, view=KundenkontaktView())
            changes.append(f"KK-Panel: {kundenkontakt_panel.mention}")
            panel_results.append(f"<:3518checkmark:1484250693421367389> Kundenkontakt-Panel in {kundenkontakt_panel.mention} gesendet")
            add_log_entry("KUNDENKONTAKT_SYSTEM_SETUP", interaction.user.id, {"channel_id": kundenkontakt_panel.id})
        except Exception as e:
            panel_results.append(f"<:3518crossmark:1473009455473098894> Fehler beim KK-Panel: {e}")

    # Schadensmeldungs-Panel senden
    if schadensmeldung_panel:
        try:
            config["schadensmeldung_channel_id"] = schadensmeldung_panel.id
            embed_sm = discord.Embed(
                title="Schadensmeldung",
                description="Liebe Versicherungsnehmer:innen,\n> hier können sie Schadensmeldungen einreichen.",
                color=COLOR_PRIMARY,
                timestamp=get_now()
            )
            embed_sm.add_field(
                name="__Wie funktioniert das System?__",
                value="> 1. Klicken Sie auf den Button unten!\n> 2. Geben Sie Ihre Kunden-ID ein!\n> 3. Füllen Sie das Formular aus!\n> 4. Ein Schadensfall-Ticket wird erstellt!",
                inline=False
            )
            embed_sm.add_field(
                name="__Welche Angaben sind erforderlich?__",
                value="> ▸ **Kunden-ID**\n> ▸ **Geschädigter** (RP-Name)\n> ▸ **Täter** (RP-Name)\n> ▸ **Vorfallbeschreibung**\n> ▸ **Rechnung/Nachweis**",
                inline=False
            )
            embed_sm.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await schadensmeldung_panel.send(embed=embed_sm, view=SchadensmeldungView())
            changes.append(f"SM-Panel: {schadensmeldung_panel.mention}")
            panel_results.append(f"<:3518checkmark:1484250693421367389> Schadensmeldungs-Panel in {schadensmeldung_panel.mention} gesendet")
            add_log_entry("SCHADENSMELDUNG_SYSTEM_SETUP", interaction.user.id, {"channel_id": schadensmeldung_panel.id})
        except Exception as e:
            panel_results.append(f"<:3518crossmark:1473009455473098894> Fehler beim SM-Panel: {e}")

    save_config(config)
    add_log_entry("KANAELE_KONFIGURIERT", interaction.user.id, {"changes": changes})

    e = discord.Embed(title="Konfiguration aktualisiert!", color=COLOR_SUCCESS, timestamp=get_now())
    if changes:
        e.add_field(name="__Gespeicherte Einstellungen__", value="\n".join(f"> {c}" for c in changes), inline=False)
    if panel_results:
        e.add_field(name="__Panel-Status__", value="\n".join(f"> {r}" for r in panel_results), inline=False)
    e.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.followup.send(embed=e, ephemeral=True)

# ═══════════════════════════════════════════════════════
#   BACKUP & RELOAD
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="backup", description="Erstellt ein Backup beider Datenbanken und sendet sie als ZIP")
async def backup_download(interaction: discord.Interaction):
    if not is_leitungsebene(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur die Leitungsebene kann Backups herunterladen.", "Leitungsebene"),
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        create_backup()
        buf = create_zip_buffer()
        file = discord.File(buf, filename=f"insurance_full_backup_{get_now().strftime('%Y%m%d_%H%M%S')}.zip")
        await interaction.followup.send("<:2141file:1473009449412071484> Vollständiger Datenbank-Export", file=file, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"<:3518crossmark:1473009455473098894> Fehler: {e}", ephemeral=True)

@bot.tree.command(name="reload", description="Stellt eine Datenbank-Datei (JSON) wieder her")
@app_commands.describe(datei="Die hochzuladende Datei (insurance_data.json oder bot_config.json)")
async def reload_backup(interaction: discord.Interaction, datei: discord.Attachment):
    if not is_leitungsebene(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur die Leitungsebene kann Backups wiederherstellen.", "Leitungsebene"),
            ephemeral=True
        )
        return
    if not datei.filename.endswith('.json'):
        await interaction.response.send_message(
            embed=build_error_embed("Falscher Dateityp!", "Nur `.json` Dateien sind erlaubt."),
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        create_backup()
        content = await datei.read()
        json_data = json.loads(content.decode('utf-8'))

        if "customers" in json_data and "logs" in json_data:
            global data
            data = json_data
            for key in ("schadensmeldungen", "pending_auszahlungen"):
                if key not in data:
                    data[key] = {}
            save_data(data)
            if not check_invoices.is_running():
                check_invoices.start()
            await interaction.followup.send(
                "<:3518checkmark:1484250693421367389> `insurance_data.json` erfolgreich wiederhergestellt.",
                ephemeral=True
            )
        elif "log_channel_id" in json_data or "kundenkontakt_category_id" in json_data:
            global config
            config = json_data
            save_config(config)
            await interaction.followup.send(
                "<:3518checkmark:1484250693421367389> `bot_config.json` erfolgreich wiederhergestellt.",
                ephemeral=True
            )
        else:
            await interaction.followup.send("<:3518crossmark:1473009455473098894> Unbekanntes Dateiformat.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"<:3518crossmark:1473009455473098894> Fehler: {e}", ephemeral=True)

# ═══════════════════════════════════════════════════════
#   KUNDENAKTE ERSTELLEN
# ═══════════════════════════════════════════════════════
class InsuranceSelect(discord.ui.Select):
    def __init__(self):
        insurance_types = get_insurance_types()
        options = [
            discord.SelectOption(label=insurance, description=f"Monatsbeitrag: {info['price']:,.2f} €", value=insurance)
            for insurance, info in insurance_types.items()
        ]
        super().__init__(
            placeholder="Gewünschte Versicherungen auswählen...",
            min_values=1,
            max_values=len(options),
            options=options,
            custom_id="insurance_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        for item in view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = False
        insurance_types = get_insurance_types()
        total = sum(insurance_types[ins]["price"] for ins in self.values)
        preview_text = "\n".join(f"▸ {ins} — {insurance_types[ins]['price']:,.2f} €" for ins in self.values)
        preview_embed = discord.Embed(
            title="Versicherungen ausgewählt!",
            description=f"**Ausgewählte Versicherungen:**\n{preview_text}\n\n**Gesamtbeitrag (monatlich):** `{total:,.2f} €`",
            color=COLOR_INFO
        )
        preview_embed.set_footer(text="Klicken Sie auf 'Kundenakte erstellen', um fortzufahren.")
        await interaction.response.edit_message(embed=preview_embed, view=view)

class InsuranceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.confirmed = False
        self.add_item(InsuranceSelect())
        confirm_button = discord.ui.Button(label="Kundenakte erstellen", style=discord.ButtonStyle.green, custom_id="confirm_insurance", disabled=True)
        confirm_button.callback = self.confirm_callback
        self.add_item(confirm_button)

    async def confirm_callback(self, interaction: discord.Interaction):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class AddInsuranceSelect(discord.ui.Select):
    def __init__(self, existing_insurances: list):
        insurance_types = get_insurance_types()
        options = [
            discord.SelectOption(label=ins, description=f"Monatsbeitrag: {info['price']:,.2f} €", value=ins)
            for ins, info in insurance_types.items()
            if ins not in existing_insurances
        ]
        super().__init__(
            placeholder="Neue Versicherung(en) wählen...",
            min_values=1,
            max_values=len(options),
            options=options,
            custom_id="add_insurance_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        for item in view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = False
        insurance_types = get_insurance_types()
        total = sum(insurance_types[ins]["price"] for ins in self.values)
        preview_text = "\n".join(f"▸ {ins} — {insurance_types[ins]['price']:,.2f} €" for ins in self.values)
        e = discord.Embed(
            title="Neue Versicherungen ausgewählt!",
            description=f"{preview_text}\n\n**Zusätzlicher Monatsbeitrag:** `{total:,.2f} €`",
            color=COLOR_INFO
        )
        await interaction.response.edit_message(embed=e, view=view)

class AddInsuranceView(discord.ui.View):
    def __init__(self, existing_insurances: list):
        super().__init__(timeout=180)
        self.confirmed = False
        self.add_item(AddInsuranceSelect(existing_insurances))
        btn = discord.ui.Button(label="Versicherungen hinzubuchen", style=discord.ButtonStyle.green, disabled=True)
        btn.callback = self._confirm
        self.add_item(btn)

    async def _confirm(self, interaction: discord.Interaction):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

@bot.tree.command(name="kundenakte-erstellen", description="Erstellt eine neue Kundenakte im Archiv")
@app_commands.describe(
    forum_channel="Forum-Channel für Kundenakten",
    user="Discord-User des Versicherungsnehmers",
    rp_name="RP-Name des Versicherungsnehmers",
    hbpay_nummer="HBpay Kontonummer",
    economy_id="Economy-ID des Versicherungsnehmers"
)
async def create_customer(
    interaction: discord.Interaction,
    forum_channel: discord.ForumChannel,
    user: discord.Member,
    rp_name: str,
    hbpay_nummer: str,
    economy_id: str
):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Kundenakten erstellen.", "Mitarbeiter / Leitungsebene"),
            ephemeral=True
        )
        return

    view = InsuranceView()
    select_embed = discord.Embed(
        title="Versicherungen auswählen!",
        description="> Bitte wählen Sie die gewünschten Versicherungen für den Versicherungsnehmer aus dem Dropdown-Menü aus. Nach der Auswahl klicken Sie auf den Button 'Kundenakte erstellen', um fortzufahren.",
        color=COLOR_PRIMARY
    )
    await interaction.response.send_message(embed=select_embed, view=view, ephemeral=True)
    await view.wait()

    if not view.confirmed:
        await interaction.edit_original_response(embed=discord.Embed(title="Zeitüberschreitung!", color=COLOR_WARNING), view=None)
        return

    insurance_select = view.children[0]
    if not insurance_select.values:
        await interaction.edit_original_response(embed=discord.Embed(title="Keine Auswahl!", color=COLOR_ERROR), view=None)
        return

    insurance_list = insurance_select.values
    insurance_types = get_insurance_types()
    logger.info(f"Kundenakte wird erstellt von {interaction.user.id} für {rp_name}")

    try:
        customer_id = generate_customer_id()
        total_price = sum(insurance_types[ins]["price"] for ins in insurance_list)

        customer_data = {
            "rp_name": rp_name,
            "hbpay_nummer": hbpay_nummer,
            "economy_id": economy_id,
            "versicherungen": insurance_list,
            "total_monthly_price": total_price,
            "thread_id": None,
            "discord_user_id": user.id,
            "created_at": get_now().isoformat(),
            "created_by": interaction.user.id,
            "status": "aktiv",
            "auszahlungen": {}
        }

        embed = build_kundenakte_embed(customer_id, customer_data)
        thread = await forum_channel.create_thread(
            name=f"📁 {customer_id} | {rp_name}",
            content="",
            embed=embed
        )
        customer_data["thread_id"] = thread.thread.id
        data['customers'][customer_id] = customer_data
        save_data(data)

        for insurance in insurance_list:
            role_name = insurance_types[insurance]["role"]
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                role = await interaction.guild.create_role(name=role_name, color=discord.Color.from_rgb(44, 62, 80))
            await user.add_roles(role)

        kunden_role = discord.utils.get(interaction.guild.roles, name=KUNDEN_ROLE_NAME)
        if not kunden_role:
            kunden_role = await interaction.guild.create_role(name=KUNDEN_ROLE_NAME, color=discord.Color.from_rgb(52, 152, 219))
        await user.add_roles(kunden_role)

        dm_embed = build_kundenakte_embed(customer_id, customer_data)
        dm_embed.title = "Willkommen bei InsuranceGuard!"
        dm_embed.description = "Ihre Versicherungsakte wurde erfolgreich angelegt."
        try:
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            logger.warning(f"DM an {user.id} nicht möglich")

        add_log_entry("KUNDENAKTE_ERSTELLT", interaction.user.id, {
            "customer_id": customer_id, "rp_name": rp_name,
            "versicherungen": insurance_list, "total_price": total_price,
            "thread_id": thread.thread.id, "discord_user_id": user.id
        })

        log_embed = discord.Embed(title="Neue Kundenakte erstellt!", color=COLOR_SUCCESS, timestamp=get_now())
        log_embed.add_field(name="__Versicherungsnehmer__", value=f"> {rp_name}\n> `{customer_id}`", inline=False)
        log_embed.add_field(name="__Discord__", value=f"> {user.mention}", inline=True)
        log_embed.add_field(name="__Monatsbeitrag__", value=f"> `{total_price:,.2f} €`", inline=True)
        log_embed.add_field(name="<:7549member:1484250740535988266> Aussteller", value=f"> {interaction.user.mention}\n> `{interaction.user.id}`", inline=False)
        log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await send_to_log_channel(interaction.guild, log_embed)

        success_embed = discord.Embed(title="Kundenakte erfolgreich angelegt!", color=COLOR_SUCCESS)
        success_embed.add_field(name="__Informationen__", value=f"> <:4189search:1484250713315086479> `{customer_id}`\n> <:1041searchthreads:1484250678573400155> {thread.thread.mention}\n> <:9654dollar:1484250776049291324> - `{total_price:,.2f} €`\n> <:6224mail:1484250731098673243> - <:3518checkmark:1484250693421367389> DM wurde gesendet!", inline=False)
        success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.edit_original_response(embed=success_embed, view=None)

    except Exception as e:
        logger.error(f"Fehler beim Erstellen der Kundenakte: {e}", exc_info=True)
        await interaction.edit_original_response(embed=discord.Embed(title="Fehler!", description=str(e), color=COLOR_ERROR), view=None)

# ═══════════════════════════════════════════════════════
#   VERSICHERUNG NACHBUCHEN
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="versicherung-hinzubuchen", description="Fügt einem bestehenden Kunden eine neue Versicherung hinzu")
@app_commands.describe(customer_id="Versicherungsnehmer-ID")
async def add_insurance_to_customer(interaction: discord.Interaction, customer_id: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Versicherungen nachbuchen.", "Mitarbeiter"),
            ephemeral=True
        )
        return

    if customer_id not in data['customers']:
        await interaction.response.send_message(
            embed=build_error_embed("Nicht gefunden!", f"Keine Kundenakte mit ID `{customer_id}`."),
            ephemeral=True
        )
        return

    customer = data['customers'][customer_id]
    existing = customer.get("versicherungen", [])
    insurance_types = get_insurance_types()
    available = [ins for ins in insurance_types if ins not in existing]

    if not available:
        await interaction.response.send_message(
            embed=discord.Embed(title="Alle Versicherungen abgeschlossen!", color=COLOR_INFO),
            ephemeral=True
        )
        return

    view = AddInsuranceView(existing)
    e = discord.Embed(
        title="Versicherung nachbuchen",
        description=f"Kunde: **{customer['rp_name']}** (`{customer_id}`)\n\nBitte wähle die hinzuzufügenden Versicherungen.",
        color=COLOR_INFO
    )
    await interaction.response.send_message(embed=e, view=view, ephemeral=True)
    await view.wait()

    if not view.confirmed:
        await interaction.edit_original_response(embed=discord.Embed(title="Abgebrochen.", color=COLOR_WARNING), view=None)
        return

    sel = view.children[0]
    new_insurances = sel.values
    if not new_insurances:
        return

    for ins in new_insurances:
        if ins not in data['customers'][customer_id]['versicherungen']:
            data['customers'][customer_id]['versicherungen'].append(ins)

    new_total = sum(insurance_types[ins]["price"] for ins in data['customers'][customer_id]['versicherungen'])
    data['customers'][customer_id]['total_monthly_price'] = new_total
    save_data(data)

    await update_forum_thread_embed(interaction.guild, customer_id, data['customers'][customer_id])

    member = interaction.guild.get_member(customer['discord_user_id'])
    if member:
        for ins in new_insurances:
            role_name = insurance_types[ins]["role"]
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                role = await interaction.guild.create_role(name=role_name, color=discord.Color.from_rgb(44, 62, 80))
            await member.add_roles(role)

    if member:
        dm_embed = discord.Embed(title="Versicherungsänderung", description="Neue Versicherungen wurden hinzugefügt.", color=COLOR_INFO)
        dm_embed.add_field(name="__Neue Versicherungen__", value="\n".join(f"> ▸ {ins}" for ins in new_insurances), inline=False)
        dm_embed.add_field(name="__Neuer Monatsbeitrag__", value=f"> <:9654dollar:1484250776049291324> - `{new_total:,.2f} €`", inline=False)
        dm_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        try:
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    add_log_entry("VERSICHERUNG_NACHGEBUCHT", interaction.user.id, {"customer_id": customer_id, "neue_versicherungen": new_insurances})

    success = discord.Embed(title="Versicherungen nachgebucht!", color=COLOR_SUCCESS)
    success.add_field(name="__Neue Versicherungen__", value="\n".join(f"> ▸ {ins}" for ins in new_insurances), inline=False)
    success.add_field(name="__Neuer Monatsbeitrag__", value=f"> <:9654dollar:1484250776049291324> - `{new_total:,.2f} €`", inline=False)
    success.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.edit_original_response(embed=success, view=None)

# ═══════════════════════════════════════════════════════
#   RECHNUNG AUSSTELLEN & ARCHIVIEREN
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="rechnung-ausstellen", description="Erstellt eine Versicherungsrechnung")
@app_commands.describe(customer_id="Versicherungsnehmer-ID", channel="Channel für die Rechnungsstellung")
async def create_invoice(interaction: discord.Interaction, customer_id: str, channel: discord.TextChannel):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Rechnungen ausstellen.", "Mitarbeiter"),
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)

    try:
        if customer_id not in data['customers']:
            await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."), ephemeral=True)
            return

        customer = data['customers'][customer_id]
        invoice_id = generate_invoice_id()
        betrag_netto = customer['total_monthly_price']
        steuer_prozent = config.get("steuer_prozent", 5.0)
        steuer = betrag_netto * (steuer_prozent / 100)
        betrag_brutto = betrag_netto + steuer
        due_date = get_now() + timedelta(days=3)
        insurance_types = get_insurance_types()

        embed = discord.Embed(
            title=f"Versicherungsrechnung - {get_now().strftime('%d.%m.%Y')}",
            description="Dies ist eine Zahlungsaufforderung für Ihre Versicherungsbeiträge!",
            color=COLOR_PRIMARY,
            timestamp=get_now()
        )
        embed.add_field(name="__Rechnungsinformationen__", value=f"> <:6224mail:1484250731098673243> - `{invoice_id}`")
        embed.add_field(name="__Versicherungsnehmer__", value=f"> <:7549member:1484250740535988266> - {customer['rp_name']}\n> <:4189search:1484250713315086479> - `{customer_id}`", inline=False)
        embed.add_field(name="__Zahlungsmethoden__", value=f"> <:8312card:1484250748467281951> - `{customer['hbpay_nummer']}`\n> <:9847public:1484250777307447417> - `{customer['economy_id']}`", inline=False)
        insurance_details = "\n".join(
            f"> {ins}\n> ▸ `{insurance_types.get(ins, {}).get('price', 0.0):,.2f} €`"
            for ins in customer['versicherungen']
        )
        embed.add_field(name="__Abgeschlossene Versicherungen__", value=insurance_details, inline=False)
        embed.add_field(name="__Abrechnung__", value="", inline=False)
        embed.add_field(name="Zwischensumme (Netto)", value=f"> `{betrag_netto:,.2f} €`", inline=False)
        embed.add_field(name=f"Steuer ({steuer_prozent}%)", value=f"> `+` `{steuer:,.2f} €`", inline=False)
        embed.add_field(name="Rechnungsbetrag (Brutto)", value=f"<:912926arrow:1484250786639646760> **`{betrag_brutto:,.2f} €`**", inline=False)
        embed.add_field(name="__Status: Zahlung ausstehend!__", value=f"> Sie haben bis zum **{due_date.strftime('%d.%m.%Y')}** Zeit diese Rechnung zu begleichen.", inline=False)
        embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)

        message = await channel.send(embed=embed)
        data['invoices'][invoice_id] = {
            "customer_id": customer_id,
            "betrag": betrag_brutto,
            "betrag_netto": betrag_netto,
            "steuer": steuer,
            "steuer_prozent": steuer_prozent,
            "original_betrag": betrag_brutto,
            "paid": False,
            "message_id": message.id,
            "channel_id": channel.id,
            "due_date": due_date.isoformat(),
            "reminder_count": 0,
            "created_at": get_now().isoformat(),
            "created_by": interaction.user.id
        }
        save_data(data)

        add_log_entry("RECHNUNG_ERSTELLT", interaction.user.id, {
            "invoice_id": invoice_id, "customer_id": customer_id,
            "betrag_brutto": betrag_brutto, "due_date": due_date.strftime('%d.%m.%Y')
        })

        log_embed = discord.Embed(title="Neue Rechnung ausgestellt!", color=COLOR_INFO, timestamp=get_now())
        log_embed.add_field(name="Rechnungsnummer", value=f"> `{invoice_id}`", inline=False)
        log_embed.add_field(name="Versicherungsnehmer", value=f"> {customer['rp_name']}\n> `{customer_id}`", inline=False)
        log_embed.add_field(name="Betrag (Brutto)", value=f"> **`{betrag_brutto:,.2f} €`**", inline=True)
        log_embed.add_field(name="Fällig am", value=f"> {due_date.strftime('%d.%m.%Y')}", inline=True)
        log_embed.add_field(name="Aussteller", value=f"> {interaction.user.mention}", inline=False)
        log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await send_to_log_channel(interaction.guild, log_embed)

        success_embed = discord.Embed(title="Rechnung erfolgreich ausgestellt!", color=COLOR_SUCCESS)
        success_embed.add_field(name="Rechnungsnummer", value=f"> `{invoice_id}`", inline=True)
        success_embed.add_field(name="Betrag", value=f"> `{betrag_brutto:,.2f} €`", inline=True)
        success_embed.add_field(name="Fällig", value=f"> {due_date.strftime('%d.%m.%Y')}", inline=True)
        success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=success_embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Fehler Rechnung: {e}", exc_info=True)
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

@bot.tree.command(name="rechnung-archivieren", description="Markiert eine Rechnung als bezahlt – setzt das Auszahlungslimit zurück")
@app_commands.describe(invoice_id="Rechnungsnummer (z.B. RE-2412-A3F9)")
async def archive_invoice(interaction: discord.Interaction, invoice_id: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Rechnungen archivieren.", "Mitarbeiter"),
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)

    try:
        if invoice_id not in data['invoices']:
            await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Keine Rechnung `{invoice_id}`."), ephemeral=True)
            return
        invoice = data['invoices'][invoice_id]
        if invoice.get('paid', False):
            await interaction.followup.send(embed=discord.Embed(title="Bereits archiviert!", color=COLOR_INFO), ephemeral=True)
            return

        customer_id = invoice['customer_id']
        customer = data['customers'].get(customer_id)
        if not customer:
            await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Kunde `{customer_id}` nicht gefunden."), ephemeral=True)
            return

        data['invoices'][invoice_id]['paid'] = True
        data['invoices'][invoice_id]['paid_by'] = interaction.user.id
        data['invoices'][invoice_id]['paid_at'] = get_now().isoformat()
        data['invoices'][invoice_id]['archived'] = True
        data['invoices'][invoice_id]['reminder_count'] = 0
        data['customers'][customer_id]['auszahlungen'] = {}
        save_data(data)

        try:
            ch = interaction.guild.get_channel(invoice['channel_id'])
            if ch:
                msg = await ch.fetch_message(invoice['message_id'])
                if msg.embeds:
                    upd = msg.embeds[0]
                    upd.color = COLOR_SUCCESS
                    for i, field in enumerate(upd.fields):
                        if "Status" in field.name:
                            upd.set_field_at(i, name="Status: Bezahlt!", value=f"> Bezahlt am **{get_now().strftime('%d.%m.%Y • %H:%M Uhr')}**\n> Archiviert von: {interaction.user.mention}", inline=False)
                            break
                    await msg.edit(embed=upd)
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren der Rechnung: {e}")

        thread_id = customer.get('thread_id')
        if thread_id:
            try:
                thread = interaction.guild.get_thread(thread_id) or await interaction.guild.fetch_channel(thread_id)
                if thread:
                    archive_embed = discord.Embed(title="Archivierte Rechnung", description="Bezahlt. Auszahlungslimit wurde zurückgesetzt.", color=COLOR_SUCCESS, timestamp=get_now())
                    archive_embed.add_field(name="__Rechnungsinformationen__", value=f"> Rechnungs-Nr.: `{invoice_id}`\n> Rechnungsdatum: {make_aware(datetime.fromisoformat(invoice['created_at'])).strftime('%d.%m.%Y')}\n> Zahlungsdatum: {get_now().strftime('%d.%m.%Y')}", inline=False)
                    archive_embed.add_field(name="__Abrechnung__", value=f"> Netto: `{invoice.get('betrag_netto', 0):,.2f} €`\n> Steuer: `+ {invoice.get('steuer', 0):,.2f} €`\n> **Brutto: `{invoice['betrag']:,.2f} €`**", inline=False)
                    archive_embed.add_field(name="Auszahlungslimit", value="> Vollständig zurückgesetzt!", inline=False)
                    archive_embed.add_field(name="Archiviert von", value=f"> {interaction.user.mention}", inline=False)
                    archive_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
                    await thread.send(embed=archive_embed)
            except Exception as e:
                logger.error(f"Fehler beim Posten in Kundenakte: {e}")

        add_log_entry("RECHNUNG_ARCHIVIERT", interaction.user.id, {
            "invoice_id": invoice_id, "customer_id": customer_id,
            "betrag": invoice['betrag'], "limit_reset": True
        })

        log_embed = discord.Embed(title="Rechnung archiviert!", color=COLOR_SUCCESS, timestamp=get_now())
        log_embed.add_field(name="Rechnungsnummer", value=f"> `{invoice_id}`", inline=False)
        log_embed.add_field(name="Versicherungsnehmer", value=f"> {customer['rp_name']}\n> `{customer_id}`", inline=False)
        log_embed.add_field(name="Betrag", value=f"> `{invoice['betrag']:,.2f} €`", inline=True)
        log_embed.add_field(name="Archiviert von", value=f"> {interaction.user.mention}", inline=False)
        log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await send_to_log_channel(interaction.guild, log_embed)

        success_embed = discord.Embed(title="Rechnung archiviert!", description=f"`{invoice_id}` als bezahlt markiert. Auszahlungslimit zurückgesetzt.", color=COLOR_SUCCESS)
        success_embed.add_field(name="Kunde", value=f"> {customer['rp_name']}", inline=True)
        success_embed.add_field(name="Betrag", value=f"> `{invoice['betrag']:,.2f} €`", inline=True)
        success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=success_embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Fehler beim Archivieren: {e}", exc_info=True)
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

# ═══════════════════════════════════════════════════════
#   MAHNUNG
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="mahnung-ausstellen", description="Stellt eine Mahnung für eine überfällige Rechnung aus")
@app_commands.describe(invoice_id="Rechnungsnummer")
async def issue_manual_reminder(interaction: discord.Interaction, invoice_id: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Mahnungen ausstellen.", "Mitarbeiter"),
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if invoice_id not in data['invoices']:
            await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Keine Rechnung `{invoice_id}`."), ephemeral=True)
            return
        invoice = data['invoices'][invoice_id]
        if invoice.get('paid', False):
            await interaction.followup.send(embed=discord.Embed(title="Bereits bezahlt!", color=COLOR_INFO), ephemeral=True)
            return

        customer = data['customers'].get(invoice['customer_id'])
        if not customer:
            await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", "Kunde nicht gefunden."), ephemeral=True)
            return

        reminder_count = invoice.get('reminder_count', 0) + 1
        surcharge_percent = 0
        if reminder_count == 2:
            surcharge_percent = 5
            data['invoices'][invoice_id]['betrag'] = invoice['original_betrag'] * 1.05
        elif reminder_count >= 3:
            surcharge_percent = 10
            data['invoices'][invoice_id]['betrag'] = invoice['original_betrag'] * 1.10

        data['invoices'][invoice_id]['reminder_count'] = reminder_count
        save_data(data)
        await send_reminder(invoice_id, data['invoices'][invoice_id], reminder_count, surcharge_percent)

        success_embed = discord.Embed(title=f"{reminder_count}. Mahnung ausgestellt!", description=f"Rechnung `{invoice_id}`", color=COLOR_SUCCESS)
        success_embed.add_field(name="Neuer Betrag", value=f"> `{data['invoices'][invoice_id]['betrag']:,.2f} €`", inline=True)
        if surcharge_percent > 0:
            success_embed.add_field(name="Mahngebühr", value=f"> +{surcharge_percent}%", inline=True)
        success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=success_embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

async def send_reminder(invoice_id: str, invoice_data: dict, reminder_number: int, surcharge_percent: int):
    try:
        for guild in bot.guilds:
            channel = guild.get_channel(invoice_data['channel_id'])
            if not channel:
                continue
            customer = data['customers'].get(invoice_data['customer_id'])
            if not customer:
                continue
            customer_user = guild.get_member(customer['discord_user_id'])
            surcharge_text = f" (+{surcharge_percent}% Mahngebühr)" if surcharge_percent > 0 else ""

            embed = discord.Embed(
                title=f"{reminder_number}. Mahnung",
                description=f"Die Rechnung `{invoice_id}` ist überfällig!",
                color=COLOR_WARNING if reminder_number < 3 else COLOR_ERROR,
                timestamp=get_now()
            )
            embed.add_field(name="__Rechnungsinformationen__", value=f"> Rechnungs-Nr.: `{invoice_id}`\n> Kunde: {customer['rp_name']}\n> Mahnstufe: {reminder_number}. Mahnung", inline=False)
            embed.add_field(name="__Zahlungsinformationen__", value=f"> Ursprünglicher Betrag: `{invoice_data['original_betrag']:,.2f} €`\n> **Aktueller Betrag: `{invoice_data['betrag']:,.2f} €`**{surcharge_text}", inline=False)
            embed.set_footer(text="Bitte begleichen Sie den Betrag umgehend • Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)

            mention = customer_user.mention if customer_user else ""
            await channel.send(mention, embed=embed)

            log_embed = discord.Embed(title=f"{reminder_number}. Mahnung versendet!", color=COLOR_WARNING if reminder_number < 3 else COLOR_ERROR, timestamp=get_now())
            log_embed.add_field(name="Rechnungsnummer", value=f"> `{invoice_id}`", inline=False)
            log_embed.add_field(name="Versicherungsnehmer", value=f"> {customer['rp_name']}\n> `{invoice_data['customer_id']}`", inline=False)
            log_embed.add_field(name="Mahnstufe", value=f"> {reminder_number}. Mahnung", inline=True)
            log_embed.add_field(name="Neuer Betrag", value=f"> `{invoice_data['betrag']:,.2f} €`", inline=True)
            log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await send_to_log_channel(guild, log_embed)
            add_log_entry(f"MAHNUNG_{reminder_number}", 0, {
                "invoice_id": invoice_id, "customer_id": invoice_data['customer_id'],
                "customer_name": customer['rp_name'], "surcharge": surcharge_percent,
                "neuer_betrag": invoice_data['betrag']
            })
            break
    except Exception as e:
        logger.error(f"Fehler beim Senden der Mahnung: {e}", exc_info=True)

# ═══════════════════════════════════════════════════════
#   AKTE ARCHIVIEREN
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="akte-archivieren", description="Archiviert eine Kundenakte")
@app_commands.describe(customer_id="Versicherungsnehmer-ID")
async def archive_customer(interaction: discord.Interaction, customer_id: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Akten archivieren.", "Mitarbeiter"),
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if customer_id not in data['customers']:
            await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."), ephemeral=True)
            return
        customer = data['customers'][customer_id]
        if customer.get('status') == 'archiviert':
            await interaction.followup.send(embed=discord.Embed(title="Bereits archiviert!", color=COLOR_INFO), ephemeral=True)
            return

        data['customers'][customer_id]['status'] = 'archiviert'
        data['customers'][customer_id]['archived_at'] = get_now().isoformat()
        data['customers'][customer_id]['archived_by'] = interaction.user.id
        save_data(data)

        thread_id = customer.get('thread_id')
        if thread_id:
            try:
                thread = interaction.guild.get_thread(thread_id) or await interaction.guild.fetch_channel(thread_id)
                if thread:
                    await thread.edit(name=f"🗄️ [ARCHIV] {customer_id} | {customer['rp_name']}")
                    archive_embed = discord.Embed(title="Akte archiviert!", description="Diese Kundenakte wurde archiviert.", color=COLOR_WARNING, timestamp=get_now())
                    archive_embed.add_field(name="Archiviert von", value=f"> {interaction.user.mention}", inline=True)
                    archive_embed.add_field(name="Datum", value=f"> {get_now().strftime('%d.%m.%Y • %H:%M Uhr')}", inline=True)
                    archive_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
                    await thread.send(embed=archive_embed)
            except Exception as e:
                logger.error(f"Fehler beim Aktualisieren des Threads: {e}")

        member = interaction.guild.get_member(customer['discord_user_id'])
        insurance_types = get_insurance_types()
        if member:
            for insurance in customer.get('versicherungen', []):
                role_name = insurance_types.get(insurance, {}).get("role", insurance)
                role = discord.utils.get(interaction.guild.roles, name=role_name)
                if role and role in member.roles:
                    await member.remove_roles(role)
            kunden_role = discord.utils.get(interaction.guild.roles, name=KUNDEN_ROLE_NAME)
            if kunden_role and kunden_role in member.roles:
                await member.remove_roles(kunden_role)

        add_log_entry("AKTE_ARCHIVIERT", interaction.user.id, {"customer_id": customer_id, "customer_name": customer['rp_name']})

        log_embed = discord.Embed(title="Kundenakte archiviert!", color=COLOR_WARNING, timestamp=get_now())
        log_embed.add_field(name="Kunden-ID", value=f"> `{customer_id}`", inline=True)
        log_embed.add_field(name="Kunde", value=f"> {customer['rp_name']}", inline=True)
        log_embed.add_field(name="Archiviert von", value=f"> {interaction.user.mention}", inline=False)
        log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await send_to_log_channel(interaction.guild, log_embed)

        success_embed = discord.Embed(title="Akte archiviert!", description=f"Kundenakte `{customer_id}` archiviert und alle Rollen entfernt.", color=COLOR_SUCCESS)
        success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=success_embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Fehler beim Archivieren: {e}", exc_info=True)
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

# ═══════════════════════════════════════════════════════
#   AUSZAHLUNG SYSTEM
# ═══════════════════════════════════════════════════════
class AuszahlungAntragsModal(discord.ui.Modal, title="Auszahlungsantrag"):
    betrag = discord.ui.TextInput(label="Auszahlungsbetrag (ohne €-Zeichen)", placeholder="z.B. 5000.00", required=True, max_length=20)
    beschreibung = discord.ui.TextInput(label="Beschreibung (optional)", style=discord.TextStyle.paragraph, placeholder="Kurze Beschreibung des Auszahlungsgrunds...", required=False, max_length=500)

    def __init__(self, customer_id: str, customer: dict, versicherung: str):
        super().__init__()
        self.customer_id = customer_id
        self.customer = customer
        self.versicherung = versicherung

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            try:
                betrag_float = float(self.betrag.value.replace(",", ".").replace("€", "").strip())
            except ValueError:
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Ungültiger Betrag.", ephemeral=True)
                return

            verfuegbar = get_verfuegbares_guthaben(self.customer_id, self.versicherung)
            insurance_types = get_insurance_types()
            limit = insurance_types.get(self.versicherung, {}).get("auszahlung_limit", 0.0)

            if betrag_float <= 0:
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Betrag muss > 0 sein.", ephemeral=True)
                return
            if betrag_float > verfuegbar:
                await interaction.followup.send(f"<:3518crossmark:1473009455473098894> Betrag `{betrag_float:,.2f} €` überschreitet verfügbares Guthaben `{verfuegbar:,.2f} €`.", ephemeral=True)
                return

            auszahlung_channel_id = config.get("auszahlung_channel_id")
            if not auszahlung_channel_id:
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Auszahlungs-Kanal nicht konfiguriert.", ephemeral=True)
                return

            auszahlung_channel = interaction.guild.get_channel(auszahlung_channel_id)
            if not auszahlung_channel:
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Auszahlungs-Kanal nicht gefunden.", ephemeral=True)
                return

            auszahlung_id = generate_auszahlung_id()
            beschreibung_text = self.beschreibung.value.strip() if self.beschreibung.value else "—"

            embed = discord.Embed(title="Auszahlungsantrag", color=COLOR_WARNING, timestamp=get_now())
            embed.add_field(name="__Antragsinformationen__", value=f"> <:6523information:1484250733015601182> -  `{auszahlung_id}`\n> <:9654dollar:1484250776049291324> - `{betrag_float:,.2f} €`", inline=False)
            embed.add_field(name="__Versicherungsnehmer__", value=f"> <:7549member:1484250740535988266> - {self.customer['rp_name']}\n> <:4189search:1484250713315086479> - `{self.customer_id}`", inline=False)
            embed.add_field(name="__Versicherung__", value=f"> {self.versicherung}\n> ▸ `{verfuegbar:,.2f} €` von `{limit:,.2f} €` verfügbar!", inline=False)
            embed.add_field(name="__Beschreibung__", value=f"```{beschreibung_text}```", inline=False)
            embed.add_field(name="Eingereicht von", value=f"{interaction.user.mention}", inline=True)
            embed.add_field(name="Status", value="> <:3684sync:1473009462628323523> Ausstehend", inline=True)
            embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)

            firmenkontorolle_role = interaction.guild.get_role(FIRMENKONTOROLLE_ROLE_ID)
            ping_text = firmenkontorolle_role.mention if firmenkontorolle_role else "@Firmenkontorolle"

            action_view = AuszahlungActionView(auszahlung_id, self.customer_id, betrag_float)
            msg = await auszahlung_channel.send(content=f"{ping_text} — Neuer Auszahlungsantrag!", embed=embed, view=action_view)

            data["pending_auszahlungen"][auszahlung_id] = {
                "customer_id": self.customer_id,
                "versicherung": self.versicherung,
                "betrag": betrag_float,
                "beschreibung": beschreibung_text,
                "requester_id": interaction.user.id,
                "message_id": msg.id,
                "channel_id": auszahlung_channel_id,
                "status": "ausstehend",
                "created_at": get_now().isoformat()
            }
            save_data(data)
            add_log_entry("AUSZAHLUNG_EINGEREICHT", interaction.user.id, {
                "auszahlung_id": auszahlung_id, "customer_id": self.customer_id,
                "versicherung": self.versicherung, "betrag": betrag_float
            })

            success_embed = discord.Embed(title="Auszahlungsantrag eingereicht!", description=f"Antrag `{auszahlung_id}` wurde weitergeleitet.", color=COLOR_SUCCESS)
            success_embed.add_field(name="Betrag", value=f"> `{betrag_float:,.2f} €`", inline=True)
            success_embed.add_field(name="Versicherung", value=f"> `{self.versicherung}`", inline=True)
            success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await interaction.followup.send(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Fehler beim Einreichen: {e}", exc_info=True)
            await interaction.followup.send(f"<:3518crossmark:1473009455473098894> Fehler: {e}", ephemeral=True)

class AuszahlungSelectView(discord.ui.View):
    def __init__(self, customer_id: str, customer: dict):
        super().__init__(timeout=300)
        self.customer_id = customer_id
        self.customer = customer
        insurance_types = get_insurance_types()
        options = []
        for versicherung in customer.get("versicherungen", []):
            verfuegbar = get_verfuegbares_guthaben(customer_id, versicherung)
            limit = insurance_types.get(versicherung, {}).get("auszahlung_limit", 0.0)
            bereits = limit - verfuegbar
            desc = f"Verfügbar: {verfuegbar:,.0f} € | Ausgezahlt: {bereits:,.0f} € / {limit:,.0f} €"
            options.append(discord.SelectOption(
                label=versicherung[:100], description=desc[:100], value=versicherung,
                emoji="💰" if verfuegbar > 0 else "🚫"
            ))
        self._select = discord.ui.Select(
            placeholder="Versicherung wählen...", min_values=1, max_values=1,
            options=options, custom_id="az_versicherung_select"
        )
        self._select.callback = self._on_select
        self.add_item(self._select)

    async def _on_select(self, interaction: discord.Interaction):
        selected = self._select.values[0]
        verfuegbar = get_verfuegbares_guthaben(self.customer_id, selected)
        if verfuegbar <= 0:
            await interaction.response.send_message(f"<:3518crossmark:1473009455473098894> Limit für `{selected}` bereits ausgeschöpft.", ephemeral=True)
            return
        modal = AuszahlungAntragsModal(self.customer_id, self.customer, selected)
        await interaction.response.send_modal(modal)

class AuszahlungBestaetigenModal(discord.ui.Modal, title="Auszahlung bestätigen – Nachweis"):
    auszahlungs_link = discord.ui.TextInput(label="Link der Auszahlungsnachricht", placeholder="https://discord.com/channels/...", required=True, max_length=500)

    def __init__(self, auszahlung_id: str, guild: discord.Guild, confirmer: discord.Member):
        super().__init__()
        self.auszahlung_id = auszahlung_id
        self.guild = guild
        self.confirmer = confirmer

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            pending = data.get("pending_auszahlungen", {}).get(self.auszahlung_id)
            if not pending:
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Antrag nicht gefunden.", ephemeral=True)
                return
            if pending.get("status") != "ausstehend":
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Bereits bearbeitet.", ephemeral=True)
                return

            customer_id = pending["customer_id"]
            versicherung  = pending["versicherung"]
            betrag        = pending["betrag"]
            customer      = data['customers'].get(customer_id)
            if not customer:
                await interaction.followup.send("<:3518crossmark:1473009455473098894> Kunde nicht gefunden.", ephemeral=True)
                return

            verfuegbar = get_verfuegbares_guthaben(customer_id, versicherung)
            if betrag > verfuegbar:
                await interaction.followup.send(f"<:3518crossmark:1473009455473098894> Guthaben reicht nicht aus (`{verfuegbar:,.2f} €` verfügbar).", ephemeral=True)
                return

            if "auszahlungen" not in data['customers'][customer_id]:
                data['customers'][customer_id]["auszahlungen"] = {}
            data['customers'][customer_id]["auszahlungen"][versicherung] = \
                data['customers'][customer_id]["auszahlungen"].get(versicherung, 0.0) + betrag

            data["pending_auszahlungen"][self.auszahlung_id].update({
                "status": "bestaetigt",
                "bestaetigt_von": self.confirmer.id,
                "bestaetigt_am": get_now().isoformat(),
                "auszahlungs_link": self.auszahlungs_link.value
            })
            save_data(data)

            thread_id = customer.get("thread_id")
            if thread_id:
                try:
                    thread = self.guild.get_thread(thread_id) or await self.guild.fetch_channel(thread_id)
                    if thread:
                        neues_guthaben = get_verfuegbares_guthaben(customer_id, versicherung)
                        vermerk = discord.Embed(title="Auszahlungsvermerk", color=COLOR_PRIMARY, timestamp=get_now())
                        vermerk.add_field(name="__Antragsinformationen__", value=f"> Antrags-ID: `{self.auszahlung_id}`\n> Versicherung: `{versicherung}`\n> [Zur Auszahlungsnachricht]({self.auszahlungs_link.value})", inline=False)
                        vermerk.add_field(name="__Auszahlungsdetails__", value=f"> Verfügbar vor Auszahlung: `{verfuegbar:,.2f} €`\n> Ausgezahlt: `-` `{betrag:,.2f} €`\n> **Restliches Guthaben: `{neues_guthaben:,.2f} €`**", inline=False)
                        vermerk.add_field(name="Datum", value=f"> {get_now().strftime('%d.%m.%Y, %H:%M Uhr')}", inline=False)
                        vermerk.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
                        await thread.send(embed=vermerk)
                except Exception as e:
                    logger.error(f"Fehler beim Posten des Vermerks: {e}")

            try:
                az_channel = self.guild.get_channel(pending["channel_id"])
                if az_channel:
                    orig_msg = await az_channel.fetch_message(pending["message_id"])
                    if orig_msg.embeds:
                        upd = orig_msg.embeds[0]
                        upd.color = COLOR_SUCCESS
                        for i, field in enumerate(upd.fields):
                            if field.name == "Status":
                                upd.set_field_at(i, name="Status", value="Genehmigt", inline=True)
                                break
                        upd.add_field(name="Genehmigt von", value=f"{self.confirmer.mention}", inline=True)
                        upd.add_field(name="Genehmigt am", value=get_now().strftime('%d.%m.%Y • %H:%M'), inline=True)
                        await orig_msg.edit(embed=upd, view=None)
            except Exception as e:
                logger.error(f"Fehler beim Aktualisieren des Antrags: {e}")

            add_log_entry("AUSZAHLUNG_BESTAETIGT", self.confirmer.id, {
                "auszahlung_id": self.auszahlung_id, "customer_id": customer_id,
                "versicherung": versicherung, "betrag": betrag
            })

            log_embed = discord.Embed(title="Auszahlung bestätigt!", color=COLOR_SUCCESS, timestamp=get_now())
            log_embed.add_field(name="Antrags-ID", value=f"> `{self.auszahlung_id}`", inline=False)
            log_embed.add_field(name="Versicherungsnehmer", value=f"> {customer['rp_name']}\n> `{customer_id}`", inline=False)
            log_embed.add_field(name="Betrag", value=f"> `{betrag:,.2f} €`", inline=True)
            log_embed.add_field(name="Genehmigt von", value=f"> {self.confirmer.mention}", inline=True)
            log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await send_to_log_channel(self.guild, log_embed)

            success_embed = discord.Embed(title="Auszahlung bestätigt!", description=f"Auszahlung `{self.auszahlung_id}` genehmigt und in der Kundenakte vermerkt.", color=COLOR_SUCCESS)
            success_embed.add_field(name="Betrag", value=f"> `{betrag:,.2f} €`", inline=True)
            success_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await interaction.followup.send(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Fehler beim Bestätigen: {e}", exc_info=True)
            await interaction.followup.send(f"<:3518crossmark:1473009455473098894> Fehler: {e}", ephemeral=True)

class AuszahlungActionView(discord.ui.View):
    def __init__(self, auszahlung_id: str, customer_id: str, betrag: float):
        super().__init__(timeout=None)
        self.auszahlung_id = auszahlung_id
        self.customer_id   = customer_id
        self.betrag        = betrag

    def _get_auszahlung_id_from_message(self, message: discord.Message) -> str | None:
        if not message or not message.embeds:
            return None
        for field in message.embeds[0].fields:
            if "Antrags-ID" in field.value or "Antrags-ID" in field.name:
                for part in field.value.split("\n"):
                    if "Antrags-ID" in part:
                        start = part.find("`") + 1
                        end   = part.rfind("`")
                        if start > 0 and end > start:
                            return part[start:end]
        return self.auszahlung_id

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.green, custom_id="auszahlung_bestaetigen", emoji="<:3518checkmark:1484250693421367389>")
    async def bestaetigen(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_firmenkontorolle(interaction):
            await interaction.response.send_message(
                embed=build_error_embed("Zugriff verweigert!", "Nur das Firmenkonto kann Auszahlungsanträge bearbeiten.", "Firmenkonto"),
                ephemeral=True
            )
            return
        az_id = self._get_auszahlung_id_from_message(interaction.message)
        pending = data.get("pending_auszahlungen", {}).get(az_id)
        if not pending or pending.get("status") != "ausstehend":
            await interaction.response.send_message("<:3518crossmark:1473009455473098894> Bereits bearbeitet oder nicht gefunden.", ephemeral=True)
            return
        modal = AuszahlungBestaetigenModal(az_id, interaction.guild, interaction.user)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.danger, custom_id="auszahlung_abbrechen", emoji="<:3518crossmark:1473009455473098894>")
    async def abbrechen(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_firmenkontorolle(interaction):
            await interaction.response.send_message(
                embed=build_error_embed("Zugriff verweigert!", "Nur das Firmenkonto kann Auszahlungsanträge bearbeiten.", "Firmenkonto"),
                ephemeral=True
            )
            return
        az_id = self._get_auszahlung_id_from_message(interaction.message)
        pending = data.get("pending_auszahlungen", {}).get(az_id)
        if not pending or pending.get("status") != "ausstehend":
            await interaction.response.send_message("<:3518crossmark:1473009455473098894> Bereits bearbeitet.", ephemeral=True)
            return

        data["pending_auszahlungen"][az_id].update({
            "status": "abgelehnt",
            "abgelehnt_von": interaction.user.id,
            "abgelehnt_am": get_now().isoformat()
        })
        save_data(data)

        try:
            if interaction.message.embeds:
                upd = interaction.message.embeds[0]
                upd.color = COLOR_ERROR
                for i, field in enumerate(upd.fields):
                    if field.name == "Status":
                        upd.set_field_at(i, name="Status", value="❌ Abgelehnt", inline=True)
                        break
                upd.add_field(name="Abgelehnt von", value=f"{interaction.user.mention}", inline=True)
                upd.add_field(name="Abgelehnt am", value=get_now().strftime('%d.%m.%Y • %H:%M'), inline=True)
                await interaction.message.edit(embed=upd, view=None)
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren: {e}")

        customer_id = pending.get("customer_id", "—")
        betrag = pending.get("betrag", 0.0)
        add_log_entry("AUSZAHLUNG_ABGELEHNT", interaction.user.id, {"auszahlung_id": az_id, "customer_id": customer_id, "betrag": betrag})

        log_embed = discord.Embed(title="Auszahlungsantrag abgelehnt!", color=COLOR_ERROR, timestamp=get_now())
        log_embed.add_field(name="Antrags-ID", value=f"> `{az_id}`", inline=False)
        log_embed.add_field(name="Abgelehnt von", value=f"> {interaction.user.mention}", inline=False)
        log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await send_to_log_channel(interaction.guild, log_embed)

        await interaction.response.send_message(f"<:3518checkmark:1484250693421367389> Antrag `{az_id}` wurde abgelehnt.", ephemeral=True)

@bot.tree.command(name="auszahlung-einreichen", description="Reicht einen Auszahlungsantrag für einen Kunden ein")
@app_commands.describe(customer_id="Versicherungsnehmer-ID des Kunden")
async def auszahlung_einreichen(interaction: discord.Interaction, customer_id: str):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(
            embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Auszahlungsanträge einreichen.", "Mitarbeiter"),
            ephemeral=True
        )
        return

    if customer_id not in data['customers']:
        await interaction.response.send_message(
            embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."),
            ephemeral=True
        )
        return

    customer = data['customers'][customer_id]
    if not customer.get("versicherungen"):
        await interaction.response.send_message("<:3518crossmark:1473009455473098894> Keine Versicherungen vorhanden.", ephemeral=True)
        return

    insurance_types = get_insurance_types()
    limits_text = ""
    for versicherung in customer.get("versicherungen", []):
        verfuegbar = get_verfuegbares_guthaben(customer_id, versicherung)
        limit = insurance_types.get(versicherung, {}).get("auszahlung_limit", 0.0)
        status = "💰" if verfuegbar > 0 else "🚫"
        limits_text += f"{status} **{versicherung}**\n> Verfügbar: `{verfuegbar:,.2f} €` von `{limit:,.2f} €`\n"

    select_embed = discord.Embed(title="Auszahlungsantrag einreichen", description=f"**Versicherungsnehmer:** {customer['rp_name']} (`{customer_id}`)", color=COLOR_INFO)
    select_embed.add_field(name="Auszahlungsguthaben Übersicht", value=limits_text or "Keine Daten", inline=False)

    view = AuszahlungSelectView(customer_id, customer)
    await interaction.response.send_message(embed=select_embed, view=view, ephemeral=True)

# ═══════════════════════════════════════════════════════
#   TICKET SYSTEM
# ═══════════════════════════════════════════════════════
class KundenkontaktView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Kundenkontakt anfragen!", style=discord.ButtonStyle.secondary, custom_id="open_kundenkontakt", emoji="<:6224mail:1484250731098673243>")
    async def open_kundenkontakt(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal())

class SchadensmeldungView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Schadensmeldung einreichen!", style=discord.ButtonStyle.secondary, custom_id="open_schadensmeldung", emoji="<:6224mail:1484250731098673243>")
    async def open_schadensmeldung(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SchadensmeldungModal())

class TicketModal(discord.ui.Modal, title="Kundenkontakt-Anfrage"):
    customer_id_input = discord.ui.TextInput(label="Versicherungsnehmer-ID", placeholder="VN-XXXXXXXX", required=True, max_length=15)
    reason = discord.ui.TextInput(label="Grund der Kontaktaufnahme", style=discord.TextStyle.paragraph, placeholder="Bitte beschreiben Sie detailliert den Anlass...", required=True, max_length=2000)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            customer_id = self.customer_id_input.value.strip()
            if customer_id not in data['customers']:
                await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."), ephemeral=True)
                return

            customer = data['customers'][customer_id]
            guild    = interaction.guild
            category = guild.get_channel(config.get("kundenkontakt_category_id")) if config.get("kundenkontakt_category_id") else None
            if not category:
                await interaction.followup.send(embed=build_error_embed("Nicht konfiguriert!", "Kundenkontakt-Kategorie nicht eingerichtet."), ephemeral=True)
                return

            customer_user = guild.get_member(customer['discord_user_id'])
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            mitarbeiter_role = guild.get_role(MITARBEITER_ROLE_ID)
            leitungsebene_role = guild.get_role(LEITUNGSEBENE_ROLE_ID)
            if mitarbeiter_role:
                overwrites[mitarbeiter_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            if leitungsebene_role:
                overwrites[leitungsebene_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            if customer_user:
                overwrites[customer_user] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)

            ticket_channel = await category.create_text_channel(
                name=f"kontakt-{customer_id.lower()}",
                topic=f"Kundenkontakt: {customer['rp_name']} | {customer_id}",
                overwrites=overwrites
            )

            insurance_types = get_insurance_types()
            embed = discord.Embed(title="Support-Ticket", description="**Kundenkontakt-Anfrage**", color=COLOR_INFO, timestamp=get_now())
            embed.add_field(name="__Ticketinformationen__", value=f"> Datum: {get_now().strftime('%d.%m.%Y • %H:%M')}\n> Kunden-ID: `{customer_id}`", inline=False)
            embed.add_field(name="__Beteiligte Personen__", value=f"> Mitarbeiter: {interaction.user.mention}\n> Versicherungsnehmer: {customer['rp_name']}", inline=False)
            embed.add_field(name="__Anlass der Kontaktaufnahme__", value=self.reason.value, inline=False)
            insurance_info = "\n".join(f"> ▸ {ins}" for ins in customer['versicherungen'])
            embed.add_field(name="__Kundeninformationen__", value=f"{insurance_info}\n> Monatsbeitrag: `{customer['total_monthly_price']:,.2f} €`\n> Kartennummer: `{customer['hbpay_nummer']}`\n> Economy-ID: `{customer['economy_id']}`", inline=False)
            embed.set_footer(text="Nutzen Sie den Button unten zum Schließen • Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)

            close_view = TicketCloseView(ticket_channel.id, customer_id)
            mentions = [interaction.user.mention]
            if customer_user:
                mentions.append(customer_user.mention)
            await ticket_channel.send(" ".join(mentions), embed=embed, view=close_view)

            add_log_entry("TICKET_ERSTELLT", interaction.user.id, {"customer_id": customer_id, "channel_id": ticket_channel.id})

            log_embed = discord.Embed(title="Neues Support-Ticket!", color=COLOR_INFO, timestamp=get_now())
            log_embed.add_field(name="Ticket-Channel", value=f"> {ticket_channel.mention}", inline=False)
            log_embed.add_field(name="Versicherungsnehmer", value=f"> {customer['rp_name']}\n> `{customer_id}`", inline=False)
            log_embed.add_field(name="Erstellt von", value=f"> {interaction.user.mention}", inline=False)
            log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
            await send_to_log_channel(interaction.guild, log_embed)

            success_embed = discord.Embed(title="Ticket erstellt!", description=f"Kundenkontakt-Ticket: {ticket_channel.mention}", color=COLOR_SUCCESS)
            await interaction.followup.send(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Fehler: {e}", exc_info=True)
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

class SchadensmeldungModal(discord.ui.Modal, title="Schadensmeldung einreichen"):
    customer_id_input = discord.ui.TextInput(label="Versicherungsnehmer-ID", placeholder="VN-24123456", required=True, max_length=20)
    geschaedigter     = discord.ui.TextInput(label="Geschädigter (RP-Name)", placeholder="Max Mustermann", required=True, max_length=100)
    taeter            = discord.ui.TextInput(label="Täter (RP-Name)", placeholder="John Doe", required=True, max_length=100)
    beschreibung      = discord.ui.TextInput(label="Beschreibung des Vorfalls", style=discord.TextStyle.paragraph, placeholder="Bitte beschreiben Sie den Vorfall so detailliert wie möglich...", required=True, max_length=1000)
    rechnung          = discord.ui.TextInput(label="Rechnung/Zahlungsnachweis", placeholder="Rechnungsnummer oder Link", required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            customer_id = self.customer_id_input.value.strip()
            if customer_id not in data['customers']:
                await interaction.followup.send(embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."), ephemeral=True)
                return

            customer = data['customers'][customer_id]
            guild    = interaction.guild
            category = guild.get_channel(config.get("schadensmeldung_category_id")) if config.get("schadensmeldung_category_id") else None
            if not category:
                await interaction.followup.send(embed=build_error_embed("Nicht konfiguriert!", "Schadensmeldungs-Kategorie nicht eingerichtet."), ephemeral=True)
                return

            customer_user = guild.get_member(customer['discord_user_id'])
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            mitarbeiter_role = guild.get_role(MITARBEITER_ROLE_ID)
            leitungsebene_role = guild.get_role(LEITUNGSEBENE_ROLE_ID)
            if mitarbeiter_role:
                overwrites[mitarbeiter_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            if leitungsebene_role:
                overwrites[leitungsebene_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            if customer_user:
                overwrites[customer_user] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)

            ticket_channel = await category.create_text_channel(
                name=f"schaden-{customer_id.lower()}",
                topic=f"Schadensmeldung: {customer['rp_name']} | {customer_id}",
                overwrites=overwrites
            )

            embed = discord.Embed(title="Schadensmeldung", description="**Neue Schadensmeldung wurde eingereicht**", color=COLOR_DAMAGE, timestamp=get_now())
            embed.add_field(name="__Schadensfallinformationen__", value=f"> Kunde: {customer['rp_name']} (`{customer_id}`)\n> Gemeldet von: {interaction.user.mention}", inline=False)
            embed.add_field(name="__Beteiligte Personen__", value=f"> Geschädigter: {self.geschaedigter.value}\n> Täter: {self.taeter.value}", inline=False)
            embed.add_field(name="__Beschreibung__", value=f"```{self.beschreibung.value}```", inline=False)
            embed.add_field(name="__Nachweis__", value=f"> {self.rechnung.value}", inline=False)
            embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)

            close_view = TicketCloseView(ticket_channel.id, customer_id)
            await ticket_channel.send(f"{interaction.user.mention}", embed=embed, view=close_view)

            success_embed = discord.Embed(title="Schadensmeldung eingereicht!", description=f"Ihr Schadensticket wurde erstellt: {ticket_channel.mention}", color=COLOR_SUCCESS)
            await interaction.followup.send(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Fehler: {e}")
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

class TicketCloseView(discord.ui.View):
    def __init__(self, channel_id: int, customer_id: str):
        super().__init__(timeout=None)
        self.channel_id  = channel_id
        self.customer_id = customer_id

    @discord.ui.button(label="Ticket schließen", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="<:3518crossmark:1473009455473098894>")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_mitarbeiter(interaction):
            await interaction.response.send_message(
                embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Tickets schließen.", "Mitarbeiter"),
                ephemeral=True
            )
            return

        channel = interaction.channel
        customer_id = self.customer_id
        if not customer_id and channel.topic:
            parts = channel.topic.split("|")
            if len(parts) >= 2:
                customer_id = parts[-1].strip()

        close_embed = discord.Embed(
            title="Ticket wird geschlossen!",
            description=f"Dieses Ticket wird in 5 Sekunden geschlossen.\n\n> Geschlossen von: {interaction.user.mention}",
            color=COLOR_WARNING,
            timestamp=get_now()
        )
        close_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.response.send_message(embed=close_embed)

        log_embed = discord.Embed(title="Support-Ticket geschlossen!", color=COLOR_WARNING, timestamp=get_now())
        log_embed.add_field(name="Ticket-Channel", value=f"> `{channel.name}`", inline=False)
        log_embed.add_field(name="Kunden-ID", value=f"> `{customer_id or '—'}`", inline=False)
        log_embed.add_field(name="Geschlossen von", value=f"> {interaction.user.mention}", inline=False)
        log_embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await send_to_log_channel(interaction.guild, log_embed)

        add_log_entry("TICKET_GESCHLOSSEN", interaction.user.id, {
            "customer_id": customer_id, "channel_id": channel.id, "channel_name": channel.name
        })

        await asyncio.sleep(5)
        await channel.delete(reason=f"Ticket geschlossen von {interaction.user}")

# ═══════════════════════════════════════════════════════
#   TICKET MEMBER MANAGEMENT
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="add", description="Fügt eine Person zum aktuellen Ticket hinzu")
@app_commands.describe(user="Der User, der hinzugefügt werden soll")
async def add_user_to_ticket(interaction: discord.Interaction, user: discord.Member):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Personen hinzufügen.", "Mitarbeiter"), ephemeral=True)
        return
    if not (interaction.channel.name.startswith("kontakt-") or interaction.channel.name.startswith("schaden-")):
        await interaction.response.send_message("<:3518crossmark:1473009455473098894> Nur in Ticket-Channels nutzbar.", ephemeral=True)
        return
    await interaction.channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)
    embed = discord.Embed(title="Person hinzugefügt!", description=f"> {interaction.user.mention} hat {user.mention} zum Ticket hinzugefügt.", color=COLOR_SUCCESS)
    embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove", description="Entfernt eine Person vom aktuellen Ticket")
@app_commands.describe(user="Der User, der entfernt werden soll")
async def remove_user_from_ticket(interaction: discord.Interaction, user: discord.Member):
    if not is_mitarbeiter(interaction):
        await interaction.response.send_message(embed=build_error_embed("Zugriff verweigert!", "Nur Mitarbeiter können Personen entfernen.", "Mitarbeiter"), ephemeral=True)
        return
    if not (interaction.channel.name.startswith("kontakt-") or interaction.channel.name.startswith("schaden-")):
        await interaction.response.send_message("<:3518crossmark:1473009455473098894> Nur in Ticket-Channels nutzbar.", ephemeral=True)
        return
    await interaction.channel.set_permissions(user, overwrite=None)
    embed = discord.Embed(title="Person entfernt!", description=f"> {interaction.user.mention} hat {user.mention} aus dem Ticket entfernt.", color=COLOR_WARNING)
    embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=embed)

# ═══════════════════════════════════════════════════════
#   LOGS ANZEIGEN
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="logs-anzeigen", description="Zeigt die letzten Bot-Aktivitäten an")
@app_commands.describe(anzahl="Anzahl der anzuzeigenden Log-Einträge (Standard: 10)")
async def show_logs(interaction: discord.Interaction, anzahl: int = 10):
    if not is_leitungsebene(interaction):
        await interaction.response.send_message(embed=build_error_embed("Zugriff verweigert!", "Nur die Leitungsebene kann Logs einsehen.", "Leitungsebene"), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if not data['logs']:
            await interaction.followup.send(embed=discord.Embed(title="Keine Logs vorhanden!", color=COLOR_INFO), ephemeral=True)
            return

        anzahl = max(1, min(anzahl, 25))
        recent_logs = list(reversed(data['logs'][-anzahl:]))

        action_map = {
            "KUNDENAKTE_ERSTELLT": ("📁", "Kundenakte erstellt"),
            "RECHNUNG_ERSTELLT": ("🧾", "Rechnung ausgestellt"),
            "RECHNUNG_ARCHIVIERT": ("✅", "Rechnung archiviert"),
            "MAHNUNG_1": ("⚠️", "1. Mahnung"),
            "MAHNUNG_2": ("⚠️", "2. Mahnung (+5%)"),
            "MAHNUNG_3": ("🚨", "3. Mahnung (+10%)"),
            "TICKET_ERSTELLT": ("🎫", "Ticket erstellt"),
            "TICKET_GESCHLOSSEN": ("🔒", "Ticket geschlossen"),
            "AKTE_ARCHIVIERT": ("🗄️", "Akte archiviert"),
            "AUSZAHLUNG_EINGEREICHT": ("💰", "Auszahlungsantrag"),
            "AUSZAHLUNG_BESTAETIGT": ("✅", "Auszahlung bestätigt"),
            "AUSZAHLUNG_ABGELEHNT": ("❌", "Auszahlung abgelehnt"),
            "VERSICHERUNG_NACHGEBUCHT": ("➕", "Versicherung nachgebucht"),
            "VERSICHERUNG_GEKUENDIGT": ("❌", "Versicherung gekündigt"),
            "STEUERSATZ_GEAENDERT": ("💱", "Steuersatz geändert"),
            "VERSICHERUNG_HINZUGEFUEGT": ("📋", "Versicherung hinzugefügt"),
        }

        embed = discord.Embed(title="System-Aktivitätsprotokoll", description=f"**Letzte {len(recent_logs)} Aktivitäten**", color=COLOR_PRIMARY, timestamp=get_now())

        for log in recent_logs:
            timestamp  = make_aware(datetime.fromisoformat(log['timestamp'])).strftime('%d.%m.%Y • %H:%M:%S')
            user       = interaction.guild.get_member(log['user_id']) if log.get('user_id') and log['user_id'] != 0 else None
            user_name  = user.mention if user else "🤖 System"
            action     = log['action']
            emoji, display = action_map.get(action, ("📌", action))
            details    = log.get('details', {})
            detail_parts = []
            if 'customer_id' in details:
                detail_parts.append(f"Kunden-ID: `{details['customer_id']}`")
            if 'customer_name' in details:
                detail_parts.append(f"Kunde: **{details['customer_name']}**")
            if 'invoice_id' in details:
                detail_parts.append(f"Rechnung: `{details['invoice_id']}`")
            if 'auszahlung_id' in details:
                detail_parts.append(f"Auszahlung: `{details['auszahlung_id']}`")
            detail_str = "\n".join(f"> {p}" for p in detail_parts[:3]) if detail_parts else "> —"
            embed.add_field(
                name=f"{emoji} {display}",
                value=f"> **{timestamp}**\n> {user_name}\n{detail_str}",
                inline=False
            )

        embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Fehler Logs: {e}", exc_info=True)
        await interaction.followup.send(embed=build_error_embed("Fehler!", str(e)), ephemeral=True)

# ═══════════════════════════════════════════════════════
#   AKTE ANZEIGEN — GEÄNDERT
#   Kunden sehen eigene Akte ohne ID-Eingabe
#   Mitarbeiter geben eine ID an
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="akte-anzeigen", description="Zeigt die eigene Versicherungsakte oder die eines Kunden (Mitarbeiter)")
@app_commands.describe(customer_id="Versicherungsnehmer-ID (nur für Mitarbeiter; Kunden leer lassen)")
async def show_customer(interaction: discord.Interaction, customer_id: Optional[str] = None):
    mitarbeiter = is_mitarbeiter(interaction)

    if not mitarbeiter:
        # Kunden: eigene Akte automatisch anhand der Discord-User-ID suchen
        found_id = None
        for cid, c in data['customers'].items():
            if c.get('discord_user_id') == interaction.user.id and c.get('status') == 'aktiv':
                found_id = cid
                break
        if not found_id:
            await interaction.response.send_message(
                embed=build_error_embed(
                    "Keine Akte gefunden!",
                    "Für Ihren Account wurde keine aktive Versicherungsakte gefunden.\nBitte wenden Sie sich an einen Mitarbeiter."
                ),
                ephemeral=True
            )
            return
        customer_id = found_id
    else:
        # Mitarbeiter: ID ist Pflicht
        if not customer_id:
            await interaction.response.send_message(
                embed=build_error_embed("Fehlende Angabe!", "Bitte geben Sie eine Versicherungsnehmer-ID an."),
                ephemeral=True
            )
            return
        if customer_id not in data['customers']:
            await interaction.response.send_message(
                embed=build_error_embed("Nicht gefunden!", f"Keine Akte mit ID `{customer_id}`."),
                ephemeral=True
            )
            return

    customer = data['customers'][customer_id]
    insurance_types = get_insurance_types()

    embed = discord.Embed(title=f"📁 Kundenakte — {customer['rp_name']}", color=COLOR_PRIMARY, timestamp=get_now())
    embed.add_field(
        name="__Stammdaten__",
        value=(
            f"> Kunden-ID: `{customer_id}`\n"
            f"> RP-Name: **{customer['rp_name']}**\n"
            f"> Kartenzahlung: `{customer['hbpay_nummer']}`\n"
            f"> Economy-ID: `{customer['economy_id']}`\n"
            f"> Status: `{customer.get('status', 'aktiv')}`"
        ),
        inline=False
    )

    auszahlungen = customer.get("auszahlungen", {})
    versicherungen_text = ""
    for ins in customer.get("versicherungen", []):
        limit      = insurance_types.get(ins, {}).get("auszahlung_limit", 0.0)
        ausgezahlt = auszahlungen.get(ins, 0.0)
        verfuegbar = max(0.0, limit - ausgezahlt)
        bar_filled = int((ausgezahlt / limit * 10)) if limit > 0 else 0
        bar        = "█" * bar_filled + "░" * (10 - bar_filled)
        versicherungen_text += f"> **{ins}**\n> `{bar}` {ausgezahlt:,.0f} € / {limit:,.0f} €\n"
    embed.add_field(name="__Versicherungen & Auszahlungslimits__", value=versicherungen_text or "> Keine", inline=False)

    offene_rechnungen   = [inv for inv in data['invoices'].values() if inv['customer_id'] == customer_id and not inv.get('paid')]
    bezahlte_rechnungen = [inv for inv in data['invoices'].values() if inv['customer_id'] == customer_id and inv.get('paid')]
    embed.add_field(
        name="__Rechnungsübersicht__",
        value=(
            f"> Offene Rechnungen: `{len(offene_rechnungen)}`\n"
            f"> Bezahlte Rechnungen: `{len(bezahlte_rechnungen)}`\n"
            f"> Monatsbeitrag: `{customer['total_monthly_price']:,.2f} €`"
        ),
        inline=False
    )

    created_at = make_aware(datetime.fromisoformat(customer['created_at'])).strftime('%d.%m.%Y • %H:%M Uhr')
    embed.add_field(name="__Metadaten__", value=f"> Angelegt am: {created_at}\n> Discord: <@{customer['discord_user_id']}>", inline=False)

    thread_id = customer.get("thread_id")
    if thread_id:
        embed.add_field(name="__Akte__", value=f"> <#{thread_id}>", inline=False)

    embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ═══════════════════════════════════════════════════════
#   STATISTIKEN
# ═══════════════════════════════════════════════════════
@bot.tree.command(name="statistiken", description="Zeigt eine Übersicht aller Bot-Statistiken")
async def show_stats(interaction: discord.Interaction):
    if not is_leitungsebene(interaction):
        await interaction.response.send_message(embed=build_error_embed("Zugriff verweigert!", "Nur die Leitungsebene kann Statistiken einsehen.", "Leitungsebene"), ephemeral=True)
        return

    customers   = data.get('customers', {})
    invoices    = data.get('invoices', {})
    pending_az  = data.get('pending_auszahlungen', {})

    aktive_kunden       = sum(1 for c in customers.values() if c.get('status') == 'aktiv')
    archivierte_kunden  = sum(1 for c in customers.values() if c.get('status') == 'archiviert')
    offene_rechnungen   = sum(1 for inv in invoices.values() if not inv.get('paid'))
    bezahlte_rechnungen = sum(1 for inv in invoices.values() if inv.get('paid'))
    ausstehende_az      = sum(1 for az in pending_az.values() if az.get('status') == 'ausstehend')
    bestaetigte_az      = sum(1 for az in pending_az.values() if az.get('status') == 'bestaetigt')
    total_monatsbeitraege = sum(c.get('total_monthly_price', 0) for c in customers.values() if c.get('status') == 'aktiv')
    total_ausgezahlt    = sum(sum(c.get('auszahlungen', {}).values()) for c in customers.values())

    versicherung_count = {}
    for c in customers.values():
        if c.get('status') == 'aktiv':
            for ins in c.get('versicherungen', []):
                versicherung_count[ins] = versicherung_count.get(ins, 0) + 1
    beliebteste = max(versicherung_count, key=versicherung_count.get) if versicherung_count else "—"

    embed = discord.Embed(title="InsuranceGuard v3 — Statistiken", color=COLOR_INFO, timestamp=get_now())
    embed.add_field(name="__Kundenstamm__", value=f"> Aktive Kunden: **`{aktive_kunden}`**\n> Archivierte Kunden: `{archivierte_kunden}`\n> Gesamt: `{len(customers)}`", inline=True)
    embed.add_field(name="__Finanzen__", value=f"> Monatsbeiträge gesamt: **`{total_monatsbeitraege:,.2f} €`**\n> Gesamt ausgezahlt: `{total_ausgezahlt:,.2f} €`", inline=True)
    embed.add_field(name="__Rechnungen__", value=f"> Offen: **`{offene_rechnungen}`**\n> Bezahlt: `{bezahlte_rechnungen}`", inline=True)
    embed.add_field(name="__Auszahlungen__", value=f"> Ausstehend: **`{ausstehende_az}`**\n> Bestätigt: `{bestaetigte_az}`", inline=True)
    embed.add_field(name="__Beliebteste Versicherung__", value=f"> {beliebteste}", inline=True)
    embed.add_field(name="__Log-Einträge__", value=f"> `{len(data.get('logs', []))}`", inline=True)
    embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ═══════════════════════════════════════════════════════
#   AUTOMATISCHE TASKS
# ═══════════════════════════════════════════════════════
@tasks.loop(hours=24)
async def check_invoices():
    try:
        now = get_now()
        for invoice_id, invoice_data in list(data['invoices'].items()):
            if invoice_data.get('paid', False):
                continue
            try:
                due_date = make_aware(datetime.fromisoformat(invoice_data['due_date']))
            except Exception:
                continue
            days_overdue  = (now - due_date).days
            if days_overdue < 0:
                continue
            reminder_count = invoice_data.get('reminder_count', 0)
            if days_overdue == 0 and reminder_count == 0:
                await send_reminder(invoice_id, invoice_data, 1, 0)
                data['invoices'][invoice_id]['reminder_count'] = 1
                save_data(data)
            elif days_overdue == 1 and reminder_count == 1:
                new_amount = invoice_data['original_betrag'] * 1.05
                data['invoices'][invoice_id]['betrag'] = new_amount
                await send_reminder(invoice_id, data['invoices'][invoice_id], 2, 5)
                data['invoices'][invoice_id]['reminder_count'] = 2
                save_data(data)
            elif days_overdue == 2 and reminder_count == 2:
                new_amount = invoice_data['original_betrag'] * 1.10
                data['invoices'][invoice_id]['betrag'] = new_amount
                await send_reminder(invoice_id, data['invoices'][invoice_id], 3, 10)
                data['invoices'][invoice_id]['reminder_count'] = 3
                save_data(data)
    except Exception as e:
        logger.error(f"Fehler bei Mahnungsprüfung: {e}", exc_info=True)

@tasks.loop(hours=3)
async def auto_backup():
    global _last_data_hash
    try:
        if not config.get("log_channel_id"):
            return
        current_hash = _get_data_hash()
        if current_hash == _last_data_hash:
            logger.info("Auto-Backup: Keine Änderungen – übersprungen.")
            return

        timestamp_str = get_now().strftime("%Y%m%d_%H%M%S")

        embed = discord.Embed(title="Automatisches Datenbank-Backup", color=COLOR_PRIMARY, timestamp=get_now())
        embed.add_field(name="__Information__", value="> Alle `3 Stunden` werden die Daten in diesen Kanal gesichert.", inline=False)
        embed.add_field(name="__Enthaltene Dateien__", value="> - `insurance_data.json`\n> - `bot_config.json`", inline=False)
        embed.add_field(name="__Zeitstempel__", value=f"> {get_now().strftime('%d.%m.%Y, %H:%M:%S Uhr')}", inline=False)
        embed.set_footer(text="Copyright © InsuranceGuard v3", icon_url=FOOTER_ICON)

        for guild in bot.guilds:
            log_channel = guild.get_channel(config["log_channel_id"])
            if log_channel:
                buf  = create_zip_buffer()
                file = discord.File(buf, filename=f"auto_backup_{timestamp_str}.zip")
                await log_channel.send(embed=embed, file=file)
                break

        _last_data_hash = current_hash
        logger.info(f"Auto-Backup gesendet um {get_now().strftime('%H:%M:%S')}")
    except Exception as e:
        logger.error(f"Fehler Auto-Backup: {e}", exc_info=True)

# ═══════════════════════════════════════════════════════
#   KEEP-ALIVE (Render.com)
# ═══════════════════════════════════════════════════════
from flask import Flask
from threading import Thread

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "InsuranceGuard v3 läuft!"

@app_flask.route('/health')
def health():
    return {
        "status": "healthy",
        "bot": bot.user.name if bot.user else "starting",
        "version": "v3",
        "latency_ms": round(bot.latency * 1000) if bot.latency else None,
        "customers": len(data.get('customers', {})),
        "timestamp": get_now().isoformat()
    }

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app_flask.run(host='0.0.0.0', port=port, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# ═══════════════════════════════════════════════════════
#   MAIN
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    keep_alive()
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.error("DISCORD_TOKEN nicht gefunden! Bitte als Umgebungsvariable setzen.")
    else:
        logger.info("InsuranceGuard v3 wird gestartet...")
        bot.run(token)
