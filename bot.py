"""
🔍 OKAZJE BOT — Telegram bot do analizy okazji kolekcjonerskich
Wklej link → dostaniesz analizę AI (oryginał/replika, wycena, werdykt)
Auto-monitoring Sprzedajemy.pl i Gratka.pl co 30 minut
"""

import os
import re
import json
import logging
import asyncio
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from bs4 import BeautifulSoup
import anthropic

# ── CONFIG ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CHAT_ID = os.environ.get("CHAT_ID", "")  # Twój Telegram chat ID (opcjonalnie, do auto-alertów)

SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL", "30"))
MAX_PRICE = int(os.environ.get("MAX_PRICE", "550"))
MIN_MARGIN_PERCENT = int(os.environ.get("MIN_MARGIN", "200"))

# Słowa kluczowe do monitoringu — edytuj przez /keywords w bocie
DEFAULT_KEYWORDS = [
    "komiks PRL",
    "Relax komiks",
    "Kapitan Żbik",
    "figurka Ćmielów",
    "porcelana PRL",
    "zegarek Błonie",
    "zegarek Rakieta",
    "zegarek Wostok",
    "obraz olejny",
    "szabla",
    "bagnet",
    "Lem pierwsze wydanie",
    "Sapkowski wydanie",
    "ikona prawosławna",
    "sztućce srebrne",
    "kordelas",
]

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("okazje-bot")

# ── DATA MODEL ────────────────────────────────────────────────────────────────

@dataclass
class Offer:
    url: str
    title: str
    price: float
    description: str
    location: str
    platform: str
    seller: str = ""
    condition: str = ""
    images: list = field(default_factory=list)
    scraped_at: str = ""
    analysis: str = ""
    verdict: str = ""  # "BUY", "NEGOTIATE", "SKIP", "INVESTIGATE"
    estimated_value_low: float = 0
    estimated_value_high: float = 0

    @property
    def id(self) -> str:
        return hashlib.md5(self.url.encode()).hexdigest()[:12]

    @property
    def margin_low(self) -> float:
        if self.price <= 0:
            return 0
        return ((self.estimated_value_low - self.price) / self.price) * 100

    @property
    def margin_high(self) -> float:
        if self.price <= 0:
            return 0
        return ((self.estimated_value_high - self.price) / self.price) * 100


# ── SCRAPERS ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}


async def scrape_url(url: str) -> Optional[Offer]:
    """Pobierz dane z dowolnego linku — wykrywa platformę automatycznie."""
    try:
        if "sprzedajemy.pl" in url:
            return await scrape_sprzedajemy(url)
        elif "olx.pl" in url:
            return await scrape_olx(url)
        elif "allegro.pl" in url:
            return await scrape_allegro(url)
        elif "vinted.pl" in url:
            return await scrape_vinted(url)
        elif "gratka.pl" in url:
            return await scrape_gratka(url)
        else:
            return await scrape_generic(url)
    except Exception as e:
        logger.error(f"Scraping error for {url}: {e}")
        return None


async def scrape_sprzedajemy(url: str) -> Optional[Offer]:
    """Scraper dla Sprzedajemy.pl — relatywnie łatwa strona do parsowania."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Tytuł
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

    # Cena
    price = 0.0
    price_patterns = [
        soup.find("span", class_=re.compile(r"price|cena", re.I)),
        soup.find("strong", string=re.compile(r"\d+.*zł")),
    ]
    # Szukaj ceny w tekście strony
    price_match = re.search(r'(\d[\d\s]*(?:[.,]\d{2})?)\s*zł', resp.text)
    if price_match:
        price_str = price_match.group(1).replace(" ", "").replace(",", ".")
        try:
            price = float(price_str)
        except ValueError:
            pass

    # Opis
    desc = ""
    # Sprzedajemy.pl ma opis po nagłówku "Szczegóły ogłoszenia"
    desc_candidates = soup.find_all("div", class_=re.compile(r"desc|opis|content", re.I))
    if desc_candidates:
        desc = "\n".join(d.get_text(strip=True) for d in desc_candidates[:3])
    if not desc:
        # Fallback: zbierz tekst z body
        body_text = soup.get_text(separator="\n", strip=True)
        # Wyciągnij sekcję opisu
        for keyword in ["Polecam", "Sprzedam", "Oferuję", "Zapraszam", "Stan:"]:
            idx = body_text.find(keyword)
            if idx != -1:
                desc = body_text[idx : idx + 500]
                break
        if not desc:
            desc = body_text[:500]

    # Lokalizacja
    location = ""
    loc_match = re.search(r'(Bielsko-Biała|Katowice|Kraków|Warszawa|[\w\s-]+),\s*([\w]+kie)', resp.text)
    if loc_match:
        location = loc_match.group(0)

    # Stan
    condition = ""
    if "nowe" in resp.text.lower():
        condition = "nowe"
    elif "używane" in resp.text.lower():
        condition = "używane"

    # Sprzedawca
    seller = ""
    seller_match = re.search(r'class="[^"]*user[^"]*"[^>]*>([^<]+)', resp.text)

    # Obrazki
    images = [img.get("src", "") for img in soup.find_all("img") if "thumbs" in str(img.get("src", ""))]

    return Offer(
        url=url,
        title=title,
        price=price,
        description=desc[:1000],
        location=location,
        platform="sprzedajemy.pl",
        seller=seller,
        condition=condition,
        images=images[:5],
        scraped_at=datetime.now().isoformat(),
    )


async def scrape_gratka(url: str) -> Optional[Offer]:
    """Scraper dla Gratka.pl."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

    price = 0.0
    price_match = re.search(r'(\d[\d\s]*(?:[.,]\d{2})?)\s*zł', resp.text)
    if price_match:
        price_str = price_match.group(1).replace(" ", "").replace(",", ".")
        try:
            price = float(price_str)
        except ValueError:
            pass

    body_text = soup.get_text(separator="\n", strip=True)

    return Offer(
        url=url,
        title=title,
        price=price,
        description=body_text[:1000],
        location="",
        platform="gratka.pl",
        scraped_at=datetime.now().isoformat(),
    )


async def scrape_olx(url: str) -> Optional[Offer]:
    """OLX jest trudniejszy (JS-heavy), ale próbujemy wyciągnąć co się da z HTML."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

    # OLX renderuje cenę w JSON-LD
    price = 0.0
    jsonld = soup.find("script", type="application/ld+json")
    if jsonld:
        try:
            data = json.loads(jsonld.string)
            if isinstance(data, dict) and "offers" in data:
                price = float(data["offers"].get("price", 0))
            elif isinstance(data, dict) and "price" in str(data):
                price_match = re.search(r'"price"\s*:\s*"?(\d+(?:\.\d+)?)', jsonld.string)
                if price_match:
                    price = float(price_match.group(1))
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    if price == 0:
        price_match = re.search(r'(\d[\d\s]*(?:[.,]\d{2})?)\s*zł', resp.text)
        if price_match:
            try:
                price = float(price_match.group(1).replace(" ", "").replace(",", "."))
            except ValueError:
                pass

    desc = soup.get_text(separator="\n", strip=True)[:1000]

    return Offer(
        url=url,
        title=title,
        price=price,
        description=desc,
        location="",
        platform="olx.pl",
        scraped_at=datetime.now().isoformat(),
    )


async def scrape_allegro(url: str) -> Optional[Offer]:
    """Allegro jest najtrudniejszy — wyciągamy co się da."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

    price = 0.0
    price_match = re.search(r'(\d[\d\s]*(?:[.,]\d{2})?)\s*zł', resp.text)
    if price_match:
        try:
            price = float(price_match.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            pass

    return Offer(
        url=url,
        title=title,
        price=price,
        description=soup.get_text(separator="\n", strip=True)[:1000],
        location="",
        platform="allegro.pl",
        scraped_at=datetime.now().isoformat(),
    )


async def scrape_vinted(url: str) -> Optional[Offer]:
    """Vinted jest JS-heavy, ale próbujemy."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

    price = 0.0
    price_match = re.search(r'(\d[\d\s]*(?:[.,]\d{2})?)\s*zł', resp.text)
    if price_match:
        try:
            price = float(price_match.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            pass

    return Offer(
        url=url,
        title=title,
        price=price,
        description=soup.get_text(separator="\n", strip=True)[:1000],
        location="",
        platform="vinted.pl",
        scraped_at=datetime.now().isoformat(),
    )


async def scrape_generic(url: str) -> Optional[Offer]:
    """Generyczny scraper — próbuje wyciągnąć podstawowe info."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url

    price = 0.0
    price_match = re.search(r'(\d[\d\s]*(?:[.,]\d{2})?)\s*zł', resp.text)
    if price_match:
        try:
            price = float(price_match.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            pass

    return Offer(
        url=url,
        title=title,
        price=price,
        description=soup.get_text(separator="\n", strip=True)[:1000],
        location="",
        platform="other",
        scraped_at=datetime.now().isoformat(),
    )


# ── SEARCH SCRAPERS (monitoring nowych ofert) ────────────────────────────────

async def search_sprzedajemy(keyword: str, max_price: int = MAX_PRICE) -> list[Offer]:
    """Szukaj ofert na Sprzedajemy.pl po słowie kluczowym."""
    search_url = f"https://sprzedajemy.pl/szukaj?inp_text={keyword.replace(' ', '+')}"
    offers = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            resp = await client.get(search_url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Sprzedajemy.pl listing items
        items = soup.find_all("a", href=re.compile(r"/.*-nr\d+"))
        seen_urls = set()

        for item in items[:20]:  # max 20 wyników
            href = item.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            full_url = f"https://sprzedajemy.pl{href}" if href.startswith("/") else href

            title = item.get_text(strip=True)[:100]
            if not title or len(title) < 3:
                continue

            # Próbuj wyciągnąć cenę z tekstu
            price = 0.0
            price_match = re.search(r'(\d[\d\s]*)\s*zł', item.get_text())
            if price_match:
                try:
                    price = float(price_match.group(1).replace(" ", ""))
                except ValueError:
                    pass

            if 0 < price <= max_price or price == 0:
                offers.append(Offer(
                    url=full_url,
                    title=title,
                    price=price,
                    description="",
                    location="",
                    platform="sprzedajemy.pl",
                    scraped_at=datetime.now().isoformat(),
                ))

    except Exception as e:
        logger.error(f"Search error for '{keyword}' on Sprzedajemy: {e}")

    return offers


async def search_gratka(keyword: str, max_price: int = MAX_PRICE) -> list[Offer]:
    """Szukaj ofert na Gratka.pl."""
    search_url = f"https://gratka.pl/szukaj?q={keyword.replace(' ', '+')}"
    offers = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            resp = await client.get(search_url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"gratka\.pl/.*\d"))

        seen = set()
        for link in links[:20]:
            href = link.get("href", "")
            if href in seen or not href:
                continue
            seen.add(href)

            title = link.get_text(strip=True)[:100]
            if len(title) < 3:
                continue

            offers.append(Offer(
                url=href if href.startswith("http") else f"https://gratka.pl{href}",
                title=title,
                price=0,
                description="",
                location="",
                platform="gratka.pl",
                scraped_at=datetime.now().isoformat(),
            ))

    except Exception as e:
        logger.error(f"Search error for '{keyword}' on Gratka: {e}")

    return offers


# ── AI ANALYSIS ───────────────────────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = """Jesteś ekspertem od wyceny antyków, kolekcji i militariów na polskim rynku wtórnym.
Twoje zadanie: przeanalizować ofertę i dać rekomendację kupna/odrzucenia.

KONTEKST UŻYTKOWNIKA:
- Profesjonalny reseller z Katowic, specjalizacja: komiksy PRL, porcelana, zegarki vintage, broń biała, malarstwo, książki kolekcjonerskie
- Max cena zakupu: 550 zł/szt
- Min wymagana marża: 200%
- Odbiór osobisty: max 2h w jedną stronę od Katowic
- Wysyłka: OK jeśli jest opcja

TWOJA ANALIZA MUSI ZAWIERAĆ:
1. **IDENTYFIKACJA** — Co to jest? Oryginał czy replika? Kluczowe cechy.
2. **RED FLAGS** — Co budzi podejrzenia (stan "nowe" na antykach, brak sygnatur, cena typowa dla replik, lakoniczny opis).
3. **WYCENA RYNKOWA** — Realistyczny zakres cen na Allegro/domach aukcyjnych dla ORYGINAŁU tego typu.
4. **KALKULACJA** — Cena zakupu vs. realistyczna cena sprzedaży, marża %.
5. **WERDYKT** — Jeden z: 🟢 KUP (marża 200%+, pewny oryginał), 🟡 NEGOCJUJ (potencjał ale za drogo), 🟠 ZBADAJ (trzeba zobaczyć osobiście), ❌ OMIŃ (replika/za drogo/brak marży).

Odpowiadaj zwięźle, maksymalnie 300 słów. Po polsku."""


async def analyze_offer(offer: Offer) -> str:
    """Wyślij ofertę do Claude API i dostań analizę."""
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    user_message = f"""Przeanalizuj tę ofertę:

TYTUŁ: {offer.title}
CENA: {offer.price} zł
STAN: {offer.condition or 'nie podano'}
PLATFORMA: {offer.platform}
LOKALIZACJA: {offer.location}
SPRZEDAWCA: {offer.seller}
OPIS: {offer.description}
URL: {offer.url}
LICZBA ZDJĘĆ: {len(offer.images)}"""

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return f"❌ Błąd analizy AI: {e}"


def parse_verdict(analysis: str) -> str:
    """Wyciągnij werdykt z analizy AI."""
    if "🟢" in analysis or "KUP" in analysis.upper():
        return "BUY"
    elif "🟡" in analysis or "NEGOCJUJ" in analysis.upper():
        return "NEGOTIATE"
    elif "🟠" in analysis or "ZBADAJ" in analysis.upper():
        return "INVESTIGATE"
    else:
        return "SKIP"


# ── PERSISTENCE (prosty JSON) ────────────────────────────────────────────────

DATA_FILE = "okazje_data.json"
KEYWORDS_FILE = "keywords.json"


def load_seen_urls() -> set:
    """Załaduj URLe które już widzieliśmy (żeby nie alertować dwa razy)."""
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("seen_urls", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen_url(url: str):
    """Zapisz URL jako widziany."""
    seen = load_seen_urls()
    seen.add(url)
    # Trzymaj max 5000 URLi
    if len(seen) > 5000:
        seen = set(list(seen)[-3000:])
    with open(DATA_FILE, "w") as f:
        json.dump({"seen_urls": list(seen)}, f)


def load_keywords() -> list[str]:
    try:
        with open(KEYWORDS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_KEYWORDS.copy()


def save_keywords(keywords: list[str]):
    with open(KEYWORDS_FILE, "w") as f:
        json.dump(keywords, f, ensure_ascii=False, indent=2)


# ── TELEGRAM HANDLERS ────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Komenda /start."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🔍 **OKAZJE BOT** — Twój skaner kolekcjonerski\n\n"
        f"📋 **Komendy:**\n"
        f"• Wklej link → instant analiza AI\n"
        f"• /keywords — pokaż/edytuj słowa kluczowe\n"
        f"• /add <słowo> — dodaj słowo kluczowe\n"
        f"• /remove <słowo> — usuń słowo kluczowe\n"
        f"• /scan — uruchom skan ręcznie\n"
        f"• /status — status bota\n"
        f"• /help — pomoc\n\n"
        f"🆔 Twój Chat ID: `{chat_id}`\n"
        f"_(wklej do zmiennej CHAT_ID w .env)_",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 **Jak używać:**\n\n"
        "**1. Analiza linku** — wklej link z OLX/Vinted/Allegro/Sprzedajemy/Gratka\n"
        "Bot pobierze ofertę, przeanalizuje AI i da Ci werdykt.\n\n"
        "**2. Można wkleić wiele linków naraz** — każdy w osobnej linii.\n\n"
        "**3. Auto-monitoring** — bot co 30 min skanuje Sprzedajemy.pl i Gratka.pl "
        "po Twoich słowach kluczowych i wysyła alerty o nowych ofertach.\n\n"
        "**4. Słowa kluczowe** — /keywords, /add, /remove\n\n"
        "**Werdykty:**\n"
        "🟢 KUP — marża 200%+, pewny deal\n"
        "🟡 NEGOCJUJ — potencjał, ale trzeba zbić cenę\n"
        "🟠 ZBADAJ — obejrzyj osobiście\n"
        "❌ OMIŃ — replika / za drogo / brak marży",
        parse_mode="Markdown",
    )


async def cmd_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw = load_keywords()
    text = "🔑 **Słowa kluczowe do monitoringu:**\n\n"
    for i, k in enumerate(kw, 1):
        text += f"{i}. {k}\n"
    text += f"\n📝 /add <słowo> — dodaj\n📝 /remove <numer lub słowo> — usuń"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Użycie: /add <słowo kluczowe>")
        return
    keyword = " ".join(context.args)
    kw = load_keywords()
    if keyword in kw:
        await update.message.reply_text(f"'{keyword}' już istnieje na liście.")
        return
    kw.append(keyword)
    save_keywords(kw)
    await update.message.reply_text(f"✅ Dodano: **{keyword}**", parse_mode="Markdown")


async def cmd_remove_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Użycie: /remove <numer lub słowo kluczowe>")
        return
    arg = " ".join(context.args)
    kw = load_keywords()

    # Próbuj jako numer
    try:
        idx = int(arg) - 1
        if 0 <= idx < len(kw):
            removed = kw.pop(idx)
            save_keywords(kw)
            await update.message.reply_text(f"✅ Usunięto: **{removed}**", parse_mode="Markdown")
            return
    except ValueError:
        pass

    # Próbuj jako tekst
    if arg in kw:
        kw.remove(arg)
        save_keywords(kw)
        await update.message.reply_text(f"✅ Usunięto: **{arg}**", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Nie znaleziono '{arg}' na liście.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw = load_keywords()
    seen = load_seen_urls()
    await update.message.reply_text(
        f"📊 **Status bota:**\n"
        f"• Słowa kluczowe: {len(kw)}\n"
        f"• Widziane oferty: {len(seen)}\n"
        f"• Interwał skanowania: {SCAN_INTERVAL_MINUTES} min\n"
        f"• Max cena: {MAX_PRICE} zł\n"
        f"• Min marża: {MIN_MARGIN_PERCENT}%\n"
        f"• Platformy monitorowane: Sprzedajemy.pl, Gratka.pl\n"
        f"• Platformy ręczne: OLX, Vinted, Allegro, eBay",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ręczne uruchomienie skanu."""
    await update.message.reply_text("🔄 Uruchamiam skan... to może chwilę potrwać.")
    found = await run_scan(context.bot, str(update.effective_chat.id))
    if found == 0:
        await update.message.reply_text("Brak nowych ofert spełniających kryteria.")


async def handle_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler dla wklejonych linków — główna funkcja bota."""
    text = update.message.text or ""

    # Wyciągnij wszystkie URLe
    urls = re.findall(r'https?://\S+', text)

    if not urls:
        await update.message.reply_text(
            "Nie znalazłem linku. Wklej link do oferty z OLX/Vinted/Allegro/Sprzedajemy/Gratka."
        )
        return

    for url in urls:
        # Clean URL
        url = url.rstrip(".,;:!?)")

        await update.message.reply_text(f"🔍 Pobieram: {url[:60]}...")

        offer = await scrape_url(url)

        if not offer:
            await update.message.reply_text(
                f"❌ Nie udało się pobrać oferty z:\n{url}\n\n"
                f"Możesz wkleić opis ręcznie — przeanalizuję go."
            )
            continue

        # Podsumowanie scrape'a
        await update.message.reply_text(
            f"📦 **{offer.title}**\n"
            f"💰 Cena: {offer.price} zł\n"
            f"📍 {offer.location or 'brak lokalizacji'}\n"
            f"📄 Stan: {offer.condition or 'nie podano'}\n\n"
            f"🤖 Analizuję z AI...",
            parse_mode="Markdown",
        )

        # AI analysis
        analysis = await analyze_offer(offer)
        offer.analysis = analysis
        offer.verdict = parse_verdict(analysis)

        # Zapisz jako widziane
        save_seen_url(url)

        # Wyślij analizę
        verdict_emoji = {
            "BUY": "🟢", "NEGOTIATE": "🟡",
            "INVESTIGATE": "🟠", "SKIP": "❌"
        }
        emoji = verdict_emoji.get(offer.verdict, "❓")

        await update.message.reply_text(
            f"{emoji} **ANALIZA: {offer.title[:50]}**\n\n{analysis}",
            parse_mode="Markdown",
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler dla tekstu bez linków — traktuj jako ręczny opis oferty."""
    text = update.message.text or ""

    if len(text) < 20:
        await update.message.reply_text(
            "Wklej link do oferty lub opisz przedmiot (min. 20 znaków) do analizy."
        )
        return

    # Traktuj jako ręczny opis
    offer = Offer(
        url="ręczny opis",
        title=text[:50],
        price=0,
        description=text,
        location="",
        platform="manual",
        scraped_at=datetime.now().isoformat(),
    )

    await update.message.reply_text("🤖 Analizuję opis z AI...")
    analysis = await analyze_offer(offer)

    await update.message.reply_text(
        f"📋 **ANALIZA OPISU:**\n\n{analysis}",
        parse_mode="Markdown",
    )


# ── AUTO-SCAN JOB ─────────────────────────────────────────────────────────────

async def run_scan(bot: Bot, chat_id: str) -> int:
    """Skanuj Sprzedajemy i Gratka po słowach kluczowych."""
    if not chat_id:
        logger.warning("No CHAT_ID set, skipping auto-scan alerts.")
        return 0

    keywords = load_keywords()
    seen = load_seen_urls()
    new_offers = []

    for keyword in keywords:
        # Sprzedajemy
        try:
            offers = await search_sprzedajemy(keyword)
            for o in offers:
                if o.url not in seen and o.price <= MAX_PRICE:
                    new_offers.append(o)
                    save_seen_url(o.url)
        except Exception as e:
            logger.error(f"Scan error Sprzedajemy '{keyword}': {e}")

        # Gratka
        try:
            offers = await search_gratka(keyword)
            for o in offers:
                if o.url not in seen:
                    new_offers.append(o)
                    save_seen_url(o.url)
        except Exception as e:
            logger.error(f"Scan error Gratka '{keyword}': {e}")

        # Rate limiting — nie bombarduj serwerów
        await asyncio.sleep(2)

    if not new_offers:
        logger.info(f"Scan complete: 0 new offers.")
        return 0

    # Ogranicz do 10 najciekawszych (po cenie — niższe = ciekawsze)
    new_offers.sort(key=lambda o: o.price if o.price > 0 else 9999)
    top_offers = new_offers[:10]

    # Wyślij alert
    alert_text = f"🔔 **NOWE OFERTY** ({len(new_offers)} znalezionych)\n\n"
    for i, o in enumerate(top_offers, 1):
        alert_text += (
            f"**{i}. {o.title[:50]}**\n"
            f"💰 {o.price} zł | 📍 {o.platform}\n"
            f"🔗 {o.url}\n\n"
        )

    if len(new_offers) > 10:
        alert_text += f"_...i {len(new_offers) - 10} więcej_\n"

    alert_text += "\n💡 Wklej interesujący link, żeby dostać pełną analizę AI."

    try:
        await bot.send_message(chat_id=chat_id, text=alert_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")

    logger.info(f"Scan complete: {len(new_offers)} new offers, {len(top_offers)} sent.")
    return len(new_offers)


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    """Job do automatycznego skanowania."""
    if CHAT_ID:
        await run_scan(context.bot, CHAT_ID)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    """Uruchom bota."""
    logger.info("Starting Okazje Bot...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("keywords", cmd_keywords))
    app.add_handler(CommandHandler("add", cmd_add_keyword))
    app.add_handler(CommandHandler("remove", cmd_remove_keyword))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))

    # Link handler (priority)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'https?://'), handle_links))

    # Text handler (fallback)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Scheduled scan
    if CHAT_ID:
        job_queue = app.job_queue
        job_queue.run_repeating(
            scheduled_scan,
            interval=SCAN_INTERVAL_MINUTES * 60,
            first=60,  # Pierwszy skan po 1 minucie
        )
        logger.info(f"Auto-scan enabled every {SCAN_INTERVAL_MINUTES} min for chat {CHAT_ID}")
    else:
        logger.warning("CHAT_ID not set — auto-scan alerts disabled. Use /start to get your ID.")

    # Run
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
