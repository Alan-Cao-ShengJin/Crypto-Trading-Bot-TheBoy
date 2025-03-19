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

client = UMFutures(key = api, secret = secret)

# Trading parameters
risk_percentage = 0.02  # Risk 2% of equity per trade
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
type = 'ISOLATED'  # type is 'ISOLATED' or 'CROSS'
timeframe = '1m'  # Changed from 4h to 1m for forward testing

# Binance BTCUSDT Futures constraints
MIN_ORDER_SIZE = 0.002  # Minimum order size is 0.002 BTC for BTCUSDT
SAFE_MIN_SIZE = 0.005   # Safe minimum for multiple tranche exits
MAX_LEVERAGE = 125      # Maximum allowed leverage

# Text color constants - simplified to 3 colors
TEXT_COLORS = {
    'GREEN': '\033[1;92m',  # Bright neon green
    'RED': '\033[1;91m',    # Bright red
    'YELLOW': '\033[1;93m', # Bright yellow (for chop data only)
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
current_stop_loss = 0
current_take_profit = 0

# Signal tracking to prevent re-entry on the same signal
last_signal_time = None
last_signal_type = None
last_attempted_entry = None

# P&L tracker and trade logging variables
last_pnl_update = datetime.now()
trade_history = []

# Ensure log directory exists
log_dir = "trading_logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
trade_log_file = os.path.join(log_dir, "trade_log.txt")
pnl_summary_file = os.path.join(log_dir, "pnl_summary.txt")

def get_balance_usdt():
    try:
        response = client.balance(recvWindow=6000)
        for elem in response:
            if elem['asset'] == 'USDT':
                # Round down to 2 decimal places
                return math.floor(float(elem['balance']) * 100) / 100
    except ClientError as error:
        print(
            f"{TEXT_COLORS['RED']}Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}{TEXT_COLORS['RESET']}"
        )

def klines(symbol):
    try:
        resp = pd.DataFrame(client.klines(symbol, timeframe))  # Using global timeframe
        resp = resp.iloc[:,:6]
        resp.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
        resp = resp.set_index('Time')
        resp.index = pd.to_datetime(resp.index, unit = 'ms')
        resp.index = resp.index + pd.Timedelta(hours=8)
        resp = resp.astype(float)
        return resp
    except ClientError as error:
        print(
            f"{TEXT_COLORS['RED']}Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}{TEXT_COLORS['RESET']}"
        )

def get_server_time():
    """Get Binance server time for synchronization."""
    try:
        server_time = client.time()
        return datetime.fromtimestamp(server_time['serverTime'] / 1000)
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error getting server time: {e}{TEXT_COLORS['RESET']}")
        return datetime.now()  # Fallback to local time

def get_next_candle_time():
    """Get the time when the next candle will close."""
    now = get_server_time()
    
    if timeframe == '1m':
        # Next minute
        next_candle = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    elif timeframe == '3m':
        # Next 3-minute mark
        minute = now.minute
        next_minute = 3 * ((minute // 3) + 1)
        next_candle = now.replace(minute=next_minute % 60, second=0, microsecond=0)
        if next_minute >= 60:
            next_candle += timedelta(hours=1)
    elif timeframe == '5m':
        # Next 5-minute mark
        minute = now.minute
        next_minute = 5 * ((minute // 5) + 1)
        next_candle = now.replace(minute=next_minute % 60, second=0, microsecond=0)
        if next_minute >= 60:
            next_candle += timedelta(hours=1)
    elif timeframe == '15m':
        # Next 15-minute mark
        minute = now.minute
        next_minute = 15 * ((minute // 15) + 1)
        next_candle = now.replace(minute=next_minute % 60, second=0, microsecond=0)
        if next_minute >= 60:
            next_candle += timedelta(hours=1)
    elif timeframe == '30m':
        # Next 30-minute mark
        minute = now.minute
        next_minute = 30 * ((minute // 30) + 1)
        next_candle = now.replace(minute=next_minute % 60, second=0, microsecond=0)
        if next_minute >= 60:
            next_candle += timedelta(hours=1)
    elif timeframe == '1h':
        # Next hour
        next_candle = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif timeframe == '2h':
        # Next 2-hour mark
        hour = now.hour
        next_hour = 2 * ((hour // 2) + 1)
        next_candle = now.replace(hour=next_hour % 24, minute=0, second=0, microsecond=0)
        if next_hour >= 24:
            next_candle += timedelta(days=1)
    elif timeframe == '4h':
        # Next 4-hour mark
        hour = now.hour
        next_hour = 4 * ((hour // 4) + 1)
        next_candle = now.replace(hour=next_hour % 24, minute=0, second=0, microsecond=0)
        if next_hour >= 24:
            next_candle += timedelta(days=1)
    else:
        # Default to 1 minute if timeframe is not recognized
        next_candle = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    
    return next_candle

def countdown_with_animation():
    """Display a countdown timer with a swirl animation until the next candle."""
    next_candle = get_next_candle_time()
    
    # Swirl animation characters
    swirl_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    
    try:
        idx = 0
        while True:
            now = datetime.now()
            if now >= next_candle:
                break
                
            time_left = (next_candle - now).total_seconds()
            if time_left <= 0:
                break
                
            minutes, seconds = divmod(int(time_left), 60)
            
            # Create countdown message
            if minutes > 0:
                time_str = f"{minutes}m {seconds}s"
            else:
                time_str = f"{seconds}s"
                
            # Use swirl animation
            swirl = swirl_chars[idx % len(swirl_chars)]
            
            # Print countdown with animation (no progress bar)
            message = f"\r{swirl} Next {timeframe} candle closes in {time_str}"
            sys.stdout.write(message)
            sys.stdout.flush()
            
            # Update animation index
            idx += 1
            
            # Sleep briefly
            time.sleep(0.1)
        
        # Clear the line after countdown
        sys.stdout.write("\r" + " " * len(message) + "\r")
        sys.stdout.flush()
        
    except Exception as e:
        print(f"\n{TEXT_COLORS['RED']}Error in countdown: {e}{TEXT_COLORS['RESET']}")

def set_leverage(symbol, level):
    """Set leverage with proper error handling and retry logic."""
    try:
        # First try setting the desired leverage
        response = client.change_leverage(symbol=symbol, leverage=level, recvWindow=6000)
        print(f"{TEXT_COLORS['GREEN']}Leverage set to {level}x for {symbol}{TEXT_COLORS['RESET']}")
        return level
    except ClientError as error:
        # If we get a specific error about max leverage, try to parse and use the max allowed
        if "Maximum leverage" in str(error.error_message):
            try:
                # Try to extract the max allowed leverage from the error message
                msg = str(error.error_message)
                max_allowed = int(''.join(filter(str.isdigit, msg.split("Maximum leverage")[1].split(".")[0])))
                if max_allowed > 0:
                    print(f"{TEXT_COLORS['YELLOW']}Maximum leverage allowed is {max_allowed}x. Trying to set that instead.{TEXT_COLORS['RESET']}")
                    try:
                        response = client.change_leverage(symbol=symbol, leverage=max_allowed, recvWindow=6000)
                        print(f"{TEXT_COLORS['GREEN']}Leverage set to {max_allowed}x for {symbol}{TEXT_COLORS['RESET']}")
                        return max_allowed
                    except Exception:
                        pass
            except Exception:
                pass
                
        # If any other error or the above parsing failed, try a lower leverage
        if level > 20:
            fallback_level = 20
        elif level > 10:
            fallback_level = 10
        elif level > 5:
            fallback_level = 5
        else:
            fallback_level = 3 if level > 3 else 1
            
        if fallback_level != level:
            print(f"{TEXT_COLORS['YELLOW']}Failed to set {level}x leverage. Trying {fallback_level}x instead.{TEXT_COLORS['RESET']}")
            try:
                response = client.change_leverage(symbol=symbol, leverage=fallback_level, recvWindow=6000)
                print(f"{TEXT_COLORS['GREEN']}Leverage set to {fallback_level}x for {symbol}{TEXT_COLORS['RESET']}")
                return fallback_level
            except Exception as e:
                print(f"{TEXT_COLORS['RED']}Could not set leverage to {fallback_level}x: {str(e)}{TEXT_COLORS['RESET']}")
                # Last resort - try to use leverage 1
                try:
                    response = client.change_leverage(symbol=symbol, leverage=1, recvWindow=6000)
                    print(f"{TEXT_COLORS['GREEN']}Fallback: Leverage set to 1x for {symbol}{TEXT_COLORS['RESET']}")
                    return 1
                except:
                    print(f"{TEXT_COLORS['RED']}Could not set any leverage for {symbol}{TEXT_COLORS['RESET']}")
                    return 1  # Return 1 as the safest fallback
        else:
            print(f"{TEXT_COLORS['RED']}Could not set leverage: {error.error_message}{TEXT_COLORS['RESET']}")
            return 1  # Return 1 as the safest fallback

def set_mode(symbol, margin_type):
    """Set margin type with better error handling."""
    try:
        response = client.change_margin_type(
            symbol=symbol, marginType=margin_type, recvWindow=6000
        )
        print(f"{TEXT_COLORS['GREEN']}Margin type set to {margin_type} for {symbol}{TEXT_COLORS['RESET']}")
        return True
    except ClientError as error:
        if "No need to change margin type" in str(error):
            print(f"{TEXT_COLORS['GREEN']}Margin type already set to {margin_type}{TEXT_COLORS['RESET']}")
            return True
        elif "Unable to adjust to isolated-margin mode" in str(error):
            print(f"{TEXT_COLORS['YELLOW']}Could not set to {margin_type}, using CROSS instead{TEXT_COLORS['RESET']}")
            # Try to set it to CROSS instead
            try:
                response = client.change_margin_type(
                    symbol=symbol, marginType="CROSS", recvWindow=6000
                )
                print(f"{TEXT_COLORS['GREEN']}Margin type set to CROSS for {symbol}{TEXT_COLORS['RESET']}")
                return True
            except Exception:
                # Probably already in CROSS mode
                print(f"{TEXT_COLORS['YELLOW']}Assuming CROSS margin mode is active{TEXT_COLORS['RESET']}")
                return True
        else:
            print(
                f"{TEXT_COLORS['RED']}Error setting margin type: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}"
            )
            return False

def get_price_precision(symbol):
    """Get price precision for the symbol."""
    try:
        resp = client.exchange_info()['symbols']
        for elem in resp:
            if elem['symbol'] == symbol:
                return elem['pricePrecision']
        return 1  # Default fallback for BTCUSDT (1 decimal place)
    except Exception as e:
        print(f"{TEXT_COLORS['YELLOW']}Error getting price precision: {str(e)}. Using default.{TEXT_COLORS['RESET']}")
        return 1  # Fallback
        
def get_qty_precision(symbol):
    """Get quantity precision and minimum size for the symbol."""
    try:
        resp = client.exchange_info()['symbols']
        for elem in resp:
            if elem['symbol'] == symbol:
                min_size = 0.001  # Default for BTCUSDT
                for filter in elem['filters']:
                    if filter['filterType'] == 'LOT_SIZE':
                        min_size = float(filter['minQty'])
                return elem['quantityPrecision'], min_size
        return 3, MIN_ORDER_SIZE  # Default fallback
    except Exception as e:
        print(f"{TEXT_COLORS['YELLOW']}Error getting quantity precision: {str(e)}. Using default.{TEXT_COLORS['RESET']}")
        return 3, MIN_ORDER_SIZE  # Fallback

def calculate_typical_position_size(balance, symbol='BTCUSDT'):
    """
    Calculate a typical position size using current market data.
    
    This function:
    1. Fetches recent price data
    2. Calculates ATR using current market conditions
    3. Applies the standard risk formula with current values
    
    Args:
        balance: Current account balance in USDT
        symbol: Trading pair symbol (default: 'BTCUSDT')
        
    Returns:
        Estimated full position size for a standard entry
    """
    try:
        # Get current price data
        current_price = None
        try:
            ticker_data = client.ticker_price(symbol=symbol)
            current_price = float(ticker_data['price'])
        except Exception as e:
            print(f"Error getting current price: {str(e)}")
            # Fallback: try to get price from klines
            try:
                recent_candles = client.klines(symbol=symbol, interval=timeframe, limit=1)
                if recent_candles and len(recent_candles) > 0:
                    current_price = float(recent_candles[0][4])  # Close price
            except Exception:
                pass
        
        # If we still don't have a price, use a safe estimate
        if not current_price:
            print(f"{TEXT_COLORS['YELLOW']}Warning: Using fallback price estimate for position calculation{TEXT_COLORS['RESET']}")
            current_price = 80000  # Safe fallback
        
        # Calculate recent ATR value
        atr_value = None
        try:
            # Fetch enough candles to calculate ATR
            atr_period = risk_atr_period
            candles = client.klines(symbol=symbol, interval=timeframe, limit=atr_period + 10)
            if candles and len(candles) > atr_period:
                # Convert to DataFrame for ATR calculation
                df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 
                                                    'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignored'])
                df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
                
                # Calculate TR (True Range)
                df['tr1'] = abs(df['high'] - df['low'])
                df['tr2'] = abs(df['high'] - df['close'].shift())
                df['tr3'] = abs(df['low'] - df['close'].shift())
                df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
                
                # Calculate ATR
                atr_value = df['tr'].rolling(window=atr_period).mean().iloc[-1]
        except Exception as e:
            print(f"Error calculating ATR: {str(e)}")
        
        # If we couldn't calculate ATR, estimate it as a percentage of price
        if not atr_value or math.isnan(atr_value):
            print(f"{TEXT_COLORS['YELLOW']}Warning: Using fallback ATR estimate for position calculation{TEXT_COLORS['RESET']}")
            atr_value = current_price * 0.005  # Estimate ATR as 0.5% of price
        
        # Calculate stop distance
        stop_distance = risk_atr_multiple * atr_value
        
        # Calculate risk amount (2% of equity)
        risk_amount = balance * risk_percentage
        
        # Calculate position size using risk formula
        position_size = risk_amount / stop_distance
        
        # Get precision values for rounding
        qty_precision, min_size = get_qty_precision(symbol)
        
        # Make sure it meets minimum size requirements
        position_size = max(position_size, min_size)
        
        # Round to appropriate precision
        position_size = round(position_size, qty_precision)
        
        # For debugging
        print(f"Estimated position size calculation:")
        print(f"  - Current price: ${current_price:.2f}")
        print(f"  - ATR value: ${atr_value:.2f}")
        print(f"  - Stop distance: ${stop_distance:.2f}")
        print(f"  - Risk amount: ${risk_amount:.2f}")
        print(f"  - Estimated position size: {position_size:.8f} BTC")
        
        return position_size
        
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error in position size calculation: {str(e)}{TEXT_COLORS['RESET']}")
        # Very conservative fallback
        return 0.01  # Safe fallback size

def calculate_supertrend(df, period=10, multiplier=3.0):
    """Calculate SuperTrend indicator."""
    df = df.copy()
    
    # Calculate ATR
    df['tr1'] = df['High'] - df['Low']
    df['tr2'] = abs(df['High'] - df['Close'].shift())
    df['tr3'] = abs(df['Low'] - df['Close'].shift())
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(period).mean()
    
    # Calculate basic bands
    df['hl2'] = (df['High'] + df['Low']) / 2
    df['upperband'] = df['hl2'] + (multiplier * df['atr'])
    df['lowerband'] = df['hl2'] - (multiplier * df['atr'])
    
    # Initialize SuperTrend columns
    df['in_uptrend'] = True
    df['supertrend'] = df['lowerband']
    
    # Calculate SuperTrend
    for i in range(1, len(df)):
        curr_close = df['Close'].iloc[i]
        prev_uptrend = df['in_uptrend'].iloc[i-1]
        prev_supertrend = df['supertrend'].iloc[i-1]
        
        if prev_uptrend:
            if curr_close <= prev_supertrend:
                df.loc[df.index[i], 'in_uptrend'] = False
                df.loc[df.index[i], 'supertrend'] = df['upperband'].iloc[i]
            else:
                df.loc[df.index[i], 'in_uptrend'] = True
                df.loc[df.index[i], 'supertrend'] = max(df['lowerband'].iloc[i], prev_supertrend)
        else:
            if curr_close >= prev_supertrend:
                df.loc[df.index[i], 'in_uptrend'] = True
                df.loc[df.index[i], 'supertrend'] = df['lowerband'].iloc[i]
            else:
                df.loc[df.index[i], 'in_uptrend'] = False
                df.loc[df.index[i], 'supertrend'] = min(df['upperband'].iloc[i], prev_supertrend)
    
    # Convert boolean trend to integer (1 for uptrend, -1 for downtrend)
    df['trend'] = np.where(df['in_uptrend'], 1, -1)
    
    # Generate buy/sell signals (when trend changes)
    df['buySignal'] = (df['trend'] == 1) & (df['trend'].shift(1) == -1)
    df['sellSignal'] = (df['trend'] == -1) & (df['trend'].shift(1) == 1)
    
    return df

def calculate_ssl_channel(df, period=10):
    """Calculate SSL Channel indicator."""
    df = df.copy()
    
    # Calculate moving averages of high and low
    df['sma_high'] = df['High'].rolling(window=period).mean()
    df['sma_low'] = df['Low'].rolling(window=period).mean()
    
    # Initialize SSL column
    df['ssl'] = 0
    
    # Set SSL to 1 where Close > sma_high (uptrend), -1 where Close < sma_low (downtrend)
    df.loc[df['Close'] > df['sma_high'], 'ssl'] = 1
    df.loc[df['Close'] < df['sma_low'], 'ssl'] = -1
    
    # Fill forward to maintain the trend when neither condition is met
    df['ssl'] = df['ssl'].replace(0, np.nan).ffill().fillna(0)
    
    # Generate signals
    df['ssl_long_signal'] = (df['ssl'] == 1) & (df['ssl'].shift(1) == -1)
    df['ssl_short_signal'] = (df['ssl'] == -1) & (df['ssl'].shift(1) == 1)
    
    return df

def calculate_chop(df, length=14):
    """Calculate Choppiness Index."""
    df = df.copy()
    
    # Calculate True Range
    high_low = df['High'] - df['Low']
    high_close = abs(df['High'] - df['Close'].shift())
    low_close = abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    
    # Calculate the sum of TR over the lookback period
    sum_tr = true_range.rolling(window=length).sum()
    
    # Calculate highest high and lowest low over the lookback period
    highest_high = df['High'].rolling(window=length).max()
    lowest_low = df['Low'].rolling(window=length).min()
    
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
        'balance': get_balance_usdt(),
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
        print(f"\n{TEXT_COLORS['GREEN']}=== 5-MINUTE P&L UPDATE ==={TEXT_COLORS['RESET']}")
        
        # Calculate and print current P&L
        position = get_open_positions(symbol)
        balance = get_balance_usdt()
        
        print(f"Current Balance: ${balance:.2f}")
        
        if position:
            unrealized_pnl, pnl_pct = calculate_unrealized_pnl(position)
            
            pnl_color = TEXT_COLORS['GREEN'] if unrealized_pnl >= 0 else TEXT_COLORS['RED']
            print(f"Position: {position['side'].upper()} {position['size']} {symbol}")
            print(f"Entry Price: ${position['entry_price']:.2f}")
            print(f"Current Price: ${position['mark_price']:.2f}")
            print(f"Unrealized P&L: {pnl_color}${unrealized_pnl:.2f} ({pnl_pct:.2f}%){TEXT_COLORS['RESET']}")
            
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
        print(f"Next P&L update at: {(now + timedelta(minutes=5)).strftime('%H:%M:%S')}")

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
        current_equity = get_balance_usdt()
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
            with open(pnl_summary_file, 'w') as f:
                for line in summary:
                    f.write(line + '\n')
            print(f"P&L summary saved to {pnl_summary_file}")
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error saving P&L summary: {str(e)}{TEXT_COLORS['RESET']}")
    
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Error generating P&L summary: {str(e)}{TEXT_COLORS['RESET']}")

def signal_strategy(symbol):
    """SuperTrend + SSL Channel strategy signal."""
    global prev_supertrend, prev_ssl, st_reset_detected, last_signal_time, last_signal_type
    
    # Fetch candle data
    kl = klines(symbol)
    if kl is None or len(kl) < max(supertrend_atr_period, ssl_period, chop_length):
        print(f"{TEXT_COLORS['RED']}Not enough data to calculate indicators{TEXT_COLORS['RESET']}")
        return 'none', 0, 0
    
    # Calculate indicators
    df = calculate_supertrend(kl, supertrend_atr_period, supertrend_factor)
    df = calculate_ssl_channel(df, ssl_period)
    df = calculate_chop(df, chop_length)
    
    # Get latest values
    st_trend = df['trend'].iloc[-1]
    ssl = df['ssl'].iloc[-1]
    st_buy_signal = df['buySignal'].iloc[-1]
    st_sell_signal = df['sellSignal'].iloc[-1]
    ssl_long_signal = df['ssl_long_signal'].iloc[-1]
    ssl_short_signal = df['ssl_short_signal'].iloc[-1]
    is_choppy = df['is_choppy'].iloc[-1]
    chop_value = df['chop'].iloc[-1]
    atr = df['atr'].iloc[-1]
    current_price = df['Close'].iloc[-1]
    
    # Initialize previous values if None
    if prev_supertrend is None:
        prev_supertrend = st_trend
    if prev_ssl is None:
        prev_ssl = ssl
    
    # Check for trend flips
    supertrend_up_flip = st_buy_signal or (prev_supertrend == -1 and st_trend == 1)
    supertrend_down_flip = st_sell_signal or (prev_supertrend == 1 and st_trend == -1)
    ssl_up_flip = ssl_long_signal or (prev_ssl == -1 and ssl == 1)
    ssl_down_flip = ssl_short_signal or (prev_ssl == 1 and ssl == -1)
    
    # Create a unique signal identifier for this candle
    current_time = df.index[-1] if len(df.index) > 0 else datetime.now()
    signal_id = current_time.strftime('%Y-%m-%d %H:%M:%S')
    
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
        print(f"{TEXT_COLORS['RED']}LONG signal detected but market too choppy - REJECTED{TEXT_COLORS['RESET']}")
        long_entry = False
    
    if short_entry and st_reset_detected and is_choppy:
        print(f"{TEXT_COLORS['RED']}SHORT signal detected but market too choppy - REJECTED{TEXT_COLORS['RESET']}")
        short_entry = False
    
    # Check if we've already attempted to trade this signal
    if (last_signal_time is not None and 
        last_signal_time == signal_id and 
        ((long_entry and last_signal_type == 'up') or (short_entry and last_signal_type == 'down')) and
        last_attempted_entry == True):
        print(f"{TEXT_COLORS['YELLOW']}Already attempted to trade this signal - skipping{TEXT_COLORS['RESET']}")
        long_entry = False
        short_entry = False
    
    # Update state
    prev_supertrend = st_trend
    prev_ssl = ssl
    
    # Log and return signal
    if long_entry:
        print(f"{TEXT_COLORS['GREEN']}LONG ENTRY SIGNAL: {'SSL flip + ST uptrend' if ssl_up_flip else 'ST flip + SSL uptrend'}{TEXT_COLORS['RESET']}")
        # Update last signal info
        last_signal_time = signal_id
        last_signal_type = 'up'
        return 'up', atr, current_price
    elif short_entry:
        print(f"{TEXT_COLORS['RED']}SHORT ENTRY SIGNAL: {'SSL flip + ST downtrend' if ssl_down_flip else 'ST flip + SSL downtrend'}{TEXT_COLORS['RESET']}")
        # Update last signal info
        last_signal_time = signal_id
        last_signal_type = 'down'
        return 'down', atr, current_price
    else:
        return 'none', atr, current_price

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
    """Check if we should exit the position based on signals and identify potential reversal signals."""
    global first_tranche_closed, trailing_stop_activated, current_stop_loss
    
    # Get candle data and calculate indicators
    df = klines(symbol)
    if df is None or len(df) < max(supertrend_atr_period, ssl_period, chop_length):
        print(f"{TEXT_COLORS['RED']}Not enough data to calculate exit signals{TEXT_COLORS['RESET']}")
        return None, {'has_reversal': False, 'reversal_side': None, 'st_reset_detected': False, 'is_choppy': False, 'chop_value': 0, 'atr': 0}
    
    df = calculate_supertrend(df, supertrend_atr_period, supertrend_factor)
    df = calculate_ssl_channel(df, ssl_period)  # Add SSL calculation for reversal checks
    df = calculate_chop(df, chop_length)  # Add Choppiness calculation for reversal checks
    
    current_price = df['Close'].iloc[-1]
    
    # Get key indicator values
    st_trend = df['trend'].iloc[-1]
    ssl = df['ssl'].iloc[-1]
    is_choppy = df['is_choppy'].iloc[-1]
    chop_value = df['chop'].iloc[-1]
    st_buy_signal = df['buySignal'].iloc[-1]
    st_sell_signal = df['sellSignal'].iloc[-1]
    
    # Prepare reversal info dictionary
    reversal_info = {
        'has_reversal': False,
        'reversal_side': None,
        'st_reset_detected': False,
        'is_choppy': is_choppy,
        'chop_value': chop_value,
        'atr': df['atr'].iloc[-1] if 'atr' in df.columns else 0,
        'current_price': current_price
    }
    
    # Check for SuperTrend flip exit - this is where we can also detect potential reversals
    if position['side'] == 'long' and st_sell_signal:
        print(f"{TEXT_COLORS['YELLOW']}EXIT SIGNAL: SuperTrend flipped to downtrend{TEXT_COLORS['RESET']}")
        
        # Check if immediate short reversal conditions are met
        if ssl == -1:  # SSL is already in downtrend when SuperTrend flips
            reversal_info['has_reversal'] = True
            reversal_info['reversal_side'] = 'sell'
            reversal_info['st_reset_detected'] = True
            print(f"{TEXT_COLORS['YELLOW']}REVERSAL DETECTED: Short conditions met after SuperTrend flip{TEXT_COLORS['RESET']}")
        
        return 'exit', reversal_info
        
    elif position['side'] == 'short' and st_buy_signal:
        print(f"{TEXT_COLORS['YELLOW']}EXIT SIGNAL: SuperTrend flipped to uptrend{TEXT_COLORS['RESET']}")
        
        # Check if immediate long reversal conditions are met
        if ssl == 1:  # SSL is already in uptrend when SuperTrend flips
            reversal_info['has_reversal'] = True
            reversal_info['reversal_side'] = 'buy'
            reversal_info['st_reset_detected'] = True
            print(f"{TEXT_COLORS['YELLOW']}REVERSAL DETECTED: Long conditions met after SuperTrend flip{TEXT_COLORS['RESET']}")
        
        return 'exit', reversal_info
    
    # Check trailing stop update if activated
    if trailing_stop_activated:
        if position['side'] == 'long':
            new_stop = current_price - (trailing_atr_multiple * position_entry_atr)
            if new_stop > current_stop_loss:
                current_stop_loss = new_stop
                print(f"{TEXT_COLORS['GREEN']}TRAILING STOP UPDATED to ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
                return 'update_stop', reversal_info
        else:  # short position
            new_stop = current_price + (trailing_atr_multiple * position_entry_atr)
            if new_stop < current_stop_loss:
                current_stop_loss = new_stop
                print(f"{TEXT_COLORS['GREEN']}TRAILING STOP UPDATED to ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
                return 'update_stop', reversal_info
    
    # Check if we should activate trailing stop (after first take profit hit)
    elif first_tranche_closed and not trailing_stop_activated:
        if position['side'] == 'long' and current_price >= (position_entry_price + (trailing_atr_trigger * position_entry_atr)):
            trailing_stop_activated = True
            current_stop_loss = current_price - (trailing_atr_multiple * position_entry_atr)
            print(f"{TEXT_COLORS['GREEN']}TRAILING STOP ACTIVATED at ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
            return 'trailing_activated', reversal_info
        elif position['side'] == 'short' and current_price <= (position_entry_price - (trailing_atr_trigger * position_entry_atr)):
            trailing_stop_activated = True
            current_stop_loss = current_price + (trailing_atr_multiple * position_entry_atr)
            print(f"{TEXT_COLORS['GREEN']}TRAILING STOP ACTIVATED at ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
            return 'trailing_activated', reversal_info
    
    return None, reversal_info

def handle_take_profit_fill(symbol, position, half_size):
    """
    Handle when a take profit order is filled.
    Directly updates the stop loss order to breakeven.
    """
    global first_tranche_closed, current_stop_loss, position_entry_price
    
    # Update state
    first_tranche_closed = True
    
    # Move stop loss to breakeven
    current_stop_loss = position_entry_price
    
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
    
    print(f"{TEXT_COLORS['GREEN']}Take profit hit! Moving stop loss to breakeven at ${position_entry_price:.2f}{TEXT_COLORS['RESET']}")
    
    # Immediately update the stop loss order
    try:
        # Cancel all existing stop orders first
        orders = client.get_open_orders(symbol=symbol)
        stop_orders_found = False
        
        for order in orders:
            if 'STOP' in order['type'].upper():
                stop_orders_found = True
                try:
                    client.cancel_order(symbol=symbol, orderId=order['orderId'])
                    print(f"{TEXT_COLORS['YELLOW']}Canceled existing stop order: {order['orderId']}{TEXT_COLORS['RESET']}")
                except Exception as e:
                    print(f"{TEXT_COLORS['RED']}Error canceling stop order: {str(e)}{TEXT_COLORS['RESET']}")
        
        if not stop_orders_found:
            print(f"{TEXT_COLORS['YELLOW']}No existing stop orders found to cancel{TEXT_COLORS['RESET']}")
        
        # Place new stop loss at breakeven
        side = 'SELL' if position['side'] == 'long' else 'BUY'
        
        # Add a small buffer to prevent immediate trigger on volatility
        adjusted_stop = position_entry_price
        
        # Create the stop loss order
        new_stop_order = client.new_order(
            symbol=symbol,
            side=side,
            type='STOP_MARKET',
            quantity=position['size'],  # Remaining position size
            stopPrice=adjusted_stop,
            reduceOnly='true',
            timeInForce='GTC'
        )
        
        print(f"{TEXT_COLORS['GREEN']}NEW BREAKEVEN STOP PLACED at ${adjusted_stop:.2f}{TEXT_COLORS['RESET']}")
        print(f"{TEXT_COLORS['GREEN']}Order ID: {new_stop_order['orderId']}{TEXT_COLORS['RESET']}")
        
        return True
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}Error setting breakeven stop: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        # Even if order creation fails, log it clearly so you know there's no stop loss
        print(f"{TEXT_COLORS['RED']}*** WARNING: NO STOP LOSS ACTIVE - MANUAL INTERVENTION REQUIRED ***{TEXT_COLORS['RESET']}")
        return False

def optimize_position_size(symbol, side, risk_amount, stop_distance, current_price, balance):
    """
    Optimize position size to properly enforce the risk percentage rule (2% of equity).
    
    Returns:
        dict with position size, leverage, and other details
    """
    # Get precision values
    qty_precision, min_size = get_qty_precision(symbol)
    
    # Calculate the raw position size in BTC (without leverage)
    # This is the core of the risk calculation: risk_amount / stop_distance
    raw_position_size = risk_amount / stop_distance
    raw_position_value = raw_position_size * current_price
    
    # Define safe margin requirement (95% of available balance)
    safe_balance = balance * 0.95
    
    # Force proper risk-based sizing instead of defaulting to minimum
    target_position_size = raw_position_size
    target_position_value = target_position_size * current_price
    
    # First determine if we need leverage to meet minimum size requirements
    leverage_needed_for_min_size = 1
    if target_position_size < min_size:
        # Calculate leverage needed to get to minimum size while maintaining risk amount
        leverage_needed_for_min_size = min_size / target_position_size
        print(f"{TEXT_COLORS['YELLOW']}Risk-based size {target_position_size:.8f} BTC below minimum. Need {leverage_needed_for_min_size:.1f}x leverage.{TEXT_COLORS['RESET']}")
        target_position_size = min_size
        target_position_value = target_position_size * current_price
    
    # Calculate margin requirements at different leverage levels
    leverages_to_try = [1, 2, 3, 5, 10, 20, 25, 50, 75, 100, 125]
    
    # Find the lowest leverage that allows us to trade with our desired position size
    chosen_leverage = None
    chosen_position_size = None
    
    for lev in leverages_to_try:
        margin_required = target_position_value / lev
        
        # If this leverage allows us to trade within our margin constraints
        if margin_required <= safe_balance and lev >= leverage_needed_for_min_size:
            chosen_leverage = lev
            chosen_position_size = target_position_size
            break
    
    # If no valid leverage found, use the highest allowed leverage and scale down position
    if chosen_leverage is None:
        chosen_leverage = MAX_LEVERAGE
        max_affordable_value = safe_balance * MAX_LEVERAGE
        chosen_position_size = max_affordable_value / current_price
        
        # Ensure it's still above minimum size
        if chosen_position_size < min_size:
            chosen_position_size = min_size
            print(f"{TEXT_COLORS['RED']}WARNING: Using minimum size {min_size} BTC with max leverage {MAX_LEVERAGE}x{TEXT_COLORS['RESET']}")
            print(f"{TEXT_COLORS['RED']}Actual risk will exceed target of {risk_percentage*100}%{TEXT_COLORS['RESET']}")
        else:
            print(f"{TEXT_COLORS['YELLOW']}Position size reduced to {chosen_position_size:.8f} BTC due to margin constraints{TEXT_COLORS['RESET']}")
    
    # Round to appropriate precision
    position_size = round(chosen_position_size, qty_precision)
    
    # Calculate final values
    position_value = position_size * current_price
    required_margin = position_value / chosen_leverage
    
    # Calculate actual risk amount and percentage
    actual_risk_amount = (position_size * stop_distance) / chosen_leverage
    actual_risk_percentage = (actual_risk_amount / balance) * 100
    margin_usage = (required_margin / safe_balance) * 100
    
    # Log detailed calculation for debugging
    print(f"\n{TEXT_COLORS['GREEN']}Position Sizing Details:{TEXT_COLORS['RESET']}")
    print(f"Price: ${current_price:.2f}")
    print(f"Stop Distance: ${stop_distance:.2f}")
    print(f"Risk Amount: ${risk_amount:.2f} ({risk_percentage*100:.1f}% of ${balance:.2f})")
    print(f"Raw Position Size: {raw_position_size:.8f} BTC (before leverage)")
    print(f"Final Position Size: {position_size:.8f} BTC")
    print(f"Chosen Leverage: {chosen_leverage}x")
    print(f"Position Value: ${position_value:.2f}")
    print(f"Margin Required: ${required_margin:.2f} ({margin_usage:.1f}% of available)")
    print(f"Actual Risk: ${actual_risk_amount:.2f} ({actual_risk_percentage:.2f}% of equity)")
    
    return {
        'position_size': position_size,
        'leverage': chosen_leverage,
        'position_value': position_value,
        'required_margin': required_margin,
        'available_margin': safe_balance,
        'margin_usage': margin_usage,
        'actual_risk_amount': actual_risk_amount,
        'actual_risk_percentage': actual_risk_percentage
    }

def open_order(symbol, side, atr_value, current_price):
    """
    Open an order with proper position sizing and risk management.
    
    Args:
        symbol: Trading pair
        side: 'buy' or 'sell'
        atr_value: Current ATR value for position sizing
        current_price: Current price for position sizing
    """
    global position_entry_price, position_entry_atr, current_stop_loss, current_take_profit, last_attempted_entry
    
    # Mark that we're attempting an entry on the current signal
    last_attempted_entry = True
    
    # Get account balance for risk calculation (rounded down to 2 decimal places)
    balance = get_balance_usdt()
    if balance is None or balance <= 0:
        print(f"{TEXT_COLORS['RED']}ERROR: Cannot get valid balance or balance is zero{TEXT_COLORS['RESET']}")
        return False
    
    # Calculate risk amount (2% of equity)
    risk_amount = balance * risk_percentage
    
    # Calculate stop loss distance in price
    stop_distance = risk_atr_multiple * atr_value
    
    # Security check - ensure we have a valid stop distance
    if stop_distance <= 0 or math.isnan(stop_distance):
        stop_distance = current_price * 0.01  # Use 1% of price as fallback
        print(f"{TEXT_COLORS['YELLOW']}WARNING: Using fallback stop distance: ${stop_distance:.2f}{TEXT_COLORS['RESET']}")
    
    # Calculate stop loss price
    if side == 'buy':
        stop_price = current_price - stop_distance
    else:
        stop_price = current_price + stop_distance
    
    # Get quantity precision and minimum order size
    qty_precision, min_size = get_qty_precision(symbol)
    
    # Optimize position sizing
    position_details = optimize_position_size(
        symbol=symbol,
        side=side,
        risk_amount=risk_amount,
        stop_distance=stop_distance,
        current_price=current_price,
        balance=balance
    )
    
    # Get optimized values
    position_size = position_details['position_size']
    calculated_leverage = position_details['leverage']
    position_value = position_details['position_value']
    required_margin = position_details['required_margin']
    actual_risk = position_details['actual_risk_amount']
    actual_risk_pct = position_details['actual_risk_percentage']
    
    print(f"{TEXT_COLORS['GREEN']}Position sizing calculations:{TEXT_COLORS['RESET']}")
    print(f"Risk amount: ${risk_amount:.2f} ({risk_percentage*100}% of ${balance:.2f})")
    print(f"Stop distance: ${stop_distance:.2f} ({risk_atr_multiple}x ATR)")
    print(f"Available margin: ${position_details['available_margin']:.2f}")
    print(f"Optimized position size: {position_size} BTC (${position_value:.2f})")
    print(f"Optimized leverage: {calculated_leverage}x")
    print(f"Required margin: ${required_margin:.2f} ({position_details['margin_usage']:.2f}% of available)")
    print(f"Actual risk: ${actual_risk:.2f} ({actual_risk_pct:.2f}% of equity)")
    
    # Calculate take profit level
    price_precision = get_price_precision(symbol)
    if side == 'buy':
        tp_price = round(current_price + (tp_atr_multiple * atr_value), price_precision)
    else:
        tp_price = round(current_price - (tp_atr_multiple * atr_value), price_precision)
    
    # Print order details
    print(f"\n{TEXT_COLORS['GREEN']}ORDER DETAILS:{TEXT_COLORS['RESET']}")
    print(f"Symbol: {symbol}")
    print(f"Side: {TEXT_COLORS['GREEN'] if side == 'buy' else TEXT_COLORS['RED']}{side.upper()}{TEXT_COLORS['RESET']}")
    print(f"Price: ${current_price:.2f}")
    print(f"Stop Loss: ${stop_price:.2f} ({risk_atr_multiple}x ATR)")
    print(f"Take Profit: ${tp_price:.2f} ({tp_atr_multiple}x ATR)")
    print(f"Position Size: {position_size} BTC (${position_value:.2f})")
    print(f"Leverage: {calculated_leverage}x")
    print(f"Required Margin: ${required_margin:.2f}")
    print(f"Actual Risk: ${actual_risk:.2f} ({actual_risk_pct:.2f}% of equity)")
    
    # Place the order
    try:
        # Set margin type first (ISOLATED or CROSS)
        set_mode_result = set_mode(symbol, type)
        sleep(1)
        
        # Set leverage - use the function that handles fallbacks
        actual_leverage = set_leverage(symbol, calculated_leverage)
        if actual_leverage != calculated_leverage:
            print(f"{TEXT_COLORS['YELLOW']}Using actual leverage of {actual_leverage}x instead of {calculated_leverage}x{TEXT_COLORS['RESET']}")
            # Recalculate margin requirement
            required_margin = position_value / actual_leverage
            actual_risk = position_size * stop_distance / actual_leverage
            print(f"Adjusted margin: ${required_margin:.2f}, adjusted risk: ${actual_risk:.2f}")
            
            # Double-check margin requirement with the new leverage
            if required_margin > position_details['available_margin']:
                # Reduce position size to fit available margin
                max_position_size = (position_details['available_margin'] * actual_leverage) / current_price
                position_size = round(min(position_size, max_position_size), qty_precision)
                print(f"{TEXT_COLORS['YELLOW']}Reduced position size to {position_size} BTC to fit margin{TEXT_COLORS['RESET']}")
                
                # Recalculate values
                position_value = position_size * current_price
                required_margin = position_value / actual_leverage
                actual_risk = position_size * stop_distance / actual_leverage
                print(f"Final margin: ${required_margin:.2f}, final risk: ${actual_risk:.2f}")
        
        sleep(1)
        
        # Ensure the position size is still valid after leverage adjustments
        if position_size < min_size:
            print(f"{TEXT_COLORS['RED']}Position size {position_size} is below minimum {min_size}. Adjusting to minimum.{TEXT_COLORS['RESET']}")
            position_size = min_size
        
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
            half_size = round(position_size / 2, qty_precision)
            if half_size >= min_size:  # Only place if half size meets minimum requirements
                take_profit_order = client.new_order(
                    symbol=symbol, 
                    side='SELL', 
                    type='TAKE_PROFIT_MARKET', 
                    quantity=half_size,
                    stopPrice=tp_price,
                    reduceOnly='true'
                )
                print(f"{TEXT_COLORS['GREEN']}Take profit set at ${tp_price:.2f} for {half_size} BTC{TEXT_COLORS['RESET']}")
            else:
                # If half size is too small, use full size
                print(f"{TEXT_COLORS['YELLOW']}Half position too small, using full size for take profit{TEXT_COLORS['RESET']}")
                take_profit_order = client.new_order(
                    symbol=symbol, 
                    side='SELL', 
                    type='TAKE_PROFIT_MARKET', 
                    quantity=position_size,
                    stopPrice=tp_price,
                    reduceOnly='true'
                )
                print(f"{TEXT_COLORS['GREEN']}Take profit set at ${tp_price:.2f} for {position_size} BTC{TEXT_COLORS['RESET']}")
            
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
            half_size = round(position_size / 2, qty_precision)
            if half_size >= min_size:  # Only place if half size meets minimum requirements
                take_profit_order = client.new_order(
                    symbol=symbol, 
                    side='BUY', 
                    type='TAKE_PROFIT_MARKET', 
                    quantity=half_size,
                    stopPrice=tp_price,
                    reduceOnly='true'
                )
                print(f"{TEXT_COLORS['GREEN']}Take profit set at ${tp_price:.2f} for {half_size} BTC{TEXT_COLORS['RESET']}")
            else:
                # If half size is too small, use full size
                print(f"{TEXT_COLORS['YELLOW']}Half position too small, using full size for take profit{TEXT_COLORS['RESET']}")
                take_profit_order = client.new_order(
                    symbol=symbol, 
                    side='BUY', 
                    type='TAKE_PROFIT_MARKET', 
                    quantity=position_size,
                    stopPrice=tp_price,
                    reduceOnly='true'
                )
                print(f"{TEXT_COLORS['GREEN']}Take profit set at ${tp_price:.2f} for {position_size} BTC{TEXT_COLORS['RESET']}")
            
            # Save position details
            position_entry_price = current_price
            position_entry_atr = atr_value
            current_stop_loss = stop_price
            current_take_profit = tp_price
            
        # Log the trade
        log_trade('entry', symbol, side, position_size, current_price, 
                  additional_info={'leverage': actual_leverage, 'stop_loss': stop_price, 'take_profit': tp_price})
            
        return True
        
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}ERROR placing order: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        # Reset last_attempted_entry since the order failed
        last_attempted_entry = False
        return False

def close_position(symbol, position):
    """Close an open position and handle any reversal logic."""
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
        
        # Reset position tracking
        global first_tranche_closed, trailing_stop_activated, last_attempted_entry
        first_tranche_closed = False
        trailing_stop_activated = False
        last_attempted_entry = False  # Reset for potential reversal entry
        
        return True
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}Error closing position: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        return False

def update_stop_loss(symbol, position):
    """Update the stop loss order."""
    try:
        # Cancel existing stop orders
        orders = client.get_open_orders(symbol=symbol)
        for order in orders:
            if 'STOP' in order['type']:
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                print(f"{TEXT_COLORS['YELLOW']}Canceled old stop order{TEXT_COLORS['RESET']}")
        
        # Place new stop order
        side = 'SELL' if position['side'] == 'long' else 'BUY'
        client.new_order(
            symbol=symbol,
            side=side,
            type='STOP_MARKET',
            quantity=position['size'],
            stopPrice=current_stop_loss,
            reduceOnly='true'
        )
        print(f"{TEXT_COLORS['GREEN']}New stop loss order placed at ${current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
        
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
            
            print(f"{TEXT_COLORS['GREEN']}Take profit hit! Stop loss moved to breakeven at ${position_entry_price:.2f}{TEXT_COLORS['RESET']}")
        
        return True
    except ClientError as error:
        print(f"{TEXT_COLORS['RED']}Error updating stop loss: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}")
        return False

def process_reversal(symbol, reversal_info):
    """Process a reversal signal after position close."""
    if not reversal_info['has_reversal']:
        return False
    
    reversal_side = reversal_info['reversal_side']
    atr_value = reversal_info['atr']
    current_price = reversal_info['current_price']
    is_choppy = reversal_info['is_choppy']
    st_reset_detected = reversal_info['st_reset_detected']
    
    # Reject if market is choppy and SuperTrend just reset
    if is_choppy and st_reset_detected:
        print(f"{TEXT_COLORS['YELLOW']}REJECTED REVERSAL: Market too choppy (Chop: {reversal_info['chop_value']:.2f}){TEXT_COLORS['RESET']}")
        return False
    
    print(f"{TEXT_COLORS['YELLOW']}EXECUTING IMMEDIATE REVERSAL: {reversal_side.upper()} after trend flip{TEXT_COLORS['RESET']}")
    
    # Execute the reversal entry
    return open_order(symbol, reversal_side, atr_value, current_price)

# Main trading loop
symbol = 'BTCUSDT'
cycle_count = 0

print(f"\n{TEXT_COLORS['GREEN']}=== THE BOY - CRYPTO TRADING BOT v1.1 ==={TEXT_COLORS['RESET']}")
print(f"{TEXT_COLORS['GREEN']}Strategy Parameters:{TEXT_COLORS['RESET']}")
print(f"Timeframe: {timeframe}")
print(f"Risk: {risk_percentage*100}% of equity")
print(f"Stop Loss: {risk_atr_multiple}x ATR")
print(f"Take Profit: {tp_atr_multiple}x ATR")
print(f"SuperTrend Period: {supertrend_atr_period}")
print(f"SuperTrend Factor: {supertrend_factor}")
print(f"SSL Period: {ssl_period}")
print(f"Chop Length: {chop_length}")
print(f"Chop Threshold: {chop_threshold}")
print(f"Margin Type: {type}")
print(f"Dynamic leverage calculation: Enabled\n")

while True:
    try:
        cycle_count += 1
        print(f"\n=== CYCLE #{cycle_count} ===")
        
        # Check balance
        balance = get_balance_usdt()
        if balance is None:
            print(f"{TEXT_COLORS['RED']}Cannot connect to API. Check IP, restrictions or wait{TEXT_COLORS['RESET']}")
            sleep(60)
            continue
        
        print(f"Balance: {TEXT_COLORS['GREEN']}${balance:.2f} USDT{TEXT_COLORS['RESET']}")
        
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
            
            # Multiple detection methods for take profit fills
            if not first_tranche_closed:
                take_profit_detected = False
                detection_method = "None"
                
                # Method 1: Check recent order history for filled take profit orders
                try:
                    closed_orders = client.get_all_orders(symbol=symbol, limit=20)
                    for order in closed_orders:
                        # Check for recently filled take profit or limit sell/buy orders
                        if (order['status'] == 'FILLED' and 
                            # Check order types that could be take profits
                            any(tp_type in order['type'].upper() for tp_type in ['TAKE_PROFIT', 'LIMIT']) and
                            # Make sure it's a close order (opposite side of our position)
                            ((position['side'] == 'long' and order['side'] == 'SELL') or 
                             (position['side'] == 'short' and order['side'] == 'BUY')) and
                            # Check if it's recent (within last 10 minutes)
                            (int(time.time() * 1000) - order['time']) < 10 * 60 * 1000):
                            
                            take_profit_detected = True
                            detection_method = "Order History"
                            closed_size = float(order.get('executedQty', position['size'] / 2))
                            print(f"{TEXT_COLORS['GREEN']}Detected take profit order fill: ID {order['orderId']}, {closed_size} BTC{TEXT_COLORS['RESET']}")
                            break
                except Exception as e:
                    print(f"{TEXT_COLORS['RED']}Error checking order history: {str(e)}{TEXT_COLORS['RESET']}")
                
                # Method 2: Check position size compared to expected full size
                if not take_profit_detected:
                    try:
                        # Calculate what a typical "full" position size would be
                        expected_full_size = calculate_typical_position_size(balance, symbol)
                        
                        # If position size is noticeably smaller than expected full size
                        # But not too small (which would suggest multiple partial fills or another issue)
                        size_ratio = position['size'] / expected_full_size
                        original_entry_size = getattr(position, 'original_size', None) or expected_full_size
                        
                        if 0.4 <= size_ratio <= 0.7:  # Allowing for some variance in position calculations
                            take_profit_detected = True
                            detection_method = "Position Size"
                            closed_size = original_entry_size - position['size']
                            print(f"{TEXT_COLORS['YELLOW']}Position size ({position['size']:.8f} BTC) suggests partial take profit.{TEXT_COLORS['RESET']}")
                            print(f"Expected full size: ~{expected_full_size:.8f} BTC, ratio: {size_ratio:.2f}")
                    except Exception as e:
                        print(f"{TEXT_COLORS['RED']}Error in position size comparison: {str(e)}{TEXT_COLORS['RESET']}")
                
                # Method 3: Compare current position value to entry value
                if not take_profit_detected and 'entry_price' in globals() and position_entry_price > 0:
                    try:
                        # Look for significant PnL gains that suggest a take profit hit
                        if position['side'] == 'long' and position['mark_price'] >= current_take_profit:
                            take_profit_detected = True
                            detection_method = "Price Target"
                            closed_size = position['size']
                            print(f"{TEXT_COLORS['GREEN']}Current price (${position['mark_price']:.2f}) above take profit (${current_take_profit:.2f}){TEXT_COLORS['RESET']}")
                        elif position['side'] == 'short' and position['mark_price'] <= current_take_profit:
                            take_profit_detected = True
                            detection_method = "Price Target"
                            closed_size = position['size']
                            print(f"{TEXT_COLORS['GREEN']}Current price (${position['mark_price']:.2f}) below take profit (${current_take_profit:.2f}){TEXT_COLORS['RESET']}")
                    except Exception as e:
                        print(f"{TEXT_COLORS['RED']}Error in price comparison: {str(e)}{TEXT_COLORS['RESET']}")
                
                # Handle detected take profit
                if take_profit_detected:
                    print(f"{TEXT_COLORS['GREEN']}Take profit detected via {detection_method}!{TEXT_COLORS['RESET']}")
                    handle_take_profit_fill(symbol, position, closed_size)
            
            # Verify stop loss orders are correct if first tranche was closed
            if position and first_tranche_closed:
                # Verify that we actually have a stop loss order at breakeven
                stop_loss_verified = False
                try:
                    # Get open orders and check for stop loss orders
                    open_orders = client.get_open_orders(symbol=symbol)
                    
                    for order in open_orders:
                        if 'STOP' in order['type'].upper():
                            order_side = order['side']
                            stop_price = float(order.get('stopPrice', 0))
                            
                            # Check if it's our breakeven stop loss
                            correct_side = (position['side'] == 'long' and order_side == 'SELL') or \
                                        (position['side'] == 'short' and order_side == 'BUY')
                                        
                            # Allow a small deviation from exact breakeven (0.5%)
                            price_deviation = abs(stop_price - position_entry_price) / position_entry_price
                            is_at_breakeven = price_deviation < 0.005
                            
                            if correct_side and is_at_breakeven:
                                stop_loss_verified = True
                                print(f"{TEXT_COLORS['GREEN']}Verified breakeven stop loss at ${stop_price:.2f}{TEXT_COLORS['RESET']}")
                                break
                    
                    # If no valid stop loss found, create one
                    if not stop_loss_verified:
                        print(f"{TEXT_COLORS['RED']}No valid breakeven stop loss found! Creating one now.{TEXT_COLORS['RESET']}")
                        
                        # Cancel any existing stop orders first
                        for order in open_orders:
                            if 'STOP' in order['type'].upper():
                                try:
                                    client.cancel_order(symbol=symbol, orderId=order['orderId'])
                                    print(f"Canceled existing stop order: {order['orderId']}")
                                except Exception as e:
                                    print(f"Error canceling old stop order: {str(e)}")
                        
                        # Create new stop loss at breakeven
                        side = 'SELL' if position['side'] == 'long' else 'BUY'
                        try:
                            new_stop = client.new_order(
                                symbol=symbol,
                                side=side,
                                type='STOP_MARKET',
                                quantity=position['size'],
                                stopPrice=position_entry_price,
                                reduceOnly='true'
                            )
                            print(f"{TEXT_COLORS['GREEN']}Created new breakeven stop loss at ${position_entry_price:.2f}{TEXT_COLORS['RESET']}")
                        except Exception as e:
                            print(f"{TEXT_COLORS['RED']}Failed to create breakeven stop: {str(e)}{TEXT_COLORS['RESET']}")
                            print(f"{TEXT_COLORS['RED']}*** WARNING: POSITION HAS NO STOP LOSS ***{TEXT_COLORS['RESET']}")
                    
                except Exception as e:
                    print(f"{TEXT_COLORS['RED']}Error verifying stop loss: {str(e)}{TEXT_COLORS['RESET']}")
            
            # Check for exit signals
            exit_signal, reversal_info = check_exit_signals(symbol, position)
                
            if exit_signal == 'exit':
                # Store the reversal info before closing the position
                has_reversal = reversal_info['has_reversal']
                
                # Close the position
                if close_position(symbol, position):
                    # If we have a valid reversal signal, process it immediately
                    if has_reversal:
                        process_reversal(symbol, reversal_info)
            
            elif exit_signal in ['update_stop', 'trailing_activated']:
                update_stop_loss(symbol, position)
        
        else:
            # No position open, check for entry signals
            print(f"No open position, scanning for entry signals...")
            
            # Reset last attempted entry flag since we have no position
            last_attempted_entry = False
            
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
        
        # Generate final P&L summary
        generate_pnl_summary()
        
        # Close any open positions if requested
        if position and input("Close open position? (y/n): ").lower() == 'y':
            close_position(symbol, position)
        
        break
    
    except Exception as e:
        print(f"{TEXT_COLORS['RED']}Unexpected error: {str(e)}{TEXT_COLORS['RESET']}")
        traceback.print_exc()
        sleep(60)  # Wait a bit before retrying