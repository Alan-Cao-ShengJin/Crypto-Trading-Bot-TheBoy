from keys import api, secret
from binance.um_futures import UMFutures
import ta
import pandas as pd
import numpy as np
from time import sleep
from binance.error import ClientError
import traceback
import sys
import math
import time
import json
import os
from datetime import datetime, timedelta
import re
from itertools import cycle
import sys
from collections import deque
from supertrend import supertrend_indicator
from order_monitor import OrderMonitor
import telegram as telegram

client = UMFutures(key = api, secret = secret)

# Trading parameters
# risk_percentage = 0.02
risk_atr_period = 11
risk_atr_multiple = 1.0
tp_atr_multiple = 1.0
trailing_atr_multiple = 1.0
trailing_atr_trigger = 2.0
supertrend_atr_period = 14
supertrend_factor = 2.0
ssl_period = 4
chop_length = 7
chop_threshold = 44.0
type = 'ISOLATED'
timeframe = '4h'
target_risk_percentage = 0.02  # Target risk (2%)
min_acceptable_risk = 0.018    # Minimum acceptable risk (1.8%)
risk_adjustment_factor = 1.1    # Adjustment factor to compensate for rounding

# Binance BTCUSDC Futures constraints
MIN_ORDER_SIZE = 0.002  # Minimum order size is 0.002 BTC
SAFE_MIN_SIZE = 0.005   # Safer minimum (allowing for 2 tranches)
MAX_LEVERAGE = 125      # Maximum allowed leverage

TEXT_COLORS = {
    'GREEN': '\033[1;92m',  # Bright neon green
    'RED': '\033[1;91m',    # Bright red
    'YELLOW': '\033[1;93m', # Bright yellow (for chop data only)
    'CYAN': '\033[1;96m',
    'RESET': '\033[0m'
}

# State tracking variables
prev_supertrend = None
prev_ssl = None
st_reset_detected = False
first_tranche_closed = False
trailing_stop_activated = False
position_entry_price = 0
position_entry_atr = 0
current_stop_loss =  0
current_take_profit = 0
stop_order_monitor = False
position_size = 0

# P&L tracker and trade logging variables
last_pnl_update = datetime.now()
trade_history = []

# Ensure log directory exists
log_dir = "trading_logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
trade_log_file = os.path.join(log_dir, "trade_log.txt")
pnl_summary_file = os.path.join(log_dir, "pnl_summary.txt")

class RateLimiter:
    def __init__(self, max_calls_per_minute=60, max_calls_per_second=20):
        self.max_calls_per_minute = max_calls_per_minute
        self.max_calls_per_second = max_calls_per_second
        self.minute_calls = deque()
        self.second_calls = deque()
    
    def check_rate_limit(self):
        """Check if we're about to exceed rate limits and wait if necessary"""
        current_time = time.time()
        
        # Clean up old timestamps
        self._cleanup_timestamps(current_time)
        
        # Record this call (do this first to avoid race conditions)
        self.second_calls.append(current_time)
        self.minute_calls.append(current_time)
        
        # Check if we need to wait (approaching limits)
        if len(self.second_calls) >= self.max_calls_per_second:
            sleep_time = 1.1 - (current_time - self.second_calls[0])
            if sleep_time > 0:
                # Print a complete separate message with newlines
                print(f"\n{TEXT_COLORS['YELLOW']}Rate limit approaching, waiting {sleep_time:.2f}s{TEXT_COLORS['RESET']}")
                time.sleep(sleep_time)
                print("")  # Extra newline for separation
                
        if len(self.minute_calls) >= self.max_calls_per_minute:
            sleep_time = 60.1 - (current_time - self.minute_calls[0])
            if sleep_time > 0:
                # Print a complete separate message with newlines
                print(f"\n{TEXT_COLORS['YELLOW']}Minute rate limit approaching, waiting {sleep_time:.2f}s{TEXT_COLORS['RESET']}")
                time.sleep(sleep_time)
                print("")  # Extra newline for separation
    
    def _cleanup_timestamps(self, current_time):
        """Remove timestamps older than the tracking periods"""
        while self.second_calls and current_time - self.second_calls[0] > 1.0:
            self.second_calls.popleft()
            
        while self.minute_calls and current_time - self.minute_calls[0] > 60.0:
            self.minute_calls.popleft()
            
    def handle_rate_limit_error(self):
        """Handle a rate limit error with exponential backoff"""
        wait_time = min(60, 5 * (2 ** len(self.minute_calls) / self.max_calls_per_minute))
        print(f"\r{TEXT_COLORS['RED']}Rate limit exceeded. Backing off for {wait_time:.2f}s{TEXT_COLORS['RESET']}", end='', flush=True)
        time.sleep(wait_time)
        print("\r" + " " * 50 + "\r", end='', flush=True)  # Clear the line

# Create a global rate limiter
rate_limiter = RateLimiter()

# Decorator for API functions
def rate_limited(func):
    def wrapper(*args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                rate_limiter.check_rate_limit()
                return func(*args, **kwargs)
            except ClientError as error:
                if error.status_code == 429 or "too many requests" in str(error).lower():
                    rate_limiter.handle_rate_limit_error()
                    if attempt < max_retries - 1:
                        continue
                raise
    return wrapper

@rate_limited
def get_balance_usdc():
    try:
        response = client.balance(recvWindow=6000)
        for elem in response:
            if elem['asset'] == 'USDC':
                # Round down to 2 decimal places
                return math.floor(float(elem['balance']) * 100) / 100
    except ClientError as error:
        print(
            f"{TEXT_COLORS['RED']}Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}{TEXT_COLORS['RESET']}"
        )

@rate_limited
def klines(symbol):
    try:
        resp = pd.DataFrame(client.klines(symbol, timeframe))  # Using global timeframe
        resp = resp.iloc[:,:6]
        resp.columns = ['time', 'open', 'high', 'low', 'close', 'volume']
        resp = resp.set_index('time')
        resp.index = pd.to_datetime(resp.index, unit = 'ms')
        resp.index = resp.index + pd.Timedelta(hours=8)
        resp = resp.astype(float)
        return resp
    except ClientError as error:
        print(
            f"{TEXT_COLORS['RED']}Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}{TEXT_COLORS['RESET']}"
        )

@rate_limited
def get_server_time():
    """Get Binance server time for synchronization."""
    try:
        server_time = client.time()
        return datetime.fromtimestamp(server_time['serverTime'] / 1000)
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error getting server time: {e}{TEXT_COLORS['RESET']}")
        return datetime.now()  # Fallback to local time

def get_next_candle_time():
    """Get the time when the next candle will close based on the global timeframe."""
    global timeframe
    # Use regex to parse timeframe (e.g., '5m' -> 5, 'm'; '2h' -> 2, 'h')
    match = re.match(r'(\d+)([mh])', timeframe)
    if match:
        number = int(match.group(1))
        unit = match.group(2)
        # Convert to minutes: 'm' stays as is, 'h' multiplies by 60
        interval = number if unit == 'm' else number * 60
    else:
        interval = 1  # Default to 1 minute if timeframe is invalid

    # Get current time and calculate total minutes since midnight
    now = get_server_time()
    total_minutes = now.hour * 60 + now.minute

    # Find minutes until next candle
    remainder = total_minutes % interval
    delta_minutes = interval if remainder == 0 else interval - remainder

    # Truncate to current minute, then add delta to reach next candle time
    now_truncated = now.replace(second=0, microsecond=0)
    next_candle = now_truncated + timedelta(minutes=delta_minutes)

    return next_candle

def get_price_precision(symbol):
    resp = client.exchange_info()['symbols']
    for elem in resp:
        if elem['symbol'] == symbol:
            return elem['pricePrecision']-1
    return 1

def get_qty_precision(symbol):
    resp = client.exchange_info()['symbols']
    for elem in resp:
        if elem['symbol'] == symbol:
            return elem['quantityPrecision'], MIN_ORDER_SIZE
    return 3, MIN_ORDER_SIZE 

def countdown_with_animation():
    """Display a countdown timer with a swirl animation until the next candle."""
    next_candle = get_next_candle_time()
    swirl_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']  # Swirl characters
    sleep_interval = 0.3  # Slower animation speed to reduce CPU usage
    
    # Track time for PnL updates
    last_pnl_check = datetime.now()
    pnl_check_interval = 300  # 5 minutes in seconds

    try:
        # Hide the cursor using ANSI escape code
        print("\033[?25l", end='', flush=True)
        
        # Cycle through swirl characters
        char_index = 0
        last_update_time = time.time()
        last_message_length = 0
        
        while (time_left := (next_candle - datetime.now()).total_seconds()) > 0:
            current_time = time.time()
            time_since_update = current_time - last_update_time
            
            if time_since_update >= sleep_interval:
                # Update animation only when enough time has passed
                char_index = (char_index + 1) % len(swirl_chars)
                minutes, seconds = divmod(int(time_left), 60)
                time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
                message = f"{swirl_chars[char_index]} Next candle closes in {time_str}"
                
                # Make sure to clear previous message completely
                clear_str = "\r" + " " * max(last_message_length, len(message) + 5)
                print(clear_str, end="\r", flush=True)
                print(message, end="", flush=True)
                
                # Remember length for next clear
                last_message_length = len(message)
                last_update_time = current_time
            
            # Check for P&L update
            now = datetime.now()
            if (now - last_pnl_check).total_seconds() >= pnl_check_interval:
                # Clear current line and move to a new line before showing update
                print("\r" + " " * last_message_length + "\r\n", flush=True)
                print("\033[?25h", flush=True)  # Show cursor
                
                # Call check_pnl_update directly
                check_pnl_update()
                last_pnl_check = now
                
                # Hide cursor again and prepare for next animation
                print("\033[?25l", end="", flush=True)
                last_update_time = 0
                last_message_length = 0
            
            # Small sleep to prevent CPU hogging
            time.sleep(0.1)
        
        # Clear the line and show the cursor again
        print("\r" + " " * last_message_length + "\r", end="", flush=True)
        print("\033[?25h", end="", flush=True)
    
    except Exception as e:
        print("\033[?25h", end="", flush=True)
        print(f"\nError in countdown: {e}")

@rate_limited
def set_leverage(symbol, level):
    try:
        response = client.change_leverage(symbol=symbol, leverage=level, recvWindow=6000)
        print(f"{TEXT_COLORS['GREEN']}Leverage set to {level}x for {symbol}{TEXT_COLORS['RESET']}")
    except ClientError as error:
        print(
            f"{TEXT_COLORS['RED']}Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}{TEXT_COLORS['RESET']}"
        )

def calculate_atr(df, length=risk_atr_period):
    # Fetch the Kline data for the given symbol
    df = df.copy()
    
    # Check if data fetching failed (klines might return None on error)
    if df is None:
        return None
    
    # Calculate the True Range (TR)
    previous_close = df['close'].shift(1)  # Previous period's close price
    hl = df['high'] - df['low']            # High minus Low
    hp = (df['high'] - previous_close).abs()  # Absolute High minus Previous Close
    lp = (df['low'] - previous_close).abs()   # Absolute Low minus Previous Close
    
    # TR is the maximum of the three values for each period
    tr = hl.combine(hp, np.maximum).combine(lp, np.maximum)
    
    # Calculate ATR as the Simple Moving Average of TR over the specified length
    atr = tr.rolling(window=length).mean()
    
    # Return the ATR as a named pandas Series
    return atr

def setup_position(symbol):
    equity = get_balance_usdc()
    df = klines(symbol)
    df = calculate_atr(df)
    atr_value = df.round(1).iloc[-1]

    try:
        current_price = klines(symbol).iloc[-1]['close']
        stop_distance = atr_value * risk_atr_multiple
        
        # Use adjusted risk percentage with compensation factor
        adjusted_risk_percentage = target_risk_percentage * risk_adjustment_factor
        risk_amount = round(equity * adjusted_risk_percentage, 1)
        
        # Log the adjusted risk calculation
        print(f"{TEXT_COLORS['CYAN']}Risk Calculation:{TEXT_COLORS['RESET']}")
        print(f"Target risk: {target_risk_percentage * 100:.2f}%")
        print(f"Adjusted risk target: {adjusted_risk_percentage * 100:.2f}% (with {risk_adjustment_factor}x factor)")
        print(f"Risk amount: ${risk_amount:.2f}")
        
        position_size_raw = risk_amount / stop_distance
        qty_precision = get_qty_precision(symbol)[0]
        scaling_factor = 10 ** qty_precision
        
        # Try rounding instead of floor for better accuracy when possible
        position_size = round(position_size_raw * scaling_factor) / scaling_factor
        
        # Fall back to floor if rounding would make the position too large
        if position_size > position_size_raw * 1.05:  # If rounding increases by more than 5%
            position_size = math.floor(position_size_raw * scaling_factor) / scaling_factor
            print(f"{TEXT_COLORS['YELLOW']}Using floor instead of round for position size{TEXT_COLORS['RESET']}")

        if position_size < SAFE_MIN_SIZE:
            print(f"{TEXT_COLORS['RED']}Risk-based size {position_size:.8f} BTC is below safe minimum {SAFE_MIN_SIZE} BTC{TEXT_COLORS['RESET']}")
            print(f"{TEXT_COLORS['RED']}Need more equity... Stopping the bot...{TEXT_COLORS['RESET']}")
            sys.exit(1)
        else:
            leverage = 1

        position_value = position_size * current_price
        required_margin = position_value / leverage
        buffered_equity = equity * 0.99

        if required_margin > buffered_equity:
            min_leverage_needed = math.ceil(position_value / buffered_equity)
            if min_leverage_needed <= MAX_LEVERAGE:
                leverage = min_leverage_needed
            else:
                print(f"{TEXT_COLORS['RED']}Leverage is exceeding maximum allowable leverage... Stopping the bot...{TEXT_COLORS['RESET']}")
                sys.exit(1)

        # Calculate actual risk with leverage considered
        actual_risk = (position_size * stop_distance) / leverage
        risk_percentage_actual = (actual_risk / equity) * 100

        # Validate if actual risk is below minimum threshold
        if risk_percentage_actual < min_acceptable_risk * 100:
            print(f"{TEXT_COLORS['YELLOW']}WARNING: Actual risk ({risk_percentage_actual:.2f}%) is below minimum target ({min_acceptable_risk * 100:.2f}%){TEXT_COLORS['RESET']}")
            
            # Try to bump up position size if possible
            next_size = position_size + (1 / scaling_factor)
            next_position_value = next_size * current_price
            next_required_margin = next_position_value / leverage
            
            if next_required_margin <= buffered_equity:
                position_size = next_size
                position_value = next_position_value
                required_margin = next_required_margin
                # Recalculate actual risk
                actual_risk = (position_size * stop_distance) / leverage
                risk_percentage_actual = (actual_risk / equity) * 100
                print(f"{TEXT_COLORS['GREEN']}Increased position size to {position_size} BTC to achieve risk of {risk_percentage_actual:.2f}%{TEXT_COLORS['RESET']}")
            else:
                print(f"{TEXT_COLORS['YELLOW']}Cannot increase position size due to margin constraints{TEXT_COLORS['RESET']}")
        
        print(f"{TEXT_COLORS['GREEN']}Final actual risk: {risk_percentage_actual:.2f}% of equity{TEXT_COLORS['RESET']}")

        return stop_distance, position_size, leverage, position_value, required_margin, buffered_equity

    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error in setup_position: {str(e)}{TEXT_COLORS['RESET']}")
        traceback.print_exc()
        sys.exit(1)

@rate_limited
def set_mode(symbol, type):
    try:
        response = client.change_margin_type(
            symbol=symbol, marginType=type, recvWindow=6000
        )
        print(f"{TEXT_COLORS['GREEN']}Margin type set to {type} for {symbol}{TEXT_COLORS['RESET']}")
    except ClientError as error:
        if "No need to change margin type" in str(error):
            print(f"{TEXT_COLORS['GREEN']}Margin type already set to {type}{TEXT_COLORS['RESET']}")
        else:
            print(
                f"{TEXT_COLORS['RED']}Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}{TEXT_COLORS['RESET']}"
            )

def calculate_supertrend(df):
    data = df.copy()
    price_data = pd.DataFrame({"high": data.high, "low": data.low, "close": data.close})
            
    supertrend_df = supertrend_indicator(price_data, periods=supertrend_atr_period, multiplier=supertrend_factor, change_atr=True, src="hl2")
    del supertrend_df['atr']
    for col in ['trend', 'buySignal', 'sellSignal']:
        supertrend_df[col] = supertrend_df[col].fillna(method='ffill').fillna(0)

    return supertrend_df

def calculate_ssl_channel(df, period=ssl_period):
    """Calculate SSL Channel indicator."""
    df = df.copy()
    
    # Calculate moving averages of high and low
    df['sma_high'] = df['high'].rolling(window=period).mean()
    df['sma_low'] = df['low'].rolling(window=period).mean()
    
    # Initialize SSL column
    df['ssl'] = 0
    
    # Set SSL to 1 where Close > sma_high (uptrend), -1 where Close < sma_low (downtrend)
    df.loc[df['close'] > df['sma_high'], 'ssl'] = 1
    df.loc[df['close'] < df['sma_low'], 'ssl'] = -1
    
    # Fill forward to maintain the trend when neither condition is met
    df['ssl'] = df['ssl'].replace(0, np.nan).ffill().fillna(0)
    
    # Generate signals
    df['ssl_long_signal'] = (df['ssl'] == 1) & (df['ssl'].shift(1) == -1)
    df['ssl_short_signal'] = (df['ssl'] == -1) & (df['ssl'].shift(1) == 1)
    
    return df

def calculate_chop(df, length=chop_length):
    """Calculate Choppiness Index."""
    df = df.copy()
    
    # Calculate True Range
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    
    # Calculate the sum of TR over the lookback period
    sum_tr = true_range.rolling(window=length).sum()
    
    # Calculate highest high and lowest low over the lookback period
    highest_high = df['high'].rolling(window=length).max()
    lowest_low = df['low'].rolling(window=length).min()
    
    # Calculate range (highest high - lowest low)
    price_range = highest_high - lowest_low
    
    # Calculate Choppiness Index
    with np.errstate(divide='ignore', invalid='ignore'):
        chop = 100 * np.log10(sum_tr / price_range) / np.log10(length)
    
    # Handle infinity and NaN values
    df['chop'] = chop.replace([np.inf, -np.inf], np.nan).fillna(50)
    df['is_choppy'] = df['chop'] >= chop_threshold
    
    return df

def log_trade(trade_type, symbol, side, size, price, pnl=0.0, pnl_pct=0.0, additional_info=None):
    """
    Log a trade with details to both the trade history and the log file.
    
    Args:
        trade_type: Type of trade ('entry', 'exit', 'take_profit', 'stop_loss', etc.)
        symbol: Trading pair symbol
        side: Trade side ('buy', 'sell', 'long', 'short')
        size: Position size
        price: Execution price
        pnl: Realized profit/loss (for exits only)
        pnl_pct: Percentage profit/loss (for exits only)
        additional_info: Any additional information to log
    """
    global trade_history
    
    # Create trade record
    trade = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'type': trade_type,
        'symbol': symbol,
        'side': side,
        'size': size,
        'price': price,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'balance': get_balance_usdc(),
        'info': additional_info or {}
    }
    
    # Add to history
    trade_history.append(trade)
    
    # Determine color for log
    if trade_type == 'entry':
        color = TEXT_COLORS['GREEN'] if side in ['buy', 'long'] else TEXT_COLORS['RED']
    elif trade_type in ['exit', 'stop_loss']:
        color = TEXT_COLORS['RED']
    elif trade_type == 'take_profit':
        color = TEXT_COLORS['GREEN']
    else:
        color = TEXT_COLORS['RESET']
    
    # Create log message
    if trade_type == 'entry':
        log_message = f"{trade['timestamp']} | {color}{trade_type.upper()}{TEXT_COLORS['RESET']} | {color}{side.upper()}{TEXT_COLORS['RESET']} {size} {symbol} @ ${price:.2f}"
    elif trade_type in ['exit', 'stop_loss', 'take_profit']:
        pnl_color = TEXT_COLORS['GREEN'] if pnl >= 0 else TEXT_COLORS['RED']
        log_message = f"{trade['timestamp']} | {color}{trade_type.upper()}{TEXT_COLORS['RESET']} | {side.upper()} {size} {symbol} @ ${price:.2f} | PnL: {pnl_color}${pnl:.2f} ({pnl_pct:.2f}%){TEXT_COLORS['RESET']}"
    else:
        log_message = f"{trade['timestamp']} | {trade_type.upper()} | {side.upper()} {size} {symbol} @ ${price:.2f}"
    
    # Print to console
    print(log_message)
    
    # Write to log file
    try:
        with open(trade_log_file, 'a') as f:
            # Remove color codes for file
            clean_message = log_message
            for color in TEXT_COLORS.values():
                clean_message = clean_message.replace(color, '')
            f.write(clean_message + '\n')
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error writing to trade log: {str(e)}{TEXT_COLORS['RESET']}")
    
    # Save full trade history periodically
    if len(trade_history) % 5 == 0:
        save_trade_history()

def save_trade_history():
    """Save the trade history to JSON and CSV files."""
    try:
        # Save as JSON for complete data
        with open(os.path.join(log_dir, 'trade_history.json'), 'w') as f:
            json.dump(trade_history, f, indent=2)
        
        # Save as CSV for easy viewing
        try:
            import pandas as pd
            df = pd.DataFrame(trade_history)
            df.to_csv(os.path.join(log_dir, 'trade_history.csv'), index=False)
            print(f"Trade history saved ({len(trade_history)} records)")
        except ImportError:
            # If pandas is not available, just save the JSON
            print(f"Trade history saved to JSON ({len(trade_history)} records)")
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error saving trade history: {str(e)}{TEXT_COLORS['RESET']}")

def calculate_unrealized_pnl(position=None):
    """
    Calculate unrealized P&L for the current position.
    
    Args:
        position: Position data (optional, will fetch if not provided)
        
    Returns:
        Tuple of (unrealized_pnl, pnl_percentage)
    """
    if position is None:
        position = get_open_positions(symbol)
    
    if not position:
        return 0.0, 0.0
    
    unrealized_pnl = position['pnl']
    
    # Calculate percentage P&L
    if position['size'] > 0 and position['entry_price'] > 0:
        pnl_percentage = (unrealized_pnl / (position['entry_price'] * position['size'])) * 100
    else:
        pnl_percentage = 0.0
    
    return unrealized_pnl, pnl_percentage

def check_pnl_update():
    """Check if it's time to update P&L and do so if needed."""
    global last_pnl_update
    
    now = datetime.now()
    if (now - last_pnl_update).total_seconds() >= 300:  # 5 minutes = 300 seconds
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{TEXT_COLORS['GREEN']}=== 5-MINUTE P&L UPDATE [{timestamp}] ==={TEXT_COLORS['RESET']}")
        
        # Calculate and print current P&L
        position = get_open_positions(symbol)
        balance = get_balance_usdc()
        
        print(f"Current Balance: {TEXT_COLORS['GREEN']}{balance:.2f} USDC{TEXT_COLORS['RESET']}")
        
        if position:
            unrealized_pnl, pnl_pct = calculate_unrealized_pnl(position)
            
            pnl_color = TEXT_COLORS['GREEN'] if unrealized_pnl >= 0 else TEXT_COLORS['RED']
            print(f"Position: {position['side'].upper()} {position['size']} {symbol}")
            print(f"Entry Price: ${position['entry_price']:.2f}")
            print(f"Current Price: ${position['mark_price']:.2f}")
            print(f"Unrealized P&L: {pnl_color}${unrealized_pnl:.2f} ({pnl_pct:.2f}%){TEXT_COLORS['RESET']}")
            
            telegram.notify_pnl_update(
                symbol=symbol,
                side=position['side'],
                size=position['size'],
                entry_price=position['entry_price'],
                current_price=position['mark_price'],
                unrealized_pnl=unrealized_pnl,
                pnl_pct=pnl_pct,
                balance=balance,
                stop_loss=current_stop_loss,
                take_profit=current_take_profit if not first_tranche_closed else None,
                trailing_active=trailing_stop_activated
            )

            # Show risk management info if available
            if 'current_stop_loss' in globals() and current_stop_loss > 0:
                stop_distance = abs(position['mark_price'] - current_stop_loss)
                stop_pct = (stop_distance / position['mark_price']) * 100
                print(f"Stop Loss: ${current_stop_loss:.2f} ({stop_pct:.2f}% away)")
                
            if 'current_take_profit' in globals() and current_take_profit > 0 and not first_tranche_closed:
                tp_distance = abs(position['mark_price'] - current_take_profit)
                tp_pct = (tp_distance / position['mark_price']) * 100
                print(f"Take Profit: ${current_take_profit:.2f} ({tp_pct:.2f}% away)")
                
            if 'trailing_stop_activated' in globals() and trailing_stop_activated:
                print(f"Trailing Stop: {TEXT_COLORS['GREEN']}ACTIVE{TEXT_COLORS['RESET']}")
        else:
            print("No open position")
        
        # Generate a full P&L summary occasionally (every 6 hours)
        hour = now.hour
        if hour % 6 == 0 and now.minute < 5:
            generate_pnl_summary()
            
        # Update last PnL update time
        last_pnl_update = now
        next_update_time = (now + timedelta(minutes=5)).strftime('%H:%M:%S')
        print(f"Next P&L update at: {next_update_time}")

def generate_pnl_summary():
    """Generate a comprehensive P&L summary."""
    if not trade_history:
        print(f"{TEXT_COLORS['YELLOW']}No trade history available to generate P&L summary{TEXT_COLORS['RESET']}")
        return
    
    try:
        # Calculate overall statistics
        closed_trades = [t for t in trade_history if t['type'] in ['exit', 'stop_loss', 'take_profit']]
        total_trades = len(closed_trades)
        winning_trades = len([t for t in closed_trades if t['pnl'] > 0])
        losing_trades = len([t for t in closed_trades if t['pnl'] < 0])
        
        if total_trades == 0:
            print(f"{TEXT_COLORS['YELLOW']}No closed trades yet to generate P&L summary{TEXT_COLORS['RESET']}")
            return
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        total_profit = sum([t['pnl'] for t in closed_trades if t['pnl'] > 0])
        total_loss = sum([t['pnl'] for t in closed_trades if t['pnl'] < 0])
        net_pnl = total_profit + total_loss
        
        avg_win = total_profit / winning_trades if winning_trades > 0 else 0
        avg_loss = total_loss / losing_trades if losing_trades > 0 else 0
        profit_factor = abs(total_profit / total_loss) if total_loss != 0 else float('inf')
        
        # Get current unrealized P&L
        unrealized_pnl, _ = calculate_unrealized_pnl()
        
        # Calculate starting equity and current equity
        starting_equity = trade_history[0]['balance'] if trade_history else 0
        current_equity = get_balance_usdc()
        equity_growth = ((current_equity / starting_equity) - 1) * 100 if starting_equity > 0 else 0
        
        # Format the summary
        separator = "═" * 50
        summary = [
            f"\n{separator}",
            f"TRADING PERFORMANCE SUMMARY",
            f"{separator}",
            f"Starting Equity: ${starting_equity:.2f}",
            f"Current Equity: ${current_equity:.2f} ({equity_growth:+.2f}%)",
            f"Net Realized P&L: ${net_pnl:.2f}",
            f"Current Unrealized P&L: ${unrealized_pnl:.2f}" if unrealized_pnl != 0 else "",
            f"Total P&L: ${(net_pnl + unrealized_pnl):.2f}",
            f"\nTRADE STATISTICS:",
            f"Total Trades: {total_trades}",
            f"Win Rate: {win_rate:.2f}%",
            f"Profit Factor: {profit_factor:.2f}",
            f"Average Win: ${avg_win:.2f}",
            f"Average Loss: ${avg_loss:.2f}",
            f"\nRECENT TRADES:"
        ]
        
        # Add recent trades
        recent_trades = closed_trades[-5:] if len(closed_trades) >= 5 else closed_trades
        for i, trade in enumerate(reversed(recent_trades)):
            pnl_str = f"${trade['pnl']:.2f} ({trade['pnl_pct']:.2f}%)"
            summary.append(f"{i+1}. {trade['timestamp']} | {trade['type'].upper()} | {trade['side'].upper()} {trade['size']} {trade['symbol']} @ ${trade['price']:.2f} | P&L: {pnl_str}")
        
        summary.append(separator)
        
        # Print the summary with colors
        for line in summary:
            if "P&L: $" in line:
                # Extract the P&L value to determine color
                pnl_val = line.split("P&L: $")[1].split()[0]
                try:
                    pnl_val = float(pnl_val)
                    if pnl_val > 0:
                        print(f"{TEXT_COLORS['GREEN']}{line}{TEXT_COLORS['RESET']}")
                    elif pnl_val < 0:
                        print(f"{TEXT_COLORS['RED']}{line}{TEXT_COLORS['RESET']}")
                    else:
                        print(line)
                except:
                    print(line)
            else:
                print(line)
        
        # Save the summary to a file with no colors

        try:
            with open(pnl_summary_file, 'w', encoding='utf-8') as f:  # Add encoding='utf-8' here
                for line in summary:
                    f.write(line + '\n')
            print(f"P&L summary saved to {pnl_summary_file}")
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error saving P&L summary: {str(e)}{TEXT_COLORS['RESET']}")
    
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error generating P&L summary: {str(e)}{TEXT_COLORS['RESET']}")

def signal_strategy(symbol):
    """SuperTrend + SSL Channel strategy signal."""
    global prev_supertrend, prev_ssl, st_reset_detected
    
    # Fetch candle data
    kl = klines(symbol)

    # Calculate indicators
    df = calculate_supertrend(kl)
    df['atr'] = calculate_atr(df)
    df = calculate_ssl_channel(df)
    df = calculate_chop(df)

    # Get latest values
    st_trend = df['trend'].iloc[-1]
    ssl = df['ssl'].iloc[-1]
    st_buy_signal = df['buySignal'].iloc[-1]
    st_sell_signal = df['sellSignal'].iloc[-1]
    ssl_long_signal = df['ssl_long_signal'].iloc[-1]
    ssl_short_signal = df['ssl_short_signal'].iloc[-1]
    is_choppy = df['is_choppy'].iloc[-1]
    chop_value = df['chop'].iloc[-1]
    atr_val = df['atr'].iloc[-1]
    current_price = df['close'].iloc[-1]
    
    # Get SuperTrend upper and lower bands
    st_upper = df['up'].iloc[-1]
    st_lower = df['dn'].iloc[-1]
    
    # Get SSL Channel upper and lower bands
    ssl_upper = df['sma_high'].iloc[-1]
    ssl_lower = df['sma_low'].iloc[-1]

    # Print detailed indicator values
    print(f"\n{TEXT_COLORS['CYAN']}INDICATOR VALUES:{TEXT_COLORS['RESET']}")
    print(f"Current Price: ${current_price:.2f}")
    print(f"ATR: ${atr_val:.2f}")
    
    print(f"\n{TEXT_COLORS['CYAN']}SSL CHANNEL:{TEXT_COLORS['RESET']}")
    print(f"SSL Direction: {TEXT_COLORS['GREEN'] if ssl == 1 else TEXT_COLORS['RED']}{ssl:+.0f}{TEXT_COLORS['RESET']} ({'Uptrend' if ssl == 1 else 'Downtrend' if ssl == -1 else 'Neutral'})")
    print(f"SSL Upper Band: ${ssl_upper:.2f}")
    print(f"SSL Lower Band: ${ssl_lower:.2f}")
    print(f"SSL Long Signal: {TEXT_COLORS['GREEN'] if ssl_long_signal else TEXT_COLORS['RESET']}{ssl_long_signal}{TEXT_COLORS['RESET']}")
    print(f"SSL Short Signal: {TEXT_COLORS['RED'] if ssl_short_signal else TEXT_COLORS['RESET']}{ssl_short_signal}{TEXT_COLORS['RESET']}")
    
    print(f"\n{TEXT_COLORS['CYAN']}SUPERTREND:{TEXT_COLORS['RESET']}")
    print(f"SuperTrend: {TEXT_COLORS['GREEN'] if st_trend == 1 else TEXT_COLORS['RED']}{st_trend:+d}{TEXT_COLORS['RESET']} ({'Uptrend' if st_trend == 1 else 'Downtrend'})")
    print(f"SuperTrend Upper Band: ${st_upper:.2f}")
    print(f"SuperTrend Lower Band: ${st_lower:.2f}")
    print(f"SuperTrend Buy Signal: {TEXT_COLORS['GREEN'] if st_buy_signal else TEXT_COLORS['RESET']}{st_buy_signal}{TEXT_COLORS['RESET']}")
    print(f"SuperTrend Sell Signal: {TEXT_COLORS['RED'] if st_sell_signal else TEXT_COLORS['RESET']}{st_sell_signal}{TEXT_COLORS['RESET']}")
    
    print(f"\n{TEXT_COLORS['CYAN']}MARKET CONDITIONS:{TEXT_COLORS['RESET']}")
    print(f"Choppiness: {TEXT_COLORS['YELLOW'] if is_choppy else TEXT_COLORS['GREEN']}{chop_value:.2f}{TEXT_COLORS['RESET']} ({'Choppy' if is_choppy else 'Trending'})")

    # Check for trend flips
    supertrend_up_flip = st_buy_signal or (prev_supertrend == -1 and st_trend == 1)
    supertrend_down_flip = st_sell_signal or (prev_supertrend == 1 and st_trend == -1)
    ssl_up_flip = ssl_long_signal or (prev_ssl == -1 and ssl == 1)
    ssl_down_flip = ssl_short_signal or (prev_ssl == 1 and ssl == -1)
    
    # Log trend flips
    if supertrend_up_flip:
        print(f"{TEXT_COLORS['GREEN']}SuperTrend flip to UPTREND detected{TEXT_COLORS['RESET']}")
    if supertrend_down_flip:
        print(f"{TEXT_COLORS['RED']}SuperTrend flip to DOWNTREND detected{TEXT_COLORS['RESET']}")
    if ssl_up_flip:
        print(f"{TEXT_COLORS['GREEN']}SSL flip to UPTREND detected{TEXT_COLORS['RESET']}")
    if ssl_down_flip:
        print(f"{TEXT_COLORS['RED']}SSL flip to DOWNTREND detected{TEXT_COLORS['RESET']}")
    
    # Check for SuperTrend reset
    st_reset_detected = st_buy_signal or st_sell_signal
    print(f"SuperTrend Reset Detected: {TEXT_COLORS['YELLOW'] if st_reset_detected else TEXT_COLORS['RESET']}{st_reset_detected}{TEXT_COLORS['RESET']}")

    # Entry conditions
    long_entry = (ssl_up_flip and st_trend == 1) or (supertrend_up_flip and ssl == 1)
    short_entry = (ssl_down_flip and st_trend == -1) or (supertrend_down_flip and ssl == -1)
    
    # Log choppiness
    if is_choppy:
        print(f"{TEXT_COLORS['YELLOW']}Market is choppy (Chop: {chop_value:.2f}){TEXT_COLORS['RESET']}")
    else:
        print(f"{TEXT_COLORS['GREEN']}Market is trending (Chop: {chop_value:.2f}){TEXT_COLORS['RESET']}")
    
    # Reject signals if market is choppy and SuperTrend just reset
    if long_entry and st_reset_detected and is_choppy:
        print(f"{TEXT_COLORS['YELLOW']}LONG signal detected but market too choppy - REJECTED{TEXT_COLORS['RESET']}")
        long_entry = False
    
    if short_entry and st_reset_detected and is_choppy:
        print(f"{TEXT_COLORS['RED']}SHORT signal detected but market too choppy - REJECTED{TEXT_COLORS['RESET']}")
        short_entry = False
    
    # Update state
    prev_supertrend = st_trend
    prev_ssl = ssl

    # Print verdict
    print(f"\n{TEXT_COLORS['CYAN']}VERDICT:{TEXT_COLORS['RESET']}")
    
    # Log signal
    if long_entry:
        entry_reason = 'SSL flip + ST uptrend' if ssl_up_flip else 'ST flip + SSL uptrend'
        print(f"{TEXT_COLORS['GREEN']}LONG ENTRY SIGNAL: {entry_reason}{TEXT_COLORS['RESET']}")
        return 'up', atr_val, current_price
    elif short_entry:
        entry_reason = 'SSL flip + ST downtrend' if ssl_down_flip else 'ST flip + SSL downtrend'
        print(f"{TEXT_COLORS['RED']}SHORT ENTRY SIGNAL: {entry_reason}{TEXT_COLORS['RESET']}")
        return 'down', atr_val, current_price
    else:
        # Explain why no entry signal
        reasons = []
        # Check conditions for long entry
        if not ((ssl_up_flip and st_trend == 1) or (supertrend_up_flip and ssl == 1)):
            if not ssl_up_flip and not supertrend_up_flip:
                reasons.append("No bullish indicator flips")
            elif ssl_up_flip and st_trend != 1:
                reasons.append("SSL flipped bullish but SuperTrend is bearish")
            elif supertrend_up_flip and ssl != 1:
                reasons.append("SuperTrend flipped bullish but SSL is bearish")
        elif st_reset_detected and is_choppy:
            reasons.append("Signal rejected due to choppy market during SuperTrend reset")
            
        # Check conditions for short entry
        if not ((ssl_down_flip and st_trend == -1) or (supertrend_down_flip and ssl == -1)):
            if not ssl_down_flip and not supertrend_down_flip:
                if not any(r == "No bullish indicator flips" for r in reasons):
                    reasons.append("No bearish indicator flips")
            elif ssl_down_flip and st_trend != -1:
                reasons.append("SSL flipped bearish but SuperTrend is bullish")
            elif supertrend_down_flip and ssl != -1:
                reasons.append("SuperTrend flipped bearish but SSL is bullish")
        elif st_reset_detected and is_choppy:
            if not any(r == "Signal rejected due to choppy market during SuperTrend reset" for r in reasons):
                reasons.append("Signal rejected due to choppy market during SuperTrend reset")
        
        if not reasons:
            reasons.append("Indicators are not aligned for entry")
            
        print(f"{TEXT_COLORS['YELLOW']}NO ENTRY SIGNAL: {', '.join(reasons)}{TEXT_COLORS['RESET']}")
        print(f"Be patient, no signal right now...")
        
        return 'none', atr_val, current_price
    
@rate_limited
def get_open_positions(symbol):
    """Get open positions for the given symbol."""
    try:
        positions = client.get_position_risk()
        for pos in positions:
            if pos['symbol'] == symbol and float(pos['positionAmt']) != 0:
                side = 'long' if float(pos['positionAmt']) > 0 else 'short'
                size = abs(float(pos['positionAmt']))
                entry_price = float(pos['entryPrice'])
                mark_price = float(pos['markPrice'])
                pnl = float(pos['unRealizedProfit'])
                
                return {
                    'symbol': symbol,
                    'side': side,
                    'size': size,
                    'entry_price': entry_price,
                    'mark_price': mark_price,
                    'pnl': pnl
                }
        return None
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}Error checking positions: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        return None

def check_exit_signals(symbol, position):
    """Check if we should exit the position based on signals."""
    global first_tranche_closed, trailing_stop_activated, current_stop_loss
    
    # Get candle data and calculate indicators
    df = klines(symbol)
    df = calculate_supertrend(df)
    
    current_price = df['close'].iloc[-1]
    
    # Check for SuperTrend flip exit
    if position['side'] == 'long' and df['sellSignal'].iloc[-1]:
        print(f"{TEXT_COLORS['CYAN']}EXIT SIGNAL: SuperTrend flipped to downtrend{TEXT_COLORS['RESET']}")
        return 'exit'
    elif position['side'] == 'short' and df['buySignal'].iloc[-1]:
        print(f"{TEXT_COLORS['CYAN']}EXIT SIGNAL: SuperTrend flipped to uptrend{TEXT_COLORS['RESET']}")
        return 'exit'
    
    # Check trailing stop update if activated
    if trailing_stop_activated:
        if position['side'] == 'long':
            new_stop = current_price - (trailing_atr_multiple * position_entry_atr)
            if new_stop > current_stop_loss:
                current_stop_loss = new_stop
                print(f"{TEXT_COLORS['GREEN']}TRAILING STOP UPDATED to ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
                return 'update_stop'
        else:  # short position
            new_stop = current_price + (trailing_atr_multiple * position_entry_atr)
            if new_stop < current_stop_loss:
                current_stop_loss = new_stop
                print(f"{TEXT_COLORS['GREEN']}TRAILING STOP UPDATED to ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
                return 'update_stop'
    
    # Check if we should activate trailing stop (after first take profit hit)
    elif first_tranche_closed and not trailing_stop_activated:
        if position['side'] == 'long' and current_price >= (position_entry_price + (trailing_atr_trigger * position_entry_atr)):
            trailing_stop_activated = True
            current_stop_loss = current_price - (trailing_atr_multiple * position_entry_atr)
            print(f"{TEXT_COLORS['GREEN']}TRAILING STOP ACTIVATED at ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
            
            telegram.notify_trailing_stop_activated(
                symbol=symbol,
                side=position['side'],
                current_price=current_price,
                stop_price=current_stop_loss,
                distance_pct=(abs(current_price - current_stop_loss) / current_price) * 100
            )
            
            return 'trailing_activated'
        elif position['side'] == 'short' and current_price <= (position_entry_price - (trailing_atr_trigger * position_entry_atr)):
            trailing_stop_activated = True
            current_stop_loss = current_price + (trailing_atr_multiple * position_entry_atr)
            print(f"{TEXT_COLORS['GREEN']}TRAILING STOP ACTIVATED at ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
            return 'trailing_activated'
    
    return None

@rate_limited
def open_order(symbol, side, atr_value, current_price):

    global position_entry_price, position_entry_atr, current_stop_loss, current_take_profit, position_size
    
    # Get account balance for risk calculation (rounded down to 2 decimal places)
    equity = get_balance_usdc()
    if equity is None or equity <= 0:
        print(f"{TEXT_COLORS['RED']}ERROR: Please check account balance{TEXT_COLORS['RESET']}")
        sys.exit(1)
        return False
    
    # Calculate stop loss distance in price
    stop_distance = setup_position(symbol)[0]

    # Security check - ensure we have a valid stop distance
    if stop_distance <= 0:
        stop_distance = current_price * 0.01  # Use 1% of price as fallback
        print(f"{TEXT_COLORS['YELLOW']}WARNING: Using fallback stop distance: ${stop_distance:.2f}{TEXT_COLORS['RESET']}")

    # Calculate stop loss price
    if side == 'buy':
        price_precision = get_price_precision('BTCUSDC')
        stop_price = round(current_price - stop_distance, price_precision)
    else:
        price_precision = get_price_precision('BTCUSDC')
        stop_price = round(current_price + stop_distance, price_precision)
   
    position_size = setup_position(symbol)[1]
    leverage = setup_position(symbol)[2]
    position_value = setup_position(symbol)[3]
    required_margin = setup_position(symbol)[4]
    safe_equity = setup_position(symbol)[5]

    # Calculate actual risk in dollars for reporting
    actual_risk_dollars = position_size * (stop_distance / leverage)
    
    # Calculate take profit level
    price_precision = get_price_precision(symbol)
    if side == 'buy':
        tp_price = round(current_price + (tp_atr_multiple * atr_value), price_precision)
    else:
        tp_price = round(current_price - (tp_atr_multiple * atr_value), price_precision)

    # Print order details
    print(f"\n{TEXT_COLORS['CYAN']}ORDER DETAILS:{TEXT_COLORS['RESET']}")
    print(f"Symbol: {symbol}")
    print(f"Side: {TEXT_COLORS['GREEN'] if side == 'buy' else TEXT_COLORS['RED']}{side.upper()}{TEXT_COLORS['RESET']}")
    print(f"Price: ${current_price:.2f}")
    print(f"Stop Loss: ${stop_price:.2f} ({risk_atr_multiple}x ATR)")
    print(f"Take Profit: ${tp_price:.2f} ({tp_atr_multiple}x ATR)")
    print(f"Position Size: {position_size} BTC (${position_value:.2f})")
    print(f"Leverage: {leverage}x")
    print(f"Required Margin: ${required_margin:.2f} of ${safe_equity:.2f} available")
    print(f"Actual Risk: ${actual_risk_dollars:.2f} ({(actual_risk_dollars/equity)*100:.2f}% of equity)")

    # Place the order
    try:
        # Set proper leverage and margin type
        set_mode(symbol, type)
        sleep(1)
        set_leverage(symbol, leverage)
        sleep(1)
         
        if side == 'buy':
            # Place market buy order
            resp1 = client.new_order(
                symbol=symbol, 
                side='BUY', 
                type='MARKET',
                quantity=position_size
            )
            print(f"{TEXT_COLORS['GREEN']}BUY order placed at market price{TEXT_COLORS['RESET']}")
            sleep(1)
            
            # Place stop loss order
            price_precision = get_price_precision('BTCUSDC')
            stop_price = round(stop_price, price_precision)
            stop_loss_order = client.new_order(
                symbol=symbol, 
                side='SELL', 
                type='STOP_MARKET', 
                quantity=position_size, 
                stopPrice=stop_price,
                reduceOnly='true'
            )
            print(f"{TEXT_COLORS['YELLOW']}Stop loss set at ${stop_price:.2f}{TEXT_COLORS['RESET']}")
            sleep(1)
            
            # Place take profit order for half position
            price_precision = get_price_precision('BTCUSDC')
            tp_price = round(tp_price, price_precision)
            qty_precision = get_qty_precision(symbol)[0]
            half_size = round(position_size / 2, qty_precision)
            take_profit_order = client.new_order(
                symbol=symbol, 
                side='SELL', 
                type='TAKE_PROFIT_MARKET', 
                quantity=half_size,
                stopPrice=tp_price,
                reduceOnly='true'
            )
            print(f"{TEXT_COLORS['GREEN']}Take profit set at ${tp_price:.2f} for {half_size} BTC{TEXT_COLORS['RESET']}")
            
            # Save position details
            position_entry_price = current_price
            position_entry_atr = atr_value
            current_stop_loss = stop_price
            current_take_profit = tp_price
           
        else:  # side == 'sell'
            # Place market sell order
            resp1 = client.new_order(
                symbol=symbol, 
                side='SELL', 
                type='MARKET',
                quantity=position_size
            )
            print(f"{TEXT_COLORS['RED']}SELL order placed at market price{TEXT_COLORS['RESET']}")
            sleep(1)
            
            # Place stop loss order
            stop_loss_order = client.new_order(
                symbol=symbol, 
                side='BUY', 
                type='STOP_MARKET', 
                quantity=position_size, 
                stopPrice=stop_price,
                reduceOnly='true'
            )
            print(f"{TEXT_COLORS['YELLOW']}Stop loss set at ${stop_price:.2f}{TEXT_COLORS['RESET']}")
            sleep(1)
           
            # Place take profit order for half position
            qty_precision = get_qty_precision(symbol)[0]
            half_size = round(position_size / 2, qty_precision)
            take_profit_order = client.new_order(
                symbol=symbol, 
                side='BUY', 
                type='TAKE_PROFIT_MARKET', 
                quantity=half_size,
                stopPrice=tp_price,
                reduceOnly='true'
            )
            print(f"{TEXT_COLORS['GREEN']}Take profit set at ${tp_price:.2f} for {half_size} BTC{TEXT_COLORS['RESET']}")
              
            # Save position details
            position_entry_price = current_price
            position_entry_atr = atr_value
            current_stop_loss = stop_price
            current_take_profit = tp_price
           
        # Log the trade
        log_trade('entry', symbol, side, position_size, current_price, 
                  additional_info={'leverage': leverage, 'stop_loss': stop_price, 'take_profit': tp_price})

        telegram.notify_trade_entry(
            symbol=symbol,
            side=side,
            price=current_price,
            size=position_size,
            stop_loss=stop_price,
            take_profit=tp_price,
            risk_amount=actual_risk_dollars,
            leverage=leverage
        )

        return True
        
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}ERROR placing order: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        return False

@rate_limited
def close_position(symbol, position):
    """Close an open position."""
    try:
        # Cancel all open orders for this symbol first
        client.cancel_open_orders(symbol=symbol)
        print(f"{TEXT_COLORS['YELLOW']}Canceled all open orders for {symbol}{TEXT_COLORS['RESET']}")
        
        # Close the position with a market order
        side = 'SELL' if position['side'] == 'long' else 'BUY'
        client.new_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=position['size'],
            reduceOnly='true'
        )
        print(f"{TEXT_COLORS['RED']}Closed {position['side'].upper()} position of {position['size']} BTC at market price{TEXT_COLORS['RESET']}")
        
        # Log the trade
        pnl = position['pnl']
        pnl_pct = (pnl / (position['entry_price'] * position['size'])) * 100 if position['entry_price'] > 0 else 0
        exit_side = 'sell' if position['side'] == 'long' else 'buy'
        log_trade('exit', symbol, exit_side, position['size'], position['mark_price'], pnl, pnl_pct)
        
        telegram.notify_trade_exit(
            symbol=symbol,
            side=position['side'],
            price=position['mark_price'],
            size=position['size'],
            pnl=pnl,
            pnl_pct=pnl_pct
        )

        # Reset position tracking
        global first_tranche_closed, trailing_stop_activated
        first_tranche_closed = False
        trailing_stop_activated = False
        
        return True
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}Error closing position: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        return False

@rate_limited
def update_stop_loss(symbol, position):
    """Update the stop loss order."""
    try:
        # Cancel existing stop orders
        orders = client.get_open_orders(symbol=symbol)
        for order in orders:
            if 'STOP' in order['type'] and 'orderId' in order and order['orderId']:
                try:
                    client.cancel_order(symbol=symbol, orderId=order['orderId'])
                    print(f"{TEXT_COLORS['YELLOW']}Canceled old stop order (ID: {order['orderId']}){TEXT_COLORS['RESET']}")
                except ClientError as cancel_error:
                    if "Unknown order" in str(cancel_error) or "Order does not exist" in str(cancel_error):
                        print(f"{TEXT_COLORS['YELLOW']}Order already cancelled or filled: {order['orderId']}{TEXT_COLORS['RESET']}")
                    else:
                        print(f"{TEXT_COLORS['RED']}Error cancelling order: {cancel_error.error_code} - {cancel_error.error_message}{TEXT_COLORS['RESET']}")
            elif 'STOP' in order['type']:
                print(f"{TEXT_COLORS['YELLOW']}Found STOP order without valid orderId, skipping cancel{TEXT_COLORS['RESET']}")
        
        # Place new stop order
        side = 'SELL' if position['side'] == 'long' else 'BUY'
        new_order = client.new_order(
            symbol=symbol,
            side=side,
            type='STOP_MARKET',
            quantity=position['size'],
            stopPrice=current_stop_loss,
            reduceOnly='true'
        )
        print(f"{TEXT_COLORS['GREEN']}New stop loss order placed at ${current_stop_loss:.2f} (Order ID: {new_order['orderId']}){TEXT_COLORS['RESET']}")
        
        # If this is called after a take profit hit, log it
        global first_tranche_closed
        if not first_tranche_closed:
            first_tranche_closed = True
            
            # Calculate the half size that was closed
            half_size = position['size']  # This is already half the original size
            
            # Calculate P&L
            if position['side'] == 'long':
                pnl = (position['mark_price'] - position_entry_price) * half_size
                pnl_pct = ((position['mark_price'] / position_entry_price) - 1) * 100
            else:  # short
                pnl = (position_entry_price - position['mark_price']) * half_size
                pnl_pct = ((position_entry_price / position['mark_price']) - 1) * 100
            
            # Log the take profit
            log_trade('take_profit', symbol, 'sell' if position['side'] == 'long' else 'buy', 
                    half_size, position['mark_price'], pnl, pnl_pct)
            
            telegram.notify_take_profit_hit(
                symbol=symbol,
                side=position['side'],
                price=position['mark_price'],
                size=half_size,
                profit=pnl,
                profit_pct=pnl_pct,
                remaining_size=position['size'],
                new_stop_loss=current_stop_loss
            )

            print(f"{TEXT_COLORS['GREEN']}Take profit hit! Stop loss moved to breakeven at ${position_entry_price:.2f}{TEXT_COLORS['RESET']}")
        
        return True
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}Error updating stop loss: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        return False

def get_current_stop_loss():
    """Provide access to the current stop loss value for OrderMonitor."""
    global current_stop_loss
    return current_stop_loss

def handle_take_profit_fill(symbol, position, half_size):
    """Handle when a take profit order is filled."""
    global first_tranche_closed, current_stop_loss
    
    # Print detailed debug information
    print(f"{TEXT_COLORS['YELLOW']}===== TAKE PROFIT HIT - DEBUG INFO ====={TEXT_COLORS['RESET']}")
    print(f"Current first_tranche_closed flag: {first_tranche_closed}")
    print(f"Current stop loss: ${current_stop_loss:.2f}")
    print(f"Position entry price: ${position_entry_price:.2f}")
    print(f"Position size: {position['size']} BTC")
    print(f"Position side: {position['side']}")
    
    # If already handled, log but don't continue
    if first_tranche_closed:
        print(f"{TEXT_COLORS['YELLOW']}Take profit already processed previously. No action needed.{TEXT_COLORS['RESET']}")
        return
    
    # Update state
    first_tranche_closed = True
    
    # Move stop loss to breakeven
    old_stop_loss = current_stop_loss
    current_stop_loss = position_entry_price
    
    print(f"{TEXT_COLORS['GREEN']}First tranche flag set to TRUE{TEXT_COLORS['RESET']}")
    print(f"{TEXT_COLORS['GREEN']}Stop loss moved from ${old_stop_loss:.2f} to ${current_stop_loss:.2f} (breakeven){TEXT_COLORS['RESET']}")
    
    # Calculate P&L for the closed portion
    if position['side'] == 'long':
        pnl = (position['mark_price'] - position_entry_price) * half_size
        pnl_pct = ((position['mark_price'] / position_entry_price) - 1) * 100
    else:  # short
        pnl = (position_entry_price - position['mark_price']) * half_size
        pnl_pct = ((position_entry_price / position['mark_price']) - 1) * 100
    
    # Log the take profit
    log_trade('take_profit', symbol, 'sell' if position['side'] == 'long' else 'buy', 
              half_size, position['mark_price'], pnl, pnl_pct)
    
    print(f"{TEXT_COLORS['GREEN']}Take profit hit! Stop loss moved to breakeven at ${position_entry_price:.2f}{TEXT_COLORS['RESET']}")

def verify_connectivity(max_retries=3, retry_delay=5):
    """
    Verify connectivity to Binance API before starting the trading loop.
    Returns True if connection is established, False otherwise.
    """
    print(f"\n{TEXT_COLORS['CYAN']}Verifying Binance API connectivity...{TEXT_COLORS['RESET']}")
    
    for attempt in range(1, max_retries + 1):
        try:
            # Try to get server time
            server_time = client.time()
            # Try to get account balance
            balance = get_balance_usdc()
            
            if balance is not None:
                print(f"{TEXT_COLORS['GREEN']}Connection successful! Server time: {datetime.fromtimestamp(server_time['serverTime'] / 1000)}{TEXT_COLORS['RESET']}")
                print(f"{TEXT_COLORS['GREEN']}Account balance: {balance:.2f} USDC{TEXT_COLORS['RESET']}")
                return True
                
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Connection attempt {attempt}/{max_retries} failed: {str(e)}{TEXT_COLORS['RESET']}")
        
        if attempt < max_retries:
            print(f"Retrying in {retry_delay} seconds...")
            sleep(retry_delay)
    
    print(f"{TEXT_COLORS['RED']}Failed to connect to Binance API after {max_retries} attempts.{TEXT_COLORS['RESET']}")
    return False

# Main trading loop
symbol = 'BTCUSDC'
cycle_count = 0

print(f"\n{TEXT_COLORS['GREEN']}=== THE BOY - TRADING BOT ==={TEXT_COLORS['RESET']}")
print(f"{TEXT_COLORS['GREEN']}Strategy Parameters:{TEXT_COLORS['RESET']}")
print(f"Timeframe: {timeframe}")
print(f"Risk: {target_risk_percentage*100}% of equity")
print(f"Stop Loss: {risk_atr_multiple}x ATR")
print(f"Take Profit: {tp_atr_multiple}x ATR")
print(f"SuperTrend Period: {supertrend_atr_period}")
print(f"SuperTrend Factor: {supertrend_factor}")
print(f"SSL Period: {ssl_period}")
print(f"Chop Length: {chop_length}")
print(f"Chop Threshold: {chop_threshold}")
print(f"Margin Type: {type}")
print(f"Dynamic leverage calculation: Enabled\n")

# Import the OrderMonitor class at the top of your file

# Function to check if first tranche is closed (to pass as reference)
def is_first_tranche_closed():
    global first_tranche_closed
    return first_tranche_closed

# Set up the handler functions dictionary
order_monitor_handlers = {
    'get_open_positions': get_open_positions,
    'handle_take_profit_fill': handle_take_profit_fill,
    'update_stop_loss': update_stop_loss
}

if not verify_connectivity():
    print(f"{TEXT_COLORS['RED']}Exiting due to connectivity issues...{TEXT_COLORS['RESET']}")
    sys.exit(1)

# Initialize and start the order monitor after verifying connectivity
from order_monitor import OrderMonitor
order_monitor = OrderMonitor(client, rate_limiter, order_monitor_handlers, get_current_stop_loss)
order_monitor.start(symbol, is_first_tranche_closed)
print(f"{TEXT_COLORS['GREEN']}Real-time order monitoring started{TEXT_COLORS['RESET']}")

while True:
    try:
        cycle_count += 1
        print(f"\n=== CYCLE #{cycle_count} ===")
        
        # Check balance
        equity = get_balance_usdc()
        if equity is None:
            print(f"{TEXT_COLORS['RED']}Cannot connect to API. Check IP, restrictions or wait{TEXT_COLORS['RESET']}")
            sleep(60)
            continue
        
        print(f"Balance: {TEXT_COLORS['GREEN']}{equity:.2f} USDC{TEXT_COLORS['RESET']}")
        
        # Check if we have an open position
        position = get_open_positions(symbol)
        
        if position:
            # Display position information
            side_color = TEXT_COLORS['GREEN'] if position['side'] == 'long' else TEXT_COLORS['RED']
            pnl_color = TEXT_COLORS['GREEN'] if position['pnl'] > 0 else TEXT_COLORS['RED']
            pnl_pct = (position['pnl'] / (position['entry_price'] * position['size'])) * 100
            
            print(f"\n{TEXT_COLORS['GREEN']}ACTIVE POSITION:{TEXT_COLORS['RESET']}")
            print(f"Symbol: {position['symbol']}")
            print(f"Side: {side_color}{position['side'].upper()}{TEXT_COLORS['RESET']}")
            print(f"Size: {position['size']} BTC")
            print(f"Entry Price: ${position['entry_price']:.2f}")
            print(f"Current Price: ${position['mark_price']:.2f}")
            print(f"Unrealized PnL: {pnl_color}${position['pnl']:.2f} ({pnl_pct:.2f}%){TEXT_COLORS['RESET']}")
            
            if first_tranche_closed:
                print(f"Take Profit: {TEXT_COLORS['GREEN']}First target hit{TEXT_COLORS['RESET']}")
            else:
                print(f"Take Profit: ${current_take_profit:.2f}")
                
            print(f"Stop Loss: ${current_stop_loss:.2f}")
            
            if trailing_stop_activated:
                print(f"Trailing Stop: {TEXT_COLORS['GREEN']}ACTIVE{TEXT_COLORS['RESET']}")
            
            # Check if any open orders got filled

            try:
                closed_orders = client.get_all_orders(symbol=symbol, limit=10)
                take_profit_filled = False
                
                # Store previous position size for comparison
                previous_position_size = position['size']
                
                # Check for filled take profit orders
                for order in closed_orders:
                    if (order['status'] == 'FILLED' and 
                        'TAKE_PROFIT' in order['type'] and 
                        not first_tranche_closed):
                        take_profit_filled = True
                        half_size = position['size']  # Half of original size
                        handle_take_profit_fill(symbol, position, half_size)
                        update_stop_loss(symbol, position)
                        break
                
                # Additional check - if position size is roughly half what it was at entry
                # This serves as a backup detection method for take profit fills
                if not first_tranche_closed and not take_profit_filled:
                    # Get the initial position size (this is saved during entry)
                    initial_position_size = position_size if 'position_size' in globals() else position['size'] * 2
                    
                    # If current size is close to half of initial size (with tolerance for rounding)
                    if position['size'] <= initial_position_size * 0.6:
                        print(f"{TEXT_COLORS['YELLOW']}Position size decreased significantly. Take profit likely hit.{TEXT_COLORS['RESET']}")
                        print(f"{TEXT_COLORS['GREEN']}Moving stop loss to breakeven.{TEXT_COLORS['RESET']}")
                        half_size = position['size']
                        handle_take_profit_fill(symbol, position, half_size)
                        update_stop_loss(symbol, position)
                
                # Check for stop loss hits to clean up remaining orders
                stop_loss_hit = any(
                    order['status'] == 'FILLED' and 
                    'STOP' in order['type'] and
                    not 'TAKE_PROFIT' in order['type'] 
                    for order in closed_orders
                )
                
                # If a stop loss was hit but we still have the original take profit order
                if stop_loss_hit and not first_tranche_closed:
                    print(f"{TEXT_COLORS['RED']}Stop loss hit detected! Cancelling remaining orders...{TEXT_COLORS['RESET']}")
                    client.cancel_open_orders(symbol=symbol)
                    print(f"{TEXT_COLORS['YELLOW']}All remaining orders canceled{TEXT_COLORS['RESET']}")
                    
                    # Reset position flags
                    first_tranche_closed = False
                    trailing_stop_activated = False
                
            except Exception as e:
                print(f"{TEXT_COLORS['RED']}Error checking order status: {str(e)}{TEXT_COLORS['RESET']}")
            
            # Check for exit signals
            exit_signal = check_exit_signals(symbol, position)


            if exit_signal == 'exit':
                # IMPORTANT: Capture all necessary data BEFORE closing the position
                kl = klines(symbol)
                
                # Calculate all indicators on the SAME dataset
                supertrend_df = calculate_supertrend(kl)
                atr_val = calculate_atr(kl).iloc[-1]
                ssl_df = calculate_ssl_channel(kl)
                current_price = kl['close'].iloc[-1]
                
                # Check exact reversal conditions - both SuperTrend and SSL
                is_long_reversal = (supertrend_df['buySignal'].iloc[-1] or 
                                (supertrend_df['trend'].iloc[-1] == 1 and supertrend_df['trend'].shift(1).iloc[-1] == -1)) and \
                                (ssl_df['ssl_long_signal'].iloc[-1] or 
                                (ssl_df['ssl'].iloc[-1] == 1 and ssl_df['ssl'].shift(1).iloc[-1] == -1))
                                
                is_short_reversal = (supertrend_df['sellSignal'].iloc[-1] or 
                                    (supertrend_df['trend'].iloc[-1] == -1 and supertrend_df['trend'].shift(1).iloc[-1] == 1)) and \
                                    (ssl_df['ssl_short_signal'].iloc[-1] or 
                                    (ssl_df['ssl'].iloc[-1] == -1 and ssl_df['ssl'].shift(1).iloc[-1] == 1))
                
                # Store the position side before closing
                previous_position_side = position['side']
                
                # Close the current position
                close_position(symbol, position)
                
                # Execute reversal IMMEDIATELY if conditions were met
                if (is_long_reversal and previous_position_side == 'short') or \
                (is_short_reversal and previous_position_side == 'long'):
                    
                    print(f"{TEXT_COLORS['YELLOW']}Immediate reversal condition detected on THIS candle!{TEXT_COLORS['RESET']}")
                    
                    # Execute the reversal trade without any further checks
                    if previous_position_side == 'long' and is_short_reversal:
                        print(f"{TEXT_COLORS['RED']}Executing IMMEDIATE SHORT reversal entry...{TEXT_COLORS['RESET']}")
                        open_order(symbol, 'sell', atr_val, current_price)
                        
                        # Notify about the reversal
                        telegram.notify_signal(
                            symbol=symbol,
                            signal_type="down",
                            reason="Immediate ST+SSL reversal",
                            current_price=current_price
                        )
                        
                    elif previous_position_side == 'short' and is_long_reversal:
                        print(f"{TEXT_COLORS['GREEN']}Executing IMMEDIATE LONG reversal entry...{TEXT_COLORS['RESET']}")
                        open_order(symbol, 'buy', atr_val, current_price)
                        
                        # Notify about the reversal
                        telegram.notify_signal(
                            symbol=symbol,
                            signal_type="up",
                            reason="Immediate ST+SSL reversal",
                            current_price=current_price
                        )
                else:
                    print(f"No immediate reversal condition on this candle. Will check for entry signals on next candle.")
        
        else:
            # No position open, check for entry signals
            print(f"No open position, scanning for entry signals...")
            
            # Generate signal
            signal, atr, current_price = signal_strategy(symbol)
            
            # Execute trade based on signal
            if signal == 'up':
                print(f"{TEXT_COLORS['GREEN']}LONG signal detected, executing entry...{TEXT_COLORS['RESET']}")
                open_order(symbol, 'buy', atr, current_price)
            elif signal == 'down':
                print(f"{TEXT_COLORS['RED']}SHORT signal detected, executing entry...{TEXT_COLORS['RESET']}")
                open_order(symbol, 'sell', atr, current_price)
        
        # Check if it's time for a P&L update
        check_pnl_update()
        
        # Wait until next candle with animation
        print(f"\nWaiting for next {timeframe} candle...")
        countdown_with_animation()
    
    except KeyboardInterrupt:
        print(f"\n{TEXT_COLORS['YELLOW']}Bot stopped by user{TEXT_COLORS['RESET']}")
        
        # Stop order monitoring
        order_monitor.stop()
        print(f"{TEXT_COLORS['YELLOW']}Order monitoring stopped{TEXT_COLORS['RESET']}")
        
        # Generate final P&L summary
        generate_pnl_summary()
        
        # Close any open positions if requested
        if position and input("Close open position? (y/n): ").lower() == 'y':
            close_position(symbol, position)
        
        break
    
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Unexpected error: {str(e)}{TEXT_COLORS['RESET']}")
        traceback.print_exc()
        
        telegram.notify_error(f"Unexpected error: {str(e)}", traceback.format_exc())

        sleep(60)  # Wait a bit before retrying