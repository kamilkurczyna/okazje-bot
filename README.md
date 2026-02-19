# 🔍 OKAZJE BOT — Telegram Bot do Analizy Kolekcjonerskich Okazji

Bot Telegramowy do analizy ofert z polskich platform (OLX, Vinted, Allegro, Sprzedajemy, Gratka).
Wklej link → dostaniesz analizę AI: oryginał czy replika, wycena rynkowa, werdykt kupna.

## ✨ Funkcje

- **Analiza linków** — wklej link z dowolnej platformy, bot pobierze ofertę i da AI-ową wycenę
- **Wiele linków naraz** — wklej kilka linków, każdy zostanie przeanalizowany osobno
- **Auto-monitoring** — co 30 min skanuje Sprzedajemy.pl i Gratka.pl po Twoich słowach kluczowych
- **Alerty Telegram** — nowe oferty lądują prosto na Twoim Telegramie
- **Zarządzanie keywords** — dodawaj/usuwaj słowa kluczowe bez restartu
- **Ręczne opisy** — możesz wkleić opis przedmiotu tekstem (bez linku), a bot go przeanalizuje

## 🚀 Setup krok po kroku

### Krok 1: Stwórz Telegram Bota

1. Otwórz Telegram i napisz do **@BotFather**
2. Wyślij `/newbot`
3. Podaj nazwę bota (np. "Okazje Scanner")
4. Podaj username (np. `okazje_scanner_bot`)
5. **Skopiuj token** — wygląda tak: `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### Krok 2: Załóż konto API Anthropic

1. Wejdź na **https://console.anthropic.com/**
2. Załóż konto (email + karta płatnicza)
3. Wejdź w **API Keys** → **Create Key**
4. **Skopiuj klucz** — wygląda tak: `sk-ant-api03-xxxxxx...`
5. Doładuj konto — $5 na start wystarczy na ~200-500 analiz

> 💡 Koszt jednej analizy to ~$0.01-0.03 (Sonnet). Przy 50 analizach dziennie = ~$1/dzień max.

### Krok 3: Deploy na Railway (najprostszy sposób)

1. Załóż konto na **https://railway.app/** (możesz przez GitHub)
2. Wrzuć ten projekt na GitHub:
   ```bash
   cd okazje-bot
   git init
   git add .
   git commit -m "Initial commit"
   # Stwórz repo na GitHub, potem:
   git remote add origin https://github.com/TWÓJ_USER/okazje-bot.git
   git push -u origin main
   ```
3. W Railway: **New Project → Deploy from GitHub repo**
4. Wybierz repo `okazje-bot`
5. Dodaj zmienne środowiskowe (Settings → Variables):
   - `TELEGRAM_TOKEN` = token z BotFather
   - `ANTHROPIC_API_KEY` = klucz z Anthropic
   - `CHAT_ID` = (na razie puste, uzupełnisz po kroku 4)
6. Railway automatycznie zbuduje i uruchomi bota

### Krok 4: Pobierz swój Chat ID

1. Otwórz swojego bota w Telegramie
2. Wyślij `/start`
3. Bot odpowie Twoim **Chat ID** (np. `123456789`)
4. Wróć do Railway → Variables → ustaw `CHAT_ID` = Twój ID
5. Railway automatycznie zrestartuje bota — auto-alerty zaczną działać

### Alternatywa: Uruchom lokalnie

```bash
cd okazje-bot
pip install -r requirements.txt

# Skopiuj i wypełnij .env
cp .env.example .env
# Edytuj .env — wklej swoje tokeny

# Na Linux/Mac:
export $(cat .env | xargs)
python bot.py

# Na Windows (PowerShell):
Get-Content .env | ForEach-Object { if ($_ -match '^([^#].+?)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
python bot.py
```

## 📋 Komendy bota

| Komenda | Opis |
|---------|------|
| `/start` | Powitanie + Twój Chat ID |
| `/help` | Instrukcja użytkowania |
| `/keywords` | Lista słów kluczowych do monitoringu |
| `/add <słowo>` | Dodaj słowo kluczowe |
| `/remove <nr lub słowo>` | Usuń słowo kluczowe |
| `/scan` | Uruchom skan ręcznie |
| `/status` | Status bota |
| Wklej link | Analiza AI oferty |
| Wklej tekst | Analiza opisu bez linku |

## 🎯 Werdykty AI

| Emoji | Werdykt | Znaczenie |
|-------|---------|-----------|
| 🟢 | KUP | Marża 200%+, pewny oryginał |
| 🟡 | NEGOCJUJ | Potencjał, ale za droga cena |
| 🟠 | ZBADAJ | Wymaga osobistej weryfikacji |
| ❌ | OMIŃ | Replika / za drogo / brak marży |

## ⚙️ Konfiguracja

Zmienne w `.env`:

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `SCAN_INTERVAL` | 30 | Interwał auto-skanu (minuty) |
| `MAX_PRICE` | 550 | Max cena zakupu (PLN) |
| `MIN_MARGIN` | 200 | Min wymagana marża (%) |

## 🔧 Znane ograniczenia

- **OLX, Vinted, Allegro** — scraping z tych platform jest utrudniony (JS rendering, anti-bot). Bot wyciąga co się da z HTML, ale może nie złapać wszystkich danych. Najlepszy wynik daje Sprzedajemy.pl i Gratka.pl.
- **Analiza AI** nie jest nieomylna — traktuj ją jako pierwszą filtrację, nie jako ostateczny werdykt. Zawsze weryfikuj osobiście przed zakupem.
- **Auto-monitoring** działa tylko na Sprzedajemy.pl i Gratka.pl. Dla OLX/Vinted/Allegro wklejaj linki ręcznie.

## 📈 Roadmap (przyszłe wersje)

- [ ] Dashboard webowy z historią analiz
- [ ] Playwright-based scraping (Vinted, OLX)
- [ ] Integracja z Allegro API (oficjalne)
- [ ] Analiza zdjęć (rozpoznawanie sygnatur, stanów)
- [ ] Baza danych cen transakcyjnych
- [ ] Multi-user support

## 💰 Koszty miesięczne

| Usługa | Koszt |
|--------|-------|
| Railway hosting | $0 (darmowy tier, 500h/mies) |
| Anthropic API | ~$5-30/mies (zależy od użycia) |
| **Razem** | **~$5-30/mies** |
