"""
╔══════════════════════════════════════════════════════════════════╗
║              TRIPLE SCREEN TRADING SYSTEM BOT                  ║
║              Strategy: Dr. Alexander Elder                     ║
║              With Trade Logging & Auto TP/SL Monitor           ║
║              Version: 5.0 - Simplified Logic                   ║
╚══════════════════════════════════════════════════════════════════╝

Screen 1 (1H)   : MACD Line > 0 = BULLISH tide, < 0 = BEARISH tide
Screen 2 (15min): RSI pullback — BUY if RSI <= 40, SELL if RSI >= 60
Screen 3 (5min) : EMA20 breakout — price closes above/below EMA20

Requirements: pip install requests pandas numpy

Commands:
  python tps_v2.py                  → Run bot
  python tps_v2.py backtest         → Backtest analysis
  python tps_v2.py export           → Export backtest data
  python tps_v2.py status           → Check open trades
  python tps_v2.py close <TRADE_ID> → Manual close trade
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import csv
import os
import sys
import threading
from typing import List, Dict, Tuple, Optional
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============================================
# ⚙️ CONFIGURATION
# ============================================

# 📱 Telegram Settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# 🔑 Twelve Data API Keys (comma-separated in .env)
_raw_keys = os.getenv("TWELVEDATA_API_KEYS", "")
TWELVEDATA_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]

# 📊 Trading Pairs
MONITOR_PAIRS = [
    "EUR/USD",
]

# ============================================
# 🎯 TRIPLE SCREEN SETTINGS
# ============================================

SCREEN1_TIMEFRAME = "1h"
SCREEN2_TIMEFRAME = "15min"
SCREEN3_TIMEFRAME = "5min"

# Screen 1 — MACD
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9

# Screen 2 — RSI
RSI_PERIOD     = 14
RSI_BUY_LEVEL  = 40   # RSI <= 40 → pullback in BULLISH tide
RSI_SELL_LEVEL = 60   # RSI >= 60 → pullback in BEARISH tide

# Screen 3 — EMA20 Breakout
EMA_TRIGGER    = 20   # EMA period for entry trigger
BREAKOUT_PERIOD = 20  # N-bar lookback (also used as SL reference)

# Risk / SL / TP
ATR_PERIOD         = 14
ATR_SL_MULTIPLIER  = 1.5
ATR_TP_MULTIPLIER  = 3.0   # 1:2 R:R (SL×1.5, TP×3.0 → ratio 2)
MIN_RISK_REWARD_RATIO = 2.0

# ⏱️ Schedule
ANALYSIS_INTERVAL_MINUTES = 3
DELAY_BETWEEN_PAIRS   = 8
DELAY_BETWEEN_SCREENS = 2

# ============================================
# 🔍 TRADE MONITOR SETTINGS
# ============================================

ENABLE_TRADE_MONITOR       = True
TRADE_MONITOR_INTERVAL_MINUTES = 1
SEND_TP_SL_ALERT           = True

# ============================================
# 📱 TELEGRAM ALERT MODE
# ============================================

TELEGRAM_ALERT_MODE = "SIGNALS_ONLY"  # "SIGNALS_ONLY" or "ALL"

# ============================================
# 📁 LOGGING SETTINGS
# ============================================

ENABLE_TRADE_LOGGING  = True
TRADE_LOG_DIR         = "trade_logs"
TRADE_HISTORY_FILE    = "trade_history.csv"
SIGNAL_LOG_FILE       = "signal_log.csv"
SETUP_LOG_FILE        = "setup_log.csv"
PERFORMANCE_LOG_FILE  = "performance.csv"

ENABLE_CONSOLE_LOG = True


# ============================================
# 🔑 API KEY MANAGER
# ============================================

class APIKeyManager:
    def __init__(self, api_keys, daily_limit=800, warning_threshold=790):
        self.daily_limit       = daily_limit
        self.warning_threshold = warning_threshold
        self.lock              = threading.Lock()

        self._keys       = list(api_keys)
        self._current_idx = 0
        self.key_usage   = {}
        for key in self._keys:
            self.key_usage[key] = {
                'count': 0,
                'last_reset': datetime.now().date(),
                'is_active': True,
                'errors': 0
            }

        self._log(f"✅ API Key Manager: {len(self._keys)} keys loaded")

    @property
    def current_key(self):
        if not self._keys:
            return None
        return self._keys[self._current_idx]

    def _log(self, msg):
        if ENABLE_CONSOLE_LOG:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _reset_daily(self, key):
        today = datetime.now().date()
        usage = self.key_usage[key]
        if usage['last_reset'] < today:
            usage['count']      = 0
            usage['last_reset'] = today
            usage['errors']     = 0
            usage['is_active']  = True

    def _switch_key_locked(self):
        n = len(self._keys)
        for i in range(1, n + 1):
            idx = (self._current_idx + i) % n
            key = self._keys[idx]
            self._reset_daily(key)
            usage = self.key_usage[key]
            if usage['is_active'] and usage['count'] < self.daily_limit:
                self._current_idx = idx
                self._log(f"🔄 Switched to key #{idx+1} {key[:10]}... ({usage['count']}/{self.daily_limit})")
                return key
        return None

    def get_key(self):
        with self.lock:
            if not self._keys:
                return None
            key = self._keys[self._current_idx]
            self._reset_daily(key)
            usage = self.key_usage[key]
            if usage['is_active'] and usage['count'] < self.daily_limit:
                return key
            new_key = self._switch_key_locked()
            if new_key:
                return new_key
            exhausted = True

        if exhausted:
            self._log("⏳ All keys exhausted! Waiting 60s...")
            time.sleep(60)
            with self.lock:
                best_idx = min(
                    range(len(self._keys)),
                    key=lambda i: self.key_usage[self._keys[i]]['count']
                )
                self._current_idx = best_idx
                for k in self._keys:
                    self.key_usage[k]['errors'] = 0
                return self._keys[self._current_idx]

    def mark_success(self, key=None):
        with self.lock:
            k = key or (self._keys[self._current_idx] if self._keys else None)
            if k and k in self.key_usage:
                self._reset_daily(k)
                self.key_usage[k]['count'] += 1
                count = self.key_usage[k]['count']
                if count >= self.warning_threshold:
                    self._log(f"⚠️ Key {k[:10]}... near limit: {count}/{self.daily_limit}")

    def mark_error(self, key=None):
        need_switch = False
        with self.lock:
            k = key or (self._keys[self._current_idx] if self._keys else None)
            if k and k in self.key_usage:
                self.key_usage[k]['errors'] += 1
                if self.key_usage[k]['errors'] >= 3:
                    self.key_usage[k]['is_active'] = False
                    self._log(f"🚫 Key {k[:10]}... deactivated (too many errors)")
                    new_key = self._switch_key_locked()
                    if not new_key:
                        need_switch = True

        if need_switch:
            self._log("⏳ All keys exhausted after error! Waiting 30s...")
            time.sleep(30)

    def add_key(self, new_key):
        with self.lock:
            if new_key in self.key_usage:
                self._log(f"⚠️ Key {new_key[:10]}... already exists")
                return False
            self._keys.append(new_key)
            self.key_usage[new_key] = {
                'count': 0,
                'last_reset': datetime.now().date(),
                'is_active': True,
                'errors': 0
            }
            self._log(f"➕ Key added: {new_key[:10]}... (total: {len(self._keys)})")
            return True

    def remove_key(self, key):
        with self.lock:
            if key not in self.key_usage:
                self._log(f"⚠️ Key {key[:10]}... not found")
                return False
            if len(self._keys) <= 1:
                self._log("⚠️ Cannot remove the only key")
                return False
            idx = self._keys.index(key)
            self._keys.pop(idx)
            del self.key_usage[key]
            if self._current_idx >= len(self._keys):
                self._current_idx = 0
            elif self._current_idx > idx:
                self._current_idx -= 1
            self._log(f"➖ Key removed: {key[:10]}... (total: {len(self._keys)})")
            return True

    def get_stats(self):
        with self.lock:
            stats = {}
            for i, key in enumerate(self._keys):
                self._reset_daily(key)
                marker = " ◀ current" if i == self._current_idx else ""
                stats[f"Key#{i+1} {key[:10]}...{marker}"] = {
                    'count':  self.key_usage[key]['count'],
                    'limit':  self.daily_limit,
                    'active': self.key_usage[key]['is_active'],
                    'errors': self.key_usage[key]['errors']
                }
            return stats


# ============================================
# 🧮 TECHNICAL INDICATORS
# ============================================

class Indicators:
    @staticmethod
    def ema(series, period):
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def macd(series, fast=12, slow=26, signal=9):
        ema_fast    = series.ewm(span=fast, adjust=False).mean()
        ema_slow    = series.ewm(span=slow, adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line, signal_line

    @staticmethod
    def rsi(series, period=14):
        delta    = series.diff()
        gain     = delta.where(delta > 0, 0.0)
        loss     = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        rs       = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def atr(df, period=14):
        high_low   = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close  = abs(df['low']  - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()


# ============================================
# 📁 TRADE LOGGER
# ============================================

class TradeLogger:
    def __init__(self, log_dir="trade_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

        self.trade_history_path = self.log_dir / TRADE_HISTORY_FILE
        self.signal_log_path    = self.log_dir / SIGNAL_LOG_FILE
        self.setup_log_path     = self.log_dir / SETUP_LOG_FILE
        self.performance_path   = self.log_dir / PERFORMANCE_LOG_FILE

        self._init_files()
        self._log(f"📁 Trade Logger: {self.log_dir}")

    def _log(self, msg):
        if ENABLE_CONSOLE_LOG:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _init_files(self):
        if not self.trade_history_path.exists():
            with open(self.trade_history_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([
                    'trade_id', 'datetime', 'symbol', 'direction',
                    'screen1_tide', 'screen1_macd_line',
                    'screen2_wave', 'screen2_rsi',
                    'screen3_trigger', 'screen3_ema20',
                    'entry_price', 'stop_loss', 'take_profit',
                    'stop_loss_pips', 'take_profit_pips',
                    'risk_reward_ratio', 'atr_value',
                    'screen1_tf', 'screen2_tf', 'screen3_tf',
                    'breakout_level', 'confidence', 'trade_status',
                    'exit_price', 'exit_datetime',
                    'profit_loss_pips', 'profit_loss_pct',
                    'exit_reason', 'notes'
                ])

        if not self.signal_log_path.exists():
            with open(self.signal_log_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([
                    'signal_id', 'datetime', 'symbol',
                    'screen1_tide', 'screen1_macd_line',
                    'screen2_wave', 'screen2_rsi',
                    'screen3_trigger', 'screen3_ema20',
                    'current_price', 'entry_price',
                    'stop_loss', 'take_profit',
                    'risk_reward_ratio', 'is_triggered',
                    'atr_value', 'notes'
                ])

        if not self.setup_log_path.exists():
            with open(self.setup_log_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([
                    'setup_id', 'datetime', 'symbol',
                    'screen1_tide', 'screen1_macd_line',
                    'screen2_wave', 'screen2_rsi',
                    'current_price',
                    'eventually_triggered', 'time_to_trigger_minutes', 'notes'
                ])

        if not self.performance_path.exists():
            with open(self.performance_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([
                    'date', 'total_signals', 'triggered_trades',
                    'buy_trades', 'sell_trades', 'winning_trades',
                    'losing_trades', 'win_rate_pct', 'total_pips',
                    'avg_win_pips', 'avg_loss_pips', 'profit_factor',
                    'best_trade_pips', 'worst_trade_pips', 'avg_risk_reward', 'notes'
                ])

    def _generate_id(self, prefix, filepath):
        now   = datetime.now()
        today = now.strftime("%Y-%m-%d")
        count = 0
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if len(row) > 1 and row[1].startswith(today):
                        count += 1
        return f"{prefix}-{now.strftime('%Y%m%d')}-{count+1:04d}"

    def log_trade_entry(self, trade_data):
        if not ENABLE_TRADE_LOGGING:
            return None

        trade_id = self._generate_id(
            f"T-{trade_data.get('symbol', 'UNKNOWN').replace('/', '').replace('_', '')}",
            self.trade_history_path
        )

        entry    = trade_data.get('entry_price', 0)
        sl       = trade_data.get('stop_loss', 0)
        tp       = trade_data.get('take_profit', 0)
        sl_pips  = abs(entry - sl) * 10000 if entry and sl else 0
        tp_pips  = abs(tp - entry) * 10000 if entry and tp else 0
        rr       = tp_pips / sl_pips if sl_pips > 0 else 0

        row = [
            trade_id,
            trade_data.get('datetime', datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            trade_data.get('symbol', ''),
            trade_data.get('direction', ''),
            trade_data.get('screen1_tide', ''),
            f"{trade_data.get('screen1_macd_line', 0):.6f}",
            trade_data.get('screen2_wave', ''),
            f"{trade_data.get('screen2_rsi', 0):.1f}",
            trade_data.get('screen3_trigger', ''),
            f"{trade_data.get('screen3_ema20', 0):.5f}",
            f"{entry:.5f}" if entry else '',
            f"{sl:.5f}"    if sl    else '',
            f"{tp:.5f}"    if tp    else '',
            f"{sl_pips:.1f}",
            f"{tp_pips:.1f}",
            f"{rr:.2f}",
            f"{trade_data.get('atr_value', 0):.5f}",
            trade_data.get('screen1_tf', ''),
            trade_data.get('screen2_tf', ''),
            trade_data.get('screen3_tf', ''),
            f"{trade_data.get('breakout_level', 0):.5f}",
            trade_data.get('confidence', ''),
            'OPEN',
            '', '', '', '', '',
            trade_data.get('notes', '')
        ]

        with open(self.trade_history_path, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

        self._log(f"📝 Trade logged: {trade_id} | {trade_data.get('symbol')} {trade_data.get('direction')} @ {entry:.5f}")
        return trade_id

    def log_trade_exit(self, trade_id, exit_price, exit_datetime=None, exit_reason="Manual"):
        if not ENABLE_TRADE_LOGGING or not trade_id:
            return

        if exit_datetime is None:
            exit_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        rows    = []
        updated = False

        if self.trade_history_path.exists():
            with open(self.trade_history_path, 'r', encoding='utf-8') as f:
                reader  = csv.reader(f)
                headers = next(reader)
                rows.append(headers)

                for row in reader:
                    if row[0] == trade_id:
                        try:
                            entry_price = float(row[10]) if row[10] else 0
                            direction   = row[3]

                            if direction == 'BUY':
                                pips = (exit_price - entry_price) * 10000
                            elif direction == 'SELL':
                                pips = (entry_price - exit_price) * 10000
                            else:
                                pips = 0

                            pct = (pips / (entry_price * 10000)) * 100 if entry_price > 0 else 0

                            row[22] = 'CLOSED'
                            row[23] = f"{exit_price:.5f}"
                            row[24] = exit_datetime
                            row[25] = f"{pips:.1f}"
                            row[26] = f"{pct:.2f}"
                            row[27] = exit_reason

                            updated = True
                            self._log(f"✅ Trade closed: {trade_id} | P/L: {pips:.1f} pips | {exit_reason}")
                        except (IndexError, ValueError) as e:
                            self._log(f"⚠️ Error updating trade {trade_id}: {e}")
                    rows.append(row)

        if updated:
            with open(self.trade_history_path, 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerows(rows)

    def log_signal(self, signal_data):
        if not ENABLE_TRADE_LOGGING:
            return

        signal_id = self._generate_id("SIG", self.signal_log_path)

        row = [
            signal_id,
            signal_data.get('datetime', ''),
            signal_data.get('symbol', ''),
            signal_data.get('screen1_tide', ''),
            f"{signal_data.get('screen1_macd_line', 0):.6f}",
            signal_data.get('screen2_wave', ''),
            f"{signal_data.get('screen2_rsi', 0):.1f}",
            signal_data.get('screen3_trigger', ''),
            f"{signal_data.get('screen3_ema20', 0):.5f}",
            f"{signal_data.get('current_price', 0):.5f}",
            f"{signal_data.get('entry_price', 0):.5f}"   if signal_data.get('entry_price') else '',
            f"{signal_data.get('stop_loss', 0):.5f}"     if signal_data.get('stop_loss')   else '',
            f"{signal_data.get('take_profit', 0):.5f}"   if signal_data.get('take_profit') else '',
            f"{signal_data.get('risk_reward_ratio', 0):.2f}",
            str(signal_data.get('is_triggered', False)),
            f"{signal_data.get('atr_value', 0):.5f}",
            signal_data.get('notes', '')
        ]

        with open(self.signal_log_path, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

    def log_setup(self, setup_data):
        if not ENABLE_TRADE_LOGGING:
            return None

        setup_id = self._generate_id("SET", self.setup_log_path)

        row = [
            setup_id,
            setup_data.get('datetime', ''),
            setup_data.get('symbol', ''),
            setup_data.get('screen1_tide', ''),
            f"{setup_data.get('screen1_macd_line', 0):.6f}",
            setup_data.get('screen2_wave', ''),
            f"{setup_data.get('screen2_rsi', 0):.1f}",
            f"{setup_data.get('current_price', 0):.5f}",
            str(setup_data.get('eventually_triggered', False)),
            setup_data.get('time_to_trigger_minutes', ''),
            setup_data.get('notes', '')
        ]

        with open(self.setup_log_path, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)

        return setup_id

    def get_open_trades(self):
        open_trades = []
        if self.trade_history_path.exists():
            with open(self.trade_history_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('trade_status') == 'OPEN':
                        open_trades.append(row)
        return open_trades

    def get_trade_by_id(self, trade_id):
        if self.trade_history_path.exists():
            with open(self.trade_history_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('trade_id') == trade_id:
                        return row
        return None

    def get_trade_summary_today(self):
        today  = datetime.now().strftime("%Y-%m-%d")
        trades = []
        if self.trade_history_path.exists():
            with open(self.trade_history_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('datetime', '').startswith(today):
                        trades.append(row)

        open_trades   = [t for t in trades if t.get('trade_status') == 'OPEN']
        closed_trades = [t for t in trades if t.get('trade_status') == 'CLOSED']
        tp_hits       = [t for t in closed_trades if 'TP' in t.get('exit_reason', '')]
        sl_hits       = [t for t in closed_trades if 'SL' in t.get('exit_reason', '')]
        total_pips    = sum(float(t.get('profit_loss_pips', 0)) for t in closed_trades)
        winners       = sum(1 for t in closed_trades if float(t.get('profit_loss_pips', 0)) > 0)

        return {
            'open':          len(open_trades),
            'closed':        len(closed_trades),
            'tp_hits':       len(tp_hits),
            'sl_hits':       len(sl_hits),
            'winners':       winners,
            'total_pips':    total_pips,
            'win_rate':      winners/len(closed_trades)*100 if closed_trades else 0,
            'open_trades':   open_trades,
            'closed_trades': closed_trades
        }

    def generate_daily_performance(self, date=None):
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        trades = []
        if self.trade_history_path.exists():
            with open(self.trade_history_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('datetime', '').startswith(date) and row.get('trade_status') == 'CLOSED':
                        trades.append(row)

        if not trades:
            return

        total  = len(trades)
        buys   = sum(1 for t in trades if t.get('direction') == 'BUY')
        sells  = total - buys

        pips_list = [float(t.get('profit_loss_pips', 0)) for t in trades]
        winners   = [p for p in pips_list if p > 0]
        losers    = [p for p in pips_list if p <= 0]

        win_rate      = len(winners) / total * 100 if total > 0 else 0
        total_pips    = sum(pips_list)
        avg_win       = sum(winners) / len(winners) if winners else 0
        avg_loss      = sum(losers)  / len(losers)  if losers  else 0
        gross_profit  = sum(winners)
        gross_loss    = abs(sum(losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        best_trade    = max(pips_list) if pips_list else 0
        worst_trade   = min(pips_list) if pips_list else 0

        new_row = [
            date, total, total, buys, sells,
            len(winners), len(losers),
            f"{win_rate:.1f}", f"{total_pips:.1f}",
            f"{avg_win:.1f}", f"{avg_loss:.1f}",
            f"{profit_factor:.2f}",
            f"{best_trade:.1f}", f"{worst_trade:.1f}",
            '', f"Generated {datetime.now().strftime('%H:%M:%S')}"
        ]

        rows    = []
        updated = False

        if self.performance_path.exists():
            with open(self.performance_path, 'r', encoding='utf-8') as f:
                reader  = csv.reader(f)
                headers = next(reader)
                rows.append(headers)
                for row in reader:
                    if row[0] == date:
                        rows.append(new_row)
                        updated = True
                    else:
                        rows.append(row)

        if not updated:
            if not rows:
                rows.append([
                    'date', 'total_signals', 'triggered_trades',
                    'buy_trades', 'sell_trades', 'winning_trades',
                    'losing_trades', 'win_rate_pct', 'total_pips',
                    'avg_win_pips', 'avg_loss_pips', 'profit_factor',
                    'best_trade_pips', 'worst_trade_pips', 'avg_risk_reward', 'notes'
                ])
            rows.append(new_row)

        with open(self.performance_path, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerows(rows)

        self._log(f"📊 Daily Performance ({date}): Trades:{total} | Win:{win_rate:.1f}% | P/L:{total_pips:.1f}pips | PF:{profit_factor:.2f}")

    def export_for_backtesting(self, filename="backtest_data.csv"):
        export_path = self.log_dir / filename
        signals = []
        if self.signal_log_path.exists():
            with open(self.signal_log_path, 'r', encoding='utf-8') as f:
                reader  = csv.DictReader(f)
                signals = list(reader)

        if not signals:
            self._log("❌ No data to export")
            return None

        for i, signal in enumerate(signals):
            signal['row_id'] = str(i + 1)

        with open(export_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(signals[0].keys()))
            writer.writeheader()
            writer.writerows(signals)

        self._log(f"📁 Backtest data exported: {export_path} ({len(signals)} signals)")
        return export_path

    def print_summary(self):
        print("\n" + "=" * 50)
        print("📊 TRADE LOG SUMMARY")
        print("=" * 50)

        today   = datetime.now().strftime("%Y-%m-%d")
        summary = self.get_trade_summary_today()

        print(f"📅 Date: {today}")
        print(f"🔓 Open: {summary['open']} | ✅ Closed: {summary['closed']}")
        print(f"🎯 TP Hits: {summary['tp_hits']} | 🛑 SL Hits: {summary['sl_hits']}")
        print(f"💰 P/L Today: {summary['total_pips']:+.1f} pips")
        print(f"📊 Win Rate: {summary['win_rate']:.1f}%")
        print("=" * 50 + "\n")


# ============================================
# 🔍 TRADE MONITOR
# ============================================

class TradeMonitor:
    def __init__(self, bot_instance):
        self.bot    = bot_instance
        self.logger = bot_instance.logger
        self._log("📊 Trade Monitor initialized (TP/SL alerts → Telegram)")

    def _log(self, msg):
        if ENABLE_CONSOLE_LOG:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [Monitor] {msg}")

    def _get_current_price(self, symbol):
        try:
            endpoint    = f"{self.bot.base_url}/quote"
            current_key = self.bot.key_manager.get_key()
            if not current_key:
                return None
            params = {'symbol': symbol, 'apikey': current_key}
            resp   = requests.get(endpoint, params=params, timeout=5)

            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == 'error':
                    self.bot.key_manager.mark_error(current_key)
                    return None
                self.bot.key_manager.mark_success(current_key)
                if 'close' in data:
                    return float(data['close'])
                elif 'bid' in data and 'ask' in data:
                    return (float(data['bid']) + float(data['ask'])) / 2
            elif resp.status_code == 429:
                self.bot.key_manager.mark_error(current_key)
            return None
        except Exception as e:
            self._log(f"Price fetch error for {symbol}: {e}")
            return None

    def check_trade_status(self, trade):
        symbol      = trade.get('symbol', '')
        direction   = trade.get('direction', '')
        entry_price = float(trade.get('entry_price', 0))
        stop_loss   = float(trade.get('stop_loss', 0))
        take_profit = float(trade.get('take_profit', 0))

        if not all([symbol, entry_price, stop_loss, take_profit]):
            return None

        current_price = self._get_current_price(symbol)
        if current_price is None:
            return None

        tp_hit = sl_hit = False

        if direction == 'BUY':
            if current_price >= take_profit:
                tp_hit = True
            elif current_price <= stop_loss:
                sl_hit = True
        elif direction == 'SELL':
            if current_price <= take_profit:
                tp_hit = True
            elif current_price >= stop_loss:
                sl_hit = True

        if tp_hit:
            return 'TP_HIT', current_price, self._format_tp_message(trade, current_price)
        elif sl_hit:
            return 'SL_HIT', current_price, self._format_sl_message(trade, current_price)
        return None

    def _format_tp_message(self, trade, current_price):
        trade_id  = trade.get('trade_id', '')
        symbol    = trade.get('symbol', '')
        direction = trade.get('direction', '')
        entry     = float(trade.get('entry_price', 0))
        tp        = float(trade.get('take_profit', 0))
        sl        = float(trade.get('stop_loss', 0))

        pips    = (tp - entry) * 10000 if direction == 'BUY' else (entry - tp) * 10000
        sl_pips = abs(entry - sl) * 10000
        rr      = pips / sl_pips if sl_pips > 0 else 0

        msg  = f"🎯💰 *TAKE PROFIT HIT!* 🎯💰\n"
        msg += f"📈 {symbol} | {direction}\n"
        msg += "━" * 30 + "\n\n"
        msg += f"✅ Trade ID: `{trade_id}`\n\n"
        msg += f"📊 *Trade Result:*\n"
        msg += f"   Entry: {entry:.5f}\n"
        msg += f"   Take Profit: {tp:.5f}\n"
        msg += f"   Current: {current_price:.5f}\n\n"
        msg += f"💰 *Profit: +{pips:.1f} pips*\n"
        msg += f"📊 R:R Achieved: 1:{rr:.1f}\n\n"
        msg += f"📋 *Setup:* {trade.get('screen1_tide', '')} | {trade.get('screen2_wave', '')} | {trade.get('screen3_trigger', '')}\n"
        msg += "\n" + "━" * 30 + "\n"
        msg += "🏆 WINNER! Great trade! 🏆\n"
        msg += "🤖 Trade Monitor"
        return msg

    def _format_sl_message(self, trade, current_price):
        trade_id  = trade.get('trade_id', '')
        symbol    = trade.get('symbol', '')
        direction = trade.get('direction', '')
        entry     = float(trade.get('entry_price', 0))
        sl        = float(trade.get('stop_loss', 0))
        pips      = (sl - entry) * 10000 if direction == 'BUY' else (entry - sl) * 10000

        msg  = f"🛑😔 *STOP LOSS HIT* 🛑😔\n"
        msg += f"📈 {symbol} | {direction}\n"
        msg += "━" * 30 + "\n\n"
        msg += f"❌ Trade ID: `{trade_id}`\n\n"
        msg += f"📊 *Trade Result:*\n"
        msg += f"   Entry: {entry:.5f}\n"
        msg += f"   Stop Loss: {sl:.5f}\n"
        msg += f"   Current: {current_price:.5f}\n\n"
        msg += f"📉 *Loss: {pips:.1f} pips*\n\n"
        msg += f"📋 *Setup:* {trade.get('screen1_tide', '')} | {trade.get('screen2_wave', '')} | {trade.get('screen3_trigger', '')}\n\n"
        msg += "💪 *Stay disciplined!*\n"
        msg += "   Loss is part of trading.\n"
        msg += "   Stick to the plan!\n"
        msg += "\n" + "━" * 30 + "\n"
        msg += "🤖 Trade Monitor"
        return msg

    def monitor_open_trades(self):
        open_trades = self.logger.get_open_trades()
        if not open_trades:
            return [], [], []

        tp_hits = []
        sl_hits = []

        for trade in open_trades:
            result = self.check_trade_status(trade)
            if not result:
                continue

            status, current_price, message = result
            trade_id  = trade.get('trade_id', '')
            symbol    = trade.get('symbol', '')
            direction = trade.get('direction', '')
            entry     = float(trade.get('entry_price', 0))
            pips      = (current_price - entry) * 10000 if direction == 'BUY' else (entry - current_price) * 10000

            if status == 'TP_HIT':
                self.logger.log_trade_exit(trade_id, current_price, exit_reason='TP Hit')
                tp_hits.append({'symbol': symbol, 'direction': direction, 'pips': pips, 'trade_id': trade_id})
                if symbol in self.bot.active_signals:
                    del self.bot.active_signals[symbol]
                    self._log(f"   🔓 {symbol} unlocked after TP hit")
                if SEND_TP_SL_ALERT:
                    self.bot.send_telegram(message, message_type='tp_sl')
                    self._log(f"📱 TP Alert sent to Telegram: {symbol} +{pips:.1f} pips")
                self._log(f"🎯 TP HIT: {symbol} {direction} +{pips:.1f} pips")

            elif status == 'SL_HIT':
                self.logger.log_trade_exit(trade_id, current_price, exit_reason='SL Hit')
                sl_hits.append({'symbol': symbol, 'direction': direction, 'pips': pips, 'trade_id': trade_id})
                if symbol in self.bot.active_signals:
                    del self.bot.active_signals[symbol]
                    self._log(f"   🔓 {symbol} unlocked after SL hit")
                if SEND_TP_SL_ALERT:
                    self.bot.send_telegram(message, message_type='tp_sl')
                    self._log(f"📱 SL Alert sent to Telegram: {symbol} {pips:.1f} pips")
                self._log(f"🛑 SL HIT: {symbol} {direction} {pips:.1f} pips")

        if tp_hits or sl_hits:
            self.logger.generate_daily_performance()

        return open_trades, tp_hits, sl_hits

    def run_monitor_cycle(self):
        self._log("🔍 Checking open trades...")
        open_trades, tp_hits, sl_hits = self.monitor_open_trades()

        if open_trades:
            self._log(f"📊 Open Trades: {len(open_trades)}")
            for trade in open_trades:
                symbol    = trade.get('symbol', '')
                direction = trade.get('direction', '')
                trade_id  = trade.get('trade_id', '')
                current   = self._get_current_price(symbol)
                if current:
                    entry     = float(trade.get('entry_price', 0))
                    pnl       = (current - entry) * 10000 if direction == 'BUY' else (entry - current) * 10000
                    pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                    self._log(f"   {symbol} {direction}: {pnl_emoji} {pnl:+.1f} pips | ID: {trade_id}")

        if not open_trades and not tp_hits and not sl_hits:
            self._log("   No open trades")

        return open_trades, tp_hits, sl_hits


# ============================================
# 🤖 TRIPLE SCREEN BOT
# ============================================

class TripleScreenBot:
    def __init__(self):
        self._validate_config()
        self.key_manager   = APIKeyManager(TWELVEDATA_API_KEYS)
        self.base_url      = "https://api.twelvedata.com"
        self.telegram_url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        self.indicators    = Indicators()
        self.logger        = TradeLogger(TRADE_LOG_DIR)
        self.trade_monitor = TradeMonitor(self) if ENABLE_TRADE_MONITOR else None

        self.total_signals     = 0
        self.start_time        = datetime.now()
        self.active_setups     = {}
        self.active_signals    = {}
        self.monitor_thread    = None
        self.monitor_running   = False
        self.tp_hits_session   = []
        self.sl_hits_session   = []
        self.alert_mode        = TELEGRAM_ALERT_MODE

        self._log("=" * 50)
        self._log("🚀 Triple Screen Bot v5.0 Initialized")
        self._log(f"📊 Strategy: Dr. Alexander Elder (Simplified)")
        self._log(f"⏰ Screens: {SCREEN1_TIMEFRAME}/{SCREEN2_TIMEFRAME}/{SCREEN3_TIMEFRAME}")
        self._log(f"   S1: MACD Line zero-cross tide")
        self._log(f"   S2: RSI {RSI_BUY_LEVEL}/{RSI_SELL_LEVEL} pullback")
        self._log(f"   S3: EMA{EMA_TRIGGER} breakout entry")
        self._log(f"📈 Pairs: {len(MONITOR_PAIRS)}")
        self._log(f"🔍 Monitor: {'ON' if ENABLE_TRADE_MONITOR else 'OFF'}")
        self._log(f"📱 Alert Mode: {self.alert_mode}")
        self._log(f"📁 Logging: {'ON' if ENABLE_TRADE_LOGGING else 'OFF'}")
        self._log("=" * 50)

    def _log(self, msg):
        if ENABLE_CONSOLE_LOG:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _validate_config(self):
        errors = []
        if not TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is not set in .env")
        if not TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_CHAT_ID is not set in .env")
        if not TWELVEDATA_API_KEYS:
            errors.append("TWELVEDATA_API_KEYS is not set in .env")
        if errors:
            for e in errors:
                print(f"❌ {e}")
            print("\n📝 Please copy .env.example to .env and fill in your credentials\n")
            sys.exit(1)

    def _should_send_telegram(self, message_type):
        if self.alert_mode == "ALL":
            return True
        return message_type in ['signal', 'tp_sl']

    # ========== DATA FETCHING ==========

    def fetch_data(self, symbol, interval="1h", output_size=200, _retries=3):
        endpoint = f"{self.base_url}/time_series"

        for attempt in range(1, _retries + 1):
            current_key = self.key_manager.get_key()
            if not current_key:
                self._log("❌ No API key available")
                return None

            params = {
                'symbol':     symbol,
                'interval':   interval,
                'outputsize': output_size,
                'apikey':     current_key,
                'format':     'JSON'
            }

            try:
                resp = requests.get(endpoint, params=params, timeout=10)

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('status') == 'error':
                        self._log(f"⚠️ API error ({symbol} {interval}): {data.get('message','unknown')}")
                        self.key_manager.mark_error(current_key)
                        continue
                    self.key_manager.mark_success(current_key)
                    if 'values' in data:
                        df = pd.DataFrame(data['values'])
                        df = df.rename(columns={'datetime': 'timestamp'})
                        for col in ['open', 'high', 'low', 'close']:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        df = df.set_index('timestamp').sort_index()
                        df.dropna(inplace=True)
                        return df
                    return None

                elif resp.status_code == 429:
                    self._log(f"⚠️ Rate limit hit (attempt {attempt}/{_retries}), rotating key...")
                    self.key_manager.mark_error(current_key)
                    time.sleep(5 * attempt)
                    continue

                else:
                    self._log(f"❌ HTTP {resp.status_code} fetching {symbol} {interval}")
                    self.key_manager.mark_error(current_key)
                    return None

            except requests.exceptions.Timeout:
                self._log(f"⏱️ Timeout fetching {symbol} {interval} (attempt {attempt})")
            except Exception as e:
                self._log(f"❌ Fetch error ({symbol} {interval}): {e}")
                return None

        self._log(f"❌ All {_retries} attempts failed for {symbol} {interval}")
        return None

    # ========== SCREEN 1: TIDE (MACD Line zero cross) ==========

    def analyze_screen1_tide(self, df):
        """
        BULLISH  : MACD Line > 0  (fast EMA above slow EMA)
        BEARISH  : MACD Line < 0  (fast EMA below slow EMA)
        NEUTRAL  : MACD Line == 0 (extremely rare, treat as no signal)
        """
        if df is None or len(df) < 50:
            return 'NEUTRAL', {'error': 'Not enough data'}

        close = df['close']
        macd_line, signal_line = self.indicators.macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)

        macd_val   = macd_line.iloc[-1]
        signal_val = signal_line.iloc[-1]

        if macd_val > 0:
            tide     = 'BULLISH'
            strength = 'Strong' if macd_val > signal_val else 'Moderate'
        elif macd_val < 0:
            tide     = 'BEARISH'
            strength = 'Strong' if macd_val < signal_val else 'Moderate'
        else:
            tide     = 'NEUTRAL'
            strength = 'Flat'

        return tide, {
            'strength':   strength,
            'macd_line':  macd_val,
            'signal_val': signal_val,
            'price':      close.iloc[-1]
        }

    # ========== SCREEN 2: WAVE (RSI 40 / 60 pullback) ==========

    def analyze_screen2_wave(self, df, tide_direction):
        """
        BULLISH tide → BUY_SETUP  when RSI <= 40  (pullback / oversold zone)
        BEARISH tide → SELL_SETUP when RSI >= 60  (pullback / overbought zone)
        """
        if df is None or len(df) < 50:
            return 'WAIT', {'error': 'Not enough data'}

        close   = df['close']
        rsi_val = self.indicators.rsi(close, RSI_PERIOD).iloc[-1]

        wave     = 'WAIT'
        strength = 'No setup'

        if tide_direction == 'BULLISH':
            if rsi_val <= RSI_BUY_LEVEL:
                wave     = 'BUY_SETUP'
                strength = 'Strong' if rsi_val <= 30 else 'Moderate'
            elif rsi_val >= 70:
                wave     = 'OVERBOUGHT'
                strength = 'Avoid buying'

        elif tide_direction == 'BEARISH':
            if rsi_val >= RSI_SELL_LEVEL:
                wave     = 'SELL_SETUP'
                strength = 'Strong' if rsi_val >= 70 else 'Moderate'
            elif rsi_val <= 30:
                wave     = 'OVERSOLD'
                strength = 'Avoid selling'

        return wave, {
            'strength': strength,
            'rsi':      rsi_val,
            'price':    close.iloc[-1]
        }

    # ========== SCREEN 3: RIPPLE (EMA20 breakout entry) ==========

    def analyze_screen3_ripple(self, df, wave_signal):
        """
        BUY_TRIGGER  : previous close <= EMA20, current close > EMA20
        SELL_TRIGGER : previous close >= EMA20, current close < EMA20
        SL/TP use ATR multipliers.
        """
        if df is None or len(df) < max(EMA_TRIGGER, ATR_PERIOD) + 2:
            return 'NO_TRIGGER', {}

        close = df['close']
        high  = df['high']
        low   = df['low']

        ema20         = self.indicators.ema(close, EMA_TRIGGER)
        ema20_current = ema20.iloc[-1]
        ema20_prev    = ema20.iloc[-2]

        current_price = close.iloc[-1]
        prev_price    = close.iloc[-2]

        recent_high = high.iloc[-BREAKOUT_PERIOD:-1].max()
        recent_low  = low.iloc[-BREAKOUT_PERIOD:-1].min()

        atr_val = self.indicators.atr(df, ATR_PERIOD).iloc[-1]

        trigger = 'NO_TRIGGER'
        details = {
            'price':         current_price,
            'ema20':         ema20_current,
            'breakout_high': recent_high,
            'breakout_low':  recent_low,
            'atr':           atr_val
        }

        if wave_signal == 'BUY_SETUP':
            # Price closes above EMA20 (crossover from below)
            if prev_price <= ema20_prev and current_price > ema20_current:
                trigger = 'BUY_TRIGGER'
                details['trigger_type'] = 'EMA20 Breakout'

        elif wave_signal == 'SELL_SETUP':
            # Price closes below EMA20 (crossunder from above)
            if prev_price >= ema20_prev and current_price < ema20_current:
                trigger = 'SELL_TRIGGER'
                details['trigger_type'] = 'EMA20 Breakdown'

        if trigger in ['BUY_TRIGGER', 'SELL_TRIGGER']:
            if trigger == 'BUY_TRIGGER':
                details['stop_loss']   = current_price - (atr_val * ATR_SL_MULTIPLIER)
                details['take_profit'] = current_price + (atr_val * ATR_TP_MULTIPLIER)
            else:
                details['stop_loss']   = current_price + (atr_val * ATR_SL_MULTIPLIER)
                details['take_profit'] = current_price - (atr_val * ATR_TP_MULTIPLIER)

        return trigger, details

    # ========== FULL ANALYSIS ==========

    def triple_screen_analysis(self, symbol):
        self._log(f"\n🔍 Triple Screen: {symbol}")
        self._log("-" * 40)

        # Active signal guard
        if symbol in self.active_signals:
            trade_id = self.active_signals[symbol]
            self._log(f"   ⏸️ {symbol}: Active signal [{trade_id}] — waiting for TP/SL")
            return None

        # ── SCREEN 1 ──
        self._log(f"📊 Screen 1 ({SCREEN1_TIMEFRAME}): Tide (MACD Line zero cross)...")
        key_stats = self.key_manager.get_stats()
        for key_name, stat in key_stats.items():
            if '◀ current' in key_name:
                self._log(f"   🔑 {key_name} | Requests: {stat['count']}/{stat['limit']} | Errors: {stat['errors']}")

        df_s1 = self.fetch_data(symbol, SCREEN1_TIMEFRAME, 200)
        if df_s1 is None:
            self._log(f"   ❌ Screen 1 SKIPPED — Data fetch failed")
            return None

        tide, tide_details = self.analyze_screen1_tide(df_s1)
        if tide_details.get('error'):
            self._log(f"   ❌ Screen 1 SKIPPED — {tide_details.get('error')}")
            return None

        self._log(f"   Tide: {tide} ({tide_details.get('strength', 'N/A')})")
        self._log(f"   MACD Line: {tide_details.get('macd_line', 0):.6f} | Signal: {tide_details.get('signal_val', 0):.6f}")

        if tide == 'NEUTRAL':
            self._log(f"   ⏭️ MACD Line at zero — no clear tide, skipping")
            return None

        time.sleep(DELAY_BETWEEN_SCREENS)

        # ── SCREEN 2 ──
        self._log(f"📊 Screen 2 ({SCREEN2_TIMEFRAME}): Wave (RSI {RSI_BUY_LEVEL}/{RSI_SELL_LEVEL})...")
        df_s2 = self.fetch_data(symbol, SCREEN2_TIMEFRAME, 200)
        if df_s2 is None:
            self._log(f"   ❌ Screen 2 SKIPPED — Data fetch failed")
            return None

        wave, wave_details = self.analyze_screen2_wave(df_s2, tide)
        if wave_details.get('error'):
            self._log(f"   ❌ Screen 2 SKIPPED — {wave_details.get('error')}")
            return None

        self._log(f"   Wave: {wave} ({wave_details.get('strength', 'N/A')})")
        self._log(f"   RSI({RSI_PERIOD}): {wave_details.get('rsi', 0):.1f}")

        if wave in ['BUY_SETUP', 'SELL_SETUP']:
            setup_data = {
                'datetime':           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'symbol':             symbol,
                'screen1_tide':       tide,
                'screen1_macd_line':  tide_details.get('macd_line', 0),
                'screen2_wave':       wave,
                'screen2_rsi':        wave_details.get('rsi', 0),
                'current_price':      wave_details.get('price', 0),
                'eventually_triggered': False,
                'time_to_trigger_minutes': '',
                'notes': ''
            }
            setup_id = self.logger.log_setup(setup_data)
            if setup_id:
                self.active_setups[setup_id] = {'data': setup_data, 'time': datetime.now()}

        if wave not in ['BUY_SETUP', 'SELL_SETUP']:
            self._log(f"   📝 {symbol}: {tide} tide, {wave} — No setup (Terminal only)")
            return None

        time.sleep(DELAY_BETWEEN_SCREENS)

        # ── SCREEN 3 ──
        self._log(f"📊 Screen 3 ({SCREEN3_TIMEFRAME}): Ripple (EMA{EMA_TRIGGER} Breakout)...")
        df_s3 = self.fetch_data(symbol, SCREEN3_TIMEFRAME, 100)
        if df_s3 is None:
            self._log(f"   ❌ Screen 3 SKIPPED — Data fetch failed")
            return None

        trigger, trigger_details = self.analyze_screen3_ripple(df_s3, wave)
        self._log(f"   Trigger: {trigger}")
        if trigger_details.get('ema20'):
            self._log(f"   EMA{EMA_TRIGGER}: {trigger_details['ema20']:.5f} | Price: {trigger_details.get('price', 0):.5f}")

        entry_price  = trigger_details.get('price', 0)
        sl           = trigger_details.get('stop_loss', 0)
        tp           = trigger_details.get('take_profit', 0)
        is_triggered = trigger in ['BUY_TRIGGER', 'SELL_TRIGGER']

        signal_data = {
            'datetime':          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'symbol':            symbol,
            'screen1_tide':      tide,
            'screen1_macd_line': tide_details.get('macd_line', 0),
            'screen2_wave':      wave,
            'screen2_rsi':       wave_details.get('rsi', 0),
            'screen3_trigger':   trigger,
            'screen3_ema20':     trigger_details.get('ema20', 0),
            'current_price':     df_s3['close'].iloc[-1] if df_s3 is not None else 0,
            'entry_price':       entry_price if is_triggered else 0,
            'stop_loss':         sl if is_triggered else 0,
            'take_profit':       tp if is_triggered else 0,
            'risk_reward_ratio': MIN_RISK_REWARD_RATIO if is_triggered else 0,
            'is_triggered':      is_triggered,
            'atr_value':         trigger_details.get('atr', 0),
            'notes': ''
        }

        self.logger.log_signal(signal_data)

        if is_triggered:
            self.total_signals += 1
            direction = 'BUY' if trigger == 'BUY_TRIGGER' else 'SELL'

            trade_data = {
                'datetime':          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'symbol':            symbol,
                'direction':         direction,
                'screen1_tide':      tide,
                'screen1_macd_line': tide_details.get('macd_line', 0),
                'screen2_wave':      wave,
                'screen2_rsi':       wave_details.get('rsi', 0),
                'screen3_trigger':   trigger,
                'screen3_ema20':     trigger_details.get('ema20', 0),
                'entry_price':       entry_price,
                'stop_loss':         sl,
                'take_profit':       tp,
                'atr_value':         trigger_details.get('atr', 0),
                'screen1_tf':        SCREEN1_TIMEFRAME,
                'screen2_tf':        SCREEN2_TIMEFRAME,
                'screen3_tf':        SCREEN3_TIMEFRAME,
                'breakout_level':    trigger_details.get('breakout_high', trigger_details.get('breakout_low', 0)),
                'confidence':        'HIGH' if tide_details.get('strength') == 'Strong' else 'MEDIUM',
                'notes': ''
            }

            trade_id = self.logger.log_trade_entry(trade_data)

            self.active_signals[symbol] = trade_id
            self._log(f"   🔒 {symbol} locked — waiting for TP/SL (ID: {trade_id})")

            for sid, sinfo in self.active_setups.items():
                if sinfo['data']['symbol'] == symbol and sinfo['data']['screen2_wave'] == wave:
                    sinfo['data']['eventually_triggered'] = True
                    diff = datetime.now() - sinfo['time']
                    sinfo['data']['time_to_trigger_minutes'] = f"{diff.total_seconds()/60:.0f}"

            self.logger.generate_daily_performance()

            message = self.format_full_signal(
                symbol, tide, tide_details, wave, wave_details,
                trigger, trigger_details, trade_id
            )
            return ('signal', message)

        else:
            self._log(f"   ⏳ {symbol}: {wave} ready, waiting for EMA{EMA_TRIGGER} breakout")
            self._log(f"      Tide: {tide} | RSI: {wave_details.get('rsi', 0):.1f}")
            self._log(f"      EMA{EMA_TRIGGER}: {trigger_details.get('ema20', 0):.5f}")
            return None

    # ========== MESSAGE FORMATTING ==========

    def format_full_signal(self, symbol, tide, tide_d, wave, wave_d, trigger, trigger_d, trade_id=None):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if trigger == 'BUY_TRIGGER':
            emoji     = "🟢🟢🟢"
            direction = "BUY/LONG"
        else:
            emoji     = "🔴🔴🔴"
            direction = "SELL/SHORT"

        entry   = trigger_d.get('price', 0)
        sl      = trigger_d.get('stop_loss', 0)
        tp      = trigger_d.get('take_profit', 0)
        sl_pips = abs(entry - sl) * 10000
        tp_pips = abs(tp - entry) * 10000
        rr      = tp_pips / sl_pips if sl_pips > 0 else 0

        msg  = f"{emoji} *TRIPLE SCREEN SIGNAL* {emoji}\n"
        msg += f"📈 {symbol} | 🕐 {now}\n"
        msg += "━" * 30 + "\n\n"

        msg += f"🎯 *{direction} SIGNAL*\n"
        msg += f"   Confidence: {'HIGH' if tide_d.get('strength') == 'Strong' else 'MEDIUM'}\n\n"

        msg += f"📊 *Screen 1 ({SCREEN1_TIMEFRAME}): TIDE*\n"
        msg += f"   Direction: {tide} ({tide_d.get('strength', 'N/A')})\n"
        msg += f"   MACD Line: {tide_d.get('macd_line', 0):.6f} ({'Above' if tide_d.get('macd_line', 0) > 0 else 'Below'} zero)\n\n"

        msg += f"📊 *Screen 2 ({SCREEN2_TIMEFRAME}): WAVE*\n"
        msg += f"   Setup: {wave} ({wave_d.get('strength', 'N/A')})\n"
        msg += f"   RSI({RSI_PERIOD}): {wave_d.get('rsi', 0):.1f}\n\n"

        msg += f"📊 *Screen 3 ({SCREEN3_TIMEFRAME}): RIPPLE*\n"
        msg += f"   Trigger: {trigger} ✅\n"
        msg += f"   Type: {trigger_d.get('trigger_type', 'N/A')}\n"
        msg += f"   EMA{EMA_TRIGGER}: {trigger_d.get('ema20', 0):.5f}\n\n"

        msg += "📋 *Trade Plan:*\n"
        msg += f"   Entry: {entry:.5f}\n"
        msg += f"   Stop Loss: {sl:.5f} ({sl_pips:.0f} pips)\n"
        msg += f"   Take Profit: {tp:.5f} ({tp_pips:.0f} pips)\n"
        msg += f"   R:R = 1:{rr:.1f}\n"

        if trade_id:
            msg += f"\n📝 Trade ID: `{trade_id}`\n"

        msg += "\n" + "━" * 30 + "\n"
        msg += "🤖 Triple Screen Bot v5.0 | TP/SL Monitor ON"
        return msg

    # ========== TELEGRAM ==========

    def send_telegram(self, message, message_type='other'):
        if not self._should_send_telegram(message_type):
            self._log(f"📝 [{message_type}] Terminal only")
            return False

        url     = f"{self.telegram_url}/sendMessage"
        payload = {
            'chat_id':                  TELEGRAM_CHAT_ID,
            'text':                     message,
            'parse_mode':               'Markdown',
            'disable_web_page_preview': True
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                self._log("📱 Telegram: Message sent")
                return True
            else:
                self._log(f"❌ Telegram error: {resp.status_code}")
                return False
        except Exception as e:
            self._log(f"❌ Telegram send error: {e}")
            return False

    # ========== TRADE MONITOR CONTROL ==========

    def _start_trade_monitor(self):
        if not ENABLE_TRADE_MONITOR or not self.trade_monitor:
            return

        self.monitor_running = True

        def monitor_loop():
            self._log("📊 Trade Monitor started (background)")
            time.sleep(30)
            while self.monitor_running:
                try:
                    open_trades, tp_hits, sl_hits = self.trade_monitor.run_monitor_cycle()
                    self.tp_hits_session.extend(tp_hits)
                    self.sl_hits_session.extend(sl_hits)
                except Exception as e:
                    self._log(f"Monitor error: {e}")

                for _ in range(TRADE_MONITOR_INTERVAL_MINUTES * 60):
                    if not self.monitor_running:
                        break
                    time.sleep(1)

        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _stop_trade_monitor(self):
        self.monitor_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)

    def close_trade_manually(self, trade_id, exit_price=None):
        trade = self.logger.get_trade_by_id(trade_id)
        if not trade:
            self._log(f"❌ Trade not found: {trade_id}")
            return False
        if trade.get('trade_status') != 'OPEN':
            self._log(f"⚠️ Trade already closed: {trade_id}")
            return False

        symbol = trade.get('symbol', '')

        if exit_price is None:
            exit_price = self.trade_monitor._get_current_price(symbol)
            if exit_price is None:
                self._log(f"❌ Cannot get price for {symbol}")
                return False

        self.logger.log_trade_exit(trade_id, exit_price, exit_reason='Manual Close')

        if symbol in self.active_signals:
            del self.active_signals[symbol]
            self._log(f"   🔓 {symbol} unlocked after manual close")

        entry     = float(trade.get('entry_price', 0))
        direction = trade.get('direction', '')
        pips      = (exit_price - entry) * 10000 if direction == 'BUY' else (entry - exit_price) * 10000

        msg  = f"🔒 *Trade Closed Manually*\n"
        msg += f"📈 {symbol} | {direction}\n"
        msg += f"📝 Trade ID: `{trade_id}`\n"
        msg += f"📊 Entry: {entry:.5f} → Exit: {exit_price:.5f}\n"
        msg += f"💰 P/L: {pips:+.1f} pips"

        self.send_telegram(msg, message_type='tp_sl')
        self.logger.generate_daily_performance()
        return True

    def check_trades_command(self):
        if not self.trade_monitor:
            self._log("⚠️ Trade Monitor is disabled")
            return

        open_trades = self.logger.get_open_trades()
        summary     = self.logger.get_trade_summary_today()

        self._log(f"\n📊 Trade Status Report")
        self._log(f"   Open: {summary['open']} | Closed: {summary['closed']}")
        self._log(f"   🎯 TP: {summary['tp_hits']} | 🛑 SL: {summary['sl_hits']}")
        self._log(f"   💰 P/L: {summary['total_pips']:+.1f} pips | Win: {summary['win_rate']:.1f}%")

        if open_trades:
            self._log(f"\n🔓 Open Trades:")
            for trade in open_trades:
                symbol    = trade.get('symbol', '')
                direction = trade.get('direction', '')
                entry     = float(trade.get('entry_price', 0))
                trade_id  = trade.get('trade_id', '')
                current   = self.trade_monitor._get_current_price(symbol)
                if current:
                    pnl       = (current - entry) * 10000 if direction == 'BUY' else (entry - current) * 10000
                    pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                    self._log(f"   {symbol} {direction}: {pnl_emoji} {pnl:+.1f} pips | {trade_id}")

    # ========== MAIN RUN ==========

    def run_once(self):
        self._log(f"\n{'='*40}")
        self._log(f"🔄 Triple Screen Analysis")
        self._log(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log(f"{'='*40}")

        signals_found = 0

        for i, symbol in enumerate(MONITOR_PAIRS):
            try:
                result = self.triple_screen_analysis(symbol)
                if result is not None:
                    message_type, message = result
                    if message_type == 'signal':
                        self.send_telegram(message, message_type='signal')
                        signals_found += 1
                        self._log(f"📱 SIGNAL sent to Telegram: {symbol}")

                if i < len(MONITOR_PAIRS) - 1:
                    time.sleep(DELAY_BETWEEN_PAIRS)

            except Exception as e:
                self._log(f"❌ Error analyzing {symbol}: {e}")

        self._log(f"\n✅ Cycle Complete: {signals_found} signals")
        self.logger.print_summary()

    def run(self):
        self._log(f"\n🔄 Starting Triple Screen Monitor...")
        self._log(f"   Screens: {SCREEN1_TIMEFRAME}/{SCREEN2_TIMEFRAME}/{SCREEN3_TIMEFRAME}")
        self._log(f"   Pairs: {MONITOR_PAIRS}")
        self._log(f"   Interval: {ANALYSIS_INTERVAL_MINUTES} min")
        self._log(f"   📱 Alert Mode: {self.alert_mode}")

        self._start_trade_monitor()
        self._log(f"🚀 Bot started - Monitoring {len(MONITOR_PAIRS)} pairs\n")

        try:
            while True:
                self.run_once()
                next_run = datetime.now() + timedelta(minutes=ANALYSIS_INTERVAL_MINUTES)
                self._log(f"\n⏳ Next analysis: {next_run.strftime('%H:%M:%S')}")
                time.sleep(ANALYSIS_INTERVAL_MINUTES * 60)

        except KeyboardInterrupt:
            self._log("\n👋 Stopping...")
            self._stop_trade_monitor()

            runtime = datetime.now() - self.start_time
            summary = self.logger.get_trade_summary_today()
            self.logger.generate_daily_performance()

            self._log(f"\n📊 Final Stats:")
            self._log(f"   Signals: {self.total_signals}")
            self._log(f"   Runtime: {runtime}")
            self._log(f"   Today P/L: {summary['total_pips']:+.1f} pips")
            self._log(f"   🎯 TP: {summary['tp_hits']} | 🛑 SL: {summary['sl_hits']}")
            self._log("✅ Bot stopped")


# ============================================
# 📊 BACKTEST ANALYZER
# ============================================

class BacktestAnalyzer:
    def __init__(self, log_dir="trade_logs"):
        self.log_dir    = Path(log_dir)
        self.trade_file = self.log_dir / TRADE_HISTORY_FILE
        self.signal_file = self.log_dir / SIGNAL_LOG_FILE

    def load_trades(self):
        trades = []
        if self.trade_file.exists():
            with open(self.trade_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                trades = list(reader)
        return trades

    def analyze_by_symbol(self, symbol=None):
        trades = self.load_trades()
        if symbol:
            trades = [t for t in trades if t.get('symbol') == symbol]

        closed = [t for t in trades if t.get('trade_status') == 'CLOSED']
        if not closed:
            return None

        pips    = [float(t.get('profit_loss_pips', 0)) for t in closed]
        winners = [p for p in pips if p > 0]
        losers  = [p for p in pips if p <= 0]

        return {
            'total':         len(closed),
            'winners':       len(winners),
            'losers':        len(losers),
            'win_rate':      len(winners)/len(closed)*100 if closed else 0,
            'total_pips':    sum(pips),
            'avg_win':       sum(winners)/len(winners) if winners else 0,
            'avg_loss':      sum(losers)/len(losers) if losers else 0,
            'best':          max(pips) if pips else 0,
            'worst':         min(pips) if pips else 0,
            'profit_factor': sum(winners)/abs(sum(losers)) if losers and sum(losers) != 0 else 0
        }

    def print_analysis(self):
        print("\n" + "=" * 60)
        print("📊 BACKTEST ANALYSIS")
        print("=" * 60)

        overall = self.analyze_by_symbol()
        if overall:
            self._print_stats("OVERALL", overall)

        trades  = self.load_trades()
        symbols = set(t.get('symbol', '') for t in trades)

        for sym in sorted(symbols):
            if sym:
                stats = self.analyze_by_symbol(sym)
                if stats:
                    self._print_stats(sym, stats)

        for direction in ['BUY', 'SELL']:
            dir_trades = [t for t in trades if t.get('direction') == direction and t.get('trade_status') == 'CLOSED']
            if dir_trades:
                pips = [float(t.get('profit_loss_pips', 0)) for t in dir_trades]
                wins = sum(1 for p in pips if p > 0)
                print(f"\n📈 {direction}: {len(dir_trades)} trades | Wins: {wins} | Pips: {sum(pips):.1f}")

        reasons = set(t.get('exit_reason', '') for t in trades if t.get('trade_status') == 'CLOSED')
        for reason in sorted(reasons):
            if reason:
                r_trades = [t for t in trades if t.get('exit_reason') == reason]
                r_pips   = [float(t.get('profit_loss_pips', 0)) for t in r_trades]
                print(f"\n📋 {reason}: {len(r_trades)} trades | Pips: {sum(r_pips):.1f}")

    def _print_stats(self, name, stats):
        if not stats:
            return
        print(f"\n📈 {name}:")
        print(f"   Trades: {stats['total']} | Win: {stats['win_rate']:.1f}%")
        print(f"   Pips: {stats['total_pips']:.1f} | PF: {stats['profit_factor']:.2f}")
        print(f"   Avg Win: {stats['avg_win']:.1f} | Avg Loss: {stats['avg_loss']:.1f}")
        print(f"   Best: {stats['best']:.1f} | Worst: {stats['worst']:.1f}")


# ============================================
# 🚀 MAIN
# ============================================

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("   TRIPLE SCREEN TRADING BOT v5.0")
    print("   Strategy: Dr. Alexander Elder (Simplified)")
    print(f"   S1 ({SCREEN1_TIMEFRAME}): MACD Line > 0 / < 0  → Tide")
    print(f"   S2 ({SCREEN2_TIMEFRAME}): RSI {RSI_BUY_LEVEL}/{RSI_SELL_LEVEL}            → Wave")
    print(f"   S3 ({SCREEN3_TIMEFRAME}): EMA{EMA_TRIGGER} Breakout       → Entry")
    print("=" * 50)

    print(f"\n📋 Configuration:")
    print(f"   Screens : {SCREEN1_TIMEFRAME}/{SCREEN2_TIMEFRAME}/{SCREEN3_TIMEFRAME}")
    print(f"   Pairs   : {MONITOR_PAIRS}")
    print(f"   Interval: {ANALYSIS_INTERVAL_MINUTES} min")
    print(f"   Monitor : {'ON' if ENABLE_TRADE_MONITOR else 'OFF'}")
    print(f"   📱 Alert: {TELEGRAM_ALERT_MODE}")

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == 'backtest':
            print("\n📊 Running Backtest Analysis...")
            analyzer = BacktestAnalyzer()
            analyzer.print_analysis()
            sys.exit(0)
        elif cmd == 'export':
            print("\n📁 Exporting backtest data...")
            bot = TripleScreenBot()
            bot.logger.export_for_backtesting()
            sys.exit(0)
        elif cmd == 'status':
            print("\n📊 Checking trade status...")
            bot = TripleScreenBot()
            bot.check_trades_command()
            sys.exit(0)
        elif cmd == 'close':
            if len(sys.argv) > 2:
                trade_id = sys.argv[2]
                print(f"\n🔒 Closing trade: {trade_id}")
                bot = TripleScreenBot()
                bot.close_trade_manually(trade_id)
            else:
                print("\n❌ Usage: python tps_v2.py close <TRADE_ID>")
            sys.exit(0)
        else:
            print(f"\n❌ Unknown command: {cmd}")
            print("Commands: backtest | export | status | close <ID>")
            sys.exit(1)

    print("\n" + "-" * 50)
    print("Commands:")
    print("  python tps_v2.py                    Run bot")
    print("  python tps_v2.py backtest           Backtest")
    print("  python tps_v2.py export             Export")
    print("  python tps_v2.py status             Status")
    print("  python tps_v2.py close <TRADE_ID>   Close trade")
    print("-" * 50 + "\n")

    bot = TripleScreenBot()
    bot.run()
