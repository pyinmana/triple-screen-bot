# Triple Screen Trading Bot

Dr. Alexander Elder's Triple Screen strategy အပေါ် အခြေခံတဲ့ automated Forex trading signal bot။

## Strategy Logic

| Screen | Timeframe | Indicator | Role |
|--------|-----------|-----------|------|
| Screen 1 | 1H | MACD Line | Tide — `> 0` = Bullish, `< 0` = Bearish |
| Screen 2 | 15min | RSI | Wave — `≤ 40` = Buy pullback, `≥ 60` = Sell pullback |
| Screen 3 | 5min | EMA20 | Entry — Price closes above/below EMA20 |

## Setup

### 1. Clone & Install

```bash
git clone https://github.com/your-username/triple-screen-bot.git
cd triple-screen-bot
pip install requests pandas numpy python-dotenv
```

### 2. Configure Credentials

```bash
cp .env.example .env
```

`.env` ဖိုင်ကို ဖွင့်ပြီး credentials တွေ ဖြည့်ပါ:

```env
TELEGRAM_BOT_TOKEN=8704593044:AAH...
TELEGRAM_CHAT_ID=-1003871757831
TWELVEDATA_API_KEYS=key1,key2,key3,key4,key5
```

> ⚠️ `.env` ကို Git မှာ **မတင်ရ**။ `.gitignore` မှာ already block လုပ်ထားပြီ။

### 3. Run

```bash
# Bot run
python tps_v2.py

# Open trades status
python tps_v2.py status

# Backtest analysis
python tps_v2.py backtest

# Export data
python tps_v2.py export

# Manual trade close
python tps_v2.py close <TRADE_ID>
```

### VPS (PM2)

```bash
pm2 start tps_v2.py --name "triple-screen" --interpreter python3
pm2 save
pm2 startup
```

## API Keys

- **Telegram Bot**: [@BotFather](https://t.me/BotFather) မှ bot create လုပ်ပြီး token ယူပါ
- **Twelve Data**: [twelvedata.com](https://twelvedata.com) မှ free/paid API keys ယူပါ (rate limit ကျော်ဖို့ multiple keys သုံးပါ)

## Disclaimer

ဒီ bot ကို educational purpose အတွက်သာ ရည်ရွယ်သည်။ Real money trading မတိုင်မီ paper trading နဲ့ အရင် test လုပ်ပါ။
