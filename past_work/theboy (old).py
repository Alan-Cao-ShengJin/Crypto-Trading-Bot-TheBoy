import os
import sys
import time
import logging
import traceback
import pandas as pd
import numpy as np
import math
import json
from datetime import datetime, timedelta
from binance.um_futures import UMFutures
from binance.error import ClientError
from time import sleep

# Text color constants for better readability
TEXT_COLORS = {
    "GREEN": "\033[1;92m",
    "RED": "\033[1;91m",
    "YELLOW": "\033[1;93m",
    "BLUE": "\033[1;94m",
    "CYAN": "\033[1;96m",
    "RESET": "\033[0m",
}


class TheBoy:
    """
    Enhanced trading bot that implements SuperTrend + SSL Channel strategy
    with improved reliability, error handling, and state management.
    """

    def __init__(
        self,
        client,
        symbol="BTCUSDC",
        timeframe="1m",
        risk_percentage=0.02,
        risk_atr_period=11,
        risk_atr_multiple=1.0,
        tp_atr_multiple=1.0,
        trailing_atr_multiple=1.0,
        trailing_atr_trigger=2.0,
        supertrend_atr_period=14,
        supertrend_factor=2.0,
        ssl_period=4,
        chop_length=7,
        chop_threshold=44.0,
        margin_type="ISOLATED",
    ):
        """Initialize the trading bot with all necessary parameters."""
        # Client for API calls
        self.client = client

        # Trading parameters
        self.symbol = symbol
        self.timeframe = timeframe
        self.risk_percentage = risk_percentage
        self.risk_atr_period = risk_atr_period
        self.risk_atr_multiple = risk_atr_multiple
        self.tp_atr_multiple = tp_atr_multiple
        self.trailing_atr_multiple = trailing_atr_multiple
        self.trailing_atr_trigger = trailing_atr_trigger
        self.supertrend_atr_period = supertrend_atr_period
        self.supertrend_factor = supertrend_factor
        self.ssl_period = ssl_period
        self.chop_length = chop_length
        self.chop_threshold = chop_threshold
        self.margin_type = margin_type

        # Position state
        self.position = None
        self.position_entry_price = 0
        self.position_entry_atr = 0
        self.current_stop_loss = 0
        self.current_take_profit = 0
        self.first_tranche_closed = False
        self.trailing_stop_activated = False
        self.active_orders = {}  # Track active orders by type
        self.previous_position_size = 0  # Track previous position size to detect TP fills

        # Signal state
        self.prev_supertrend = None
        self.prev_ssl = None
        self.st_reset_detected = False
        self.last_signal_time = None
        self.last_signal_type = None
        self.last_attempted_entry = False
        self.waiting_for_new_candle = False  # Flag to track if we're waiting for a new candle
        
        # Add a configuration option for waiting behavior
        self.enable_candle_wait = True  # CHANGED: Set to True by default to wait for new candles
        
        # Candle tracking
        self.last_closed_candle_time = None
        self.current_open_candle_time = None
        self.latest_data = None  # Store latest klines data
        self.time_offset = 0  # Time offset between local and server time
        self.sync_server_time()  # Added: Synchronize time with server

        # Tracking and logging
        self.last_pnl_update = datetime.now()
        self.trade_history = []
        self.log_dir = "trading_logs"
        self.cycle_count = 0
        
        # Debug flags
        self.debug_signals = True  # Set to True to see detailed signal information
        
        # Account info
        self.account_balance = 0
        self.update_account_balance()  # Initialize account balance

        # Initialize
        self.init_trading_environment()

    def init_trading_environment(self):
        """Initialize the trading environment - create directories, etc."""
        # Ensure log directory exists
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
        self.trade_log_file = os.path.join(self.log_dir, "trade_log.txt")
        self.pnl_summary_file = os.path.join(self.log_dir, "pnl_summary.txt")

        # Print welcome message
        print(
            f"\n{TEXT_COLORS['CYAN']}=== ENHANCED CRYPTO TRADING BOT v2.4 ==={TEXT_COLORS['RESET']}"
        )
        print(f"{TEXT_COLORS['CYAN']}Strategy Parameters:{TEXT_COLORS['RESET']}")
        print(f"Timeframe: {self.timeframe}")
        print(f"Risk: {self.risk_percentage*100}% of equity")
        print(f"Stop Loss: {self.risk_atr_multiple}x ATR")
        print(f"Take Profit: {self.tp_atr_multiple}x ATR")
        print(f"SuperTrend Period: {self.supertrend_atr_period}")
        print(f"SuperTrend Factor: {self.supertrend_factor}")
        print(f"SSL Period: {self.ssl_period}")
        print(f"Chop Length: {self.chop_length}")
        print(f"Chop Threshold: {self.chop_threshold}")
        print(f"Margin Type: {self.margin_type}")
        print(f"Dynamic leverage calculation: Enabled")
        print(f"Wait for new candle: {self.enable_candle_wait}")
        print(f"Debug signals: {self.debug_signals}")
        print(f"For BTCUSDC, using min position size of 0.002 BTC")
        print(f"Account balance: ${self.account_balance:.2f} USDC\n")

    # =============== DATA FETCHING METHODS ===============

    def sync_server_time(self):
        """Synchronize local time with Binance server time with better error handling."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                server_time_info = self.client.time()
                server_time = datetime.fromtimestamp(server_time_info["serverTime"] / 1000)
                local_time = datetime.now()
                self.time_offset = (server_time - local_time).total_seconds()
                print(f"{TEXT_COLORS['GREEN']}Time synchronized with Binance server (offset: {self.time_offset:.2f}s){TEXT_COLORS['RESET']}")
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"{TEXT_COLORS['YELLOW']}Retry {attempt+1}/{max_retries} synchronizing time: {str(e)}{TEXT_COLORS['RESET']}")
                    sleep(1)
                else:
                    print(f"{TEXT_COLORS['RED']}Failed to synchronize time after {max_retries} attempts: {str(e)}{TEXT_COLORS['RESET']}")
                    self.time_offset = 0
                    return False

    def get_server_time(self):
        """Get current time adjusted to match Binance server time."""
        return datetime.now() + timedelta(seconds=self.time_offset)

    def update_account_balance(self):
        """Update and return account balance."""
        previous_balance = self.account_balance
        new_balance = self.get_balance_with_retry() or 0
        self.account_balance = new_balance
        
        # If balance changed, log it
        if previous_balance > 0 and abs(new_balance - previous_balance) > 0.01:
            change = new_balance - previous_balance
            change_pct = (change / previous_balance) * 100 if previous_balance > 0 else 0
            color = TEXT_COLORS['GREEN'] if change >= 0 else TEXT_COLORS['RED']
            print(f"{TEXT_COLORS['CYAN']}Balance updated: ${previous_balance:.2f} → ${new_balance:.2f} ({color}{change:+.2f} USDC, {change_pct:+.2f}%{TEXT_COLORS['RESET']})")
        else:
            print(f"{TEXT_COLORS['CYAN']}Current balance: ${new_balance:.2f} USDC{TEXT_COLORS['RESET']}")
            
        return new_balance

    def get_balance_with_retry(self, max_retries=3, retry_delay=2):
        """Get account balance with retry mechanism."""
        for attempt in range(max_retries):
            try:
                response = self.client.balance(recvWindow=6000)
                for elem in response:
                    if elem["asset"] == "USDC":
                        # Round down to 2 decimal places
                        return math.floor(float(elem["balance"]) * 100) / 100

                # If we get here, USDC wasn't found
                print(
                    f"{TEXT_COLORS['YELLOW']}USDC balance not found in account{TEXT_COLORS['RESET']}"
                )
                return 0

            except Exception as e:
                if attempt < max_retries - 1:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Retry {attempt+1}/{max_retries} getting balance: {str(e)}{TEXT_COLORS['RESET']}"
                    )
                    sleep(retry_delay)
                else:
                    print(
                        f"{TEXT_COLORS['RED']}Failed to get balance after {max_retries} attempts: {str(e)}{TEXT_COLORS['RESET']}"
                    )
                    return None

    def _get_candle_interval_minutes(self):
        """Helper function to convert timeframe to minutes"""
        if self.timeframe.endswith('m'):
            return int(self.timeframe[:-1])
        elif self.timeframe.endswith('h'):
            return int(self.timeframe[:-1]) * 60
        elif self.timeframe.endswith('d'):
            return int(self.timeframe[:-1]) * 60 * 24
        else:
            # Default to 1 minute if unknown
            return 1

    def _get_next_candle_close_time(self):
        """Get the precise time when the next candle will close."""
        now = self.get_server_time()
        interval_minutes = self._get_candle_interval_minutes()
        
        # Get minutes since start of day
        minutes_since_day_start = now.hour * 60 + now.minute
        
        # Calculate which interval we're in
        current_interval = minutes_since_day_start // interval_minutes
        
        # Find when the next interval ends
        next_interval_end_minutes = (current_interval + 1) * interval_minutes
        next_hour = next_interval_end_minutes // 60
        next_minute = next_interval_end_minutes % 60
        
        # Create the next candle close time
        next_close = now.replace(
            hour=next_hour, 
            minute=next_minute, 
            second=0, 
            microsecond=0
        )
        
        # If we're already past this time, add a day
        if next_close <= now:
            next_close += timedelta(days=1)
            
        return next_close

    def is_candle_closed(self, timestamp):
        """Check if a candle with the given timestamp should be closed by now with a safety margin."""
        now = self.get_server_time()
        candle_interval_minutes = self._get_candle_interval_minutes()
        
        # The candle close time is candle_open_time + interval_minutes
        candle_close_time = timestamp + timedelta(minutes=candle_interval_minutes)
        
        # Use a larger safety buffer (30 seconds) to ensure candle is fully closed
        safety_buffer = timedelta(seconds=30)
        
        is_closed = now > (candle_close_time + safety_buffer)
        
        # Debug logging
        if not is_closed and now > candle_close_time:
            time_since_close = (now - candle_close_time).total_seconds()
            print(f"{TEXT_COLORS['YELLOW']}Candle should be closed but waiting for safety buffer: {time_since_close:.2f}s elapsed{TEXT_COLORS['RESET']}")
        
        return is_closed

    def klines_with_retry(self, max_retries=3, retry_delay=2, exclude_current_candle=True):
        """
        Fetch klines data with enhanced retry mechanism and stricter candle closing checks.
        """
        # Always sync time before fetching candles
        self.sync_server_time()
        
        for attempt in range(max_retries):
            try:
                # Fetch more candles than needed
                fetch_limit = 200

                # Get candle data
                klines = self.client.klines(self.symbol, self.timeframe, limit=fetch_limit)
                
                if not klines:
                    raise ValueError(f"Empty response when fetching klines for {self.symbol}")

                # Convert to DataFrame
                resp = pd.DataFrame(klines)
                
                # Process columns
                resp = resp.iloc[:, :6]
                resp.columns = ["Time", "Open", "High", "Low", "Close", "Volume"]
                resp = resp.set_index("Time")
                resp.index = pd.to_datetime(resp.index, unit="ms")
                
                # Convert to numeric columns
                resp = resp.astype(float)
                
                # Enhanced handling for unclosed candles
                if exclude_current_candle and len(resp) > 0:
                    now = self.get_server_time()
                    server_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
                    print(f"Current server time: {server_time_str}")
                    
                    # Print recent candles for debugging
                    print("\nRecent candles (newest first):")
                    for i in range(min(5, len(resp)), 0, -1):
                        candle_time = resp.index[-i]
                        candle_interval = self._get_candle_interval_minutes()
                        candle_close_time = candle_time + timedelta(minutes=candle_interval)
                        time_diff = (now - candle_close_time).total_seconds() / 60
                        status = "CLOSED" if time_diff > 0.5 else "OPEN"
                        print(f"  {candle_time.strftime('%Y-%m-%d %H:%M:%S')} to {candle_close_time.strftime('%Y-%m-%d %H:%M:%S')} - {status} (diff: {time_diff:.2f}m)")
                    
                    # Find the most recent FULLY closed candle with enhanced safety
                    found_closed_candle = False
                    for i in range(len(resp)-1, -1, -1):
                        candle_time = resp.index[i]
                        candle_close_time = candle_time + timedelta(minutes=self._get_candle_interval_minutes())
                        
                        # Use stricter time checks with larger buffer
                        if now > candle_close_time + timedelta(seconds=30):
                            # Trim the dataframe to exclude unclosed candles
                            resp = resp.iloc[:i+1]
                            self.last_closed_candle_time = candle_time
                            found_closed_candle = True
                            print(f"{TEXT_COLORS['GREEN']}Last closed candle found at: {candle_time.strftime('%Y-%m-%d %H:%M:%S')} (closed at {candle_close_time.strftime('%H:%M:%S')}){TEXT_COLORS['RESET']}")
                            break
                    
                    if not found_closed_candle:
                        print(f"{TEXT_COLORS['RED']}No closed candles found! Current time: {server_time_str}{TEXT_COLORS['RESET']}")
                        return pd.DataFrame()
                
                # Store the data
                self.latest_data = resp
                
                if self.last_closed_candle_time:
                    print(f"{TEXT_COLORS['GREEN']}Using candle data up to {self.last_closed_candle_time.strftime('%Y-%m-%d %H:%M:%S')} (server time){TEXT_COLORS['RESET']}")
                    
                return resp

            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"{TEXT_COLORS['YELLOW']}Retry {attempt+1}/{max_retries} fetching klines: {str(e)}{TEXT_COLORS['RESET']}")
                    sleep(retry_delay)
                else:
                    print(f"{TEXT_COLORS['RED']}Failed to fetch klines after {max_retries} attempts: {str(e)}{TEXT_COLORS['RESET']}")
                    traceback.print_exc()
                    return None

    def get_next_candle_time(self):
        """Calculate the time when the next candle will close."""
        # Use more precise calculation
        return self._get_next_candle_close_time()

    def check_for_new_closed_candle(self):
        """Check if a new candle has closed since the last check."""
        try:
            # Remove redundant time sync - we'll sync periodically elsewhere
            # self.sync_server_time()  # Remove this line
            
            # Get just the last few candles
            candles = self.client.klines(self.symbol, self.timeframe, limit=5)
            if not candles:
                return False
                
            # Get the most recent candle timestamp
            latest_timestamp = pd.to_datetime(candles[-1][0], unit="ms")
            
            # If this is our first time checking, store the time
            if self.last_closed_candle_time is None:
                self.last_closed_candle_time = latest_timestamp
                return False
            
            # Check if candle is closed based on server time
            is_closed = self.is_candle_closed(latest_timestamp)
            
            if not is_closed:
                # Latest candle is still open
                return False
                
            # Check if we have a new closed candle
            if latest_timestamp > self.last_closed_candle_time:
                print(f"{TEXT_COLORS['GREEN']}New closed candle detected at {latest_timestamp.strftime('%Y-%m-%d %H:%M:%S')}!{TEXT_COLORS['RESET']}")
                self.last_closed_candle_time = latest_timestamp
                
                # Reset waiting flag when we confirm a new candle has closed
                if self.waiting_for_new_candle:
                    self.waiting_for_new_candle = False
                    print(f"{TEXT_COLORS['GREEN']}No longer waiting for new candle{TEXT_COLORS['RESET']}")
                    
                return True
                
            return False
            
        except Exception as e:
            print(f"{TEXT_COLORS['YELLOW']}Error checking for new candle: {str(e)}{TEXT_COLORS['RESET']}")
            return False

    def countdown_with_animation(self):
        """Display a countdown timer with a swirl animation until the next candle."""
        next_candle = self.get_next_candle_time()
        swirl_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        try:
            idx = 0
            # Track when we last synchronized time
            last_sync_time = time.time()
            
            while True:
                now = self.get_server_time()
                if now >= next_candle:
                    break

                time_left = (next_candle - now).total_seconds()
                if time_left <= 0:
                    break

                minutes, seconds = divmod(int(time_left), 60)
                time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
                swirl = swirl_chars[idx % len(swirl_chars)]
                waiting_msg = f" {TEXT_COLORS['YELLOW']}(Waiting for new candle){TEXT_COLORS['RESET']}" if self.waiting_for_new_candle else ""
                balance_msg = f" | Balance: ${self.account_balance:.2f}"
                
                message = f"\r{swirl} Next {self.timeframe} candle closes in {time_str}{waiting_msg}{balance_msg}"
                sys.stdout.write(message)
                sys.stdout.flush()

                idx += 1
                time.sleep(0.1)
                
                # Periodically check if a new candle has closed (every 5 seconds)
                if idx % 50 == 0:
                    sys.stdout.write("\r" + " " * (len(message) - len(waiting_msg)) + "\r")
                    sys.stdout.flush()
                    
                    # Only sync time every 5 minutes
                    current_time = time.time()
                    if current_time - last_sync_time > 300:
                        self.sync_server_time()
                        last_sync_time = current_time
                    
                    if self.check_for_new_closed_candle():
                        break

            sys.stdout.write("\r" + " " * (len(message) + 20) + "\r")
            sys.stdout.flush()
            self.check_for_new_closed_candle()

        except Exception as e:
            print(f"\n{TEXT_COLORS['YELLOW']}Error in countdown: {str(e)}{TEXT_COLORS['RESET']}")

    def get_price_precision(self, max_retries=2):
        """
        Get price precision and tick size for the symbol.
        
        Returns:
            Tuple of (price_precision, tick_size)
        """
        for attempt in range(max_retries):
            try:
                resp = self.client.exchange_info()
                for elem in resp["symbols"]:
                    if elem["symbol"] == self.symbol:
                        price_precision = elem["pricePrecision"]
                        
                        # Also extract tick size from price filter
                        tick_size = None
                        for filter_item in elem['filters']:
                            if filter_item['filterType'] == 'PRICE_FILTER':
                                tick_size = float(filter_item['tickSize'])
                                break
                        
                        return price_precision, tick_size
                        
                # If not found, return defaults
                print(f"{TEXT_COLORS['YELLOW']}Symbol {self.symbol} not found in exchange info. Using default precision.{TEXT_COLORS['RESET']}")
                return 1, 0.1  # Default fallback precision and tick size
            except Exception as e:
                if attempt < max_retries - 1:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Retry getting price precision: {str(e)}{TEXT_COLORS['RESET']}"
                    )
                    sleep(1)
                else:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Error getting price precision: {str(e)}. Using default.{TEXT_COLORS['RESET']}"
                    )
                    return 1, 0.1  # Default fallback precision and tick size

    def get_qty_precision(self, max_retries=2):
        """
        Get quantity precision, minimum size, and step size for the symbol.
        
        Returns:
            Tuple of (quantity_precision, min_size, step_size)
        """
        min_size = 0.002  # Default for BTCUSDC
        step_size = 0.001  # Default step size

        for attempt in range(max_retries):
            try:
                resp = self.client.exchange_info()
                for elem in resp["symbols"]:
                    if elem["symbol"] == self.symbol:
                        qty_precision = elem["quantityPrecision"]
                        
                        # Extract minimum and step size from LOT_SIZE filter
                        for filter_item in elem['filters']:
                            if filter_item['filterType'] == 'LOT_SIZE':
                                min_size = float(filter_item['minQty'])
                                step_size = float(filter_item['stepSize'])
                                break
                                
                        return qty_precision, min_size, step_size
                        
                # If not found, return defaults
                print(f"{TEXT_COLORS['YELLOW']}Symbol {self.symbol} not found in exchange info. Using default qty precision.{TEXT_COLORS['RESET']}")
                return 3, min_size, step_size  # Default fallback
            except Exception as e:
                if attempt < max_retries - 1:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Retry getting quantity precision: {str(e)}{TEXT_COLORS['RESET']}"
                    )
                    sleep(1)
                else:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Error getting quantity precision: {str(e)}. Using default.{TEXT_COLORS['RESET']}"
                    )
                    return 3, min_size, step_size  # Fallback precision

    # =============== INDICATOR CALCULATION METHODS ===============

    def calculate_supertrend(self, df, period=None, multiplier=None):
        """Calculate SuperTrend indicator with TradingView-compatible implementation."""
        df = df.copy()
        period = period or self.supertrend_atr_period
        multiplier = multiplier or self.supertrend_factor

        # Calculate ATR
        df["tr1"] = df["High"] - df["Low"]
        df["tr2"] = abs(df["High"] - df["Close"].shift(1))
        df["tr3"] = abs(df["Low"] - df["Close"].shift(1))
        df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
        df["atr"] = df["tr"].rolling(period).mean()

        # Calculate basic bands
        df["hl2"] = (df["High"] + df["Low"]) / 2
        df["upperband"] = df["hl2"] + (multiplier * df["atr"])
        df["lowerband"] = df["hl2"] - (multiplier * df["atr"])

        # Initialize SuperTrend columns
        df["final_upperband"] = df["upperband"]
        df["final_lowerband"] = df["lowerband"]
        df["supertrend"] = 0

        # Calculate final bands - TradingView compatible method
        for i in range(1, len(df)):
            # Upper band
            if (df["final_upperband"].iloc[i-1] == df["upperband"].iloc[i-1]):
                df.loc[df.index[i], "final_upperband"] = min(df["upperband"].iloc[i], df["final_upperband"].iloc[i-1])
            else:
                df.loc[df.index[i], "final_upperband"] = df["upperband"].iloc[i]
            
            # Lower band
            if (df["final_lowerband"].iloc[i-1] == df["lowerband"].iloc[i-1]):
                df.loc[df.index[i], "final_lowerband"] = max(df["lowerband"].iloc[i], df["final_lowerband"].iloc[i-1])
            else:
                df.loc[df.index[i], "final_lowerband"] = df["lowerband"].iloc[i]
                
        # Calculate SuperTrend value and direction (TradingView style)
        for i in range(1, len(df)):
            # Initialize with previous
            if i == 1:
                # Default to uptrend on first candle
                df.loc[df.index[i], "supertrend"] = 1
                continue
                
            # Previous values
            prev_supertrend = df["supertrend"].iloc[i-1]
            curr_close = df["Close"].iloc[i]
            curr_upper = df["final_upperband"].iloc[i]
            curr_lower = df["final_lowerband"].iloc[i]
            
            # Determine trend
            if prev_supertrend == 1:
                # Still in uptrend if close is above lower band
                if curr_close > curr_lower:
                    df.loc[df.index[i], "supertrend"] = 1
                # Switched to downtrend
                else:
                    df.loc[df.index[i], "supertrend"] = -1
            else:
                # Still in downtrend if close is below upper band
                if curr_close < curr_upper:
                    df.loc[df.index[i], "supertrend"] = -1
                # Switched to uptrend
                else:
                    df.loc[df.index[i], "supertrend"] = 1
                    
        # Convert to our naming convention
        df["trend"] = df["supertrend"]

        # Generate buy/sell signals
        df["buySignal"] = (df["trend"] == 1) & (df["trend"].shift(1) == -1)
        df["sellSignal"] = (df["trend"] == -1) & (df["trend"].shift(1) == 1)

        # Print debug info for recent candles
        if self.debug_signals:
            last_rows = df.tail(3)
            print(f"\n{TEXT_COLORS['CYAN']}SuperTrend Last 3 Rows:{TEXT_COLORS['RESET']}")
            for i, (idx, row) in enumerate(last_rows.iterrows()):
                print(f"Row {len(df)-3+i}: Time={idx}")
                print(f"  Close: ${row['Close']:.2f}, ATR: ${row['atr']:.2f}")
                print(f"  Upper: ${row['final_upperband']:.2f}, Lower: ${row['final_lowerband']:.2f}")
                print(f"  Trend: {row['trend']} ({('Uptrend' if row['trend'] == 1 else 'Downtrend')})")
                
        return df

    def calculate_ssl_channel(self, df, period=None):
        """Calculate SSL Channel indicator."""
        df = df.copy()
        period = period or self.ssl_period

        # Calculate moving averages of high and low
        df["sma_high"] = df["High"].rolling(window=period).mean()
        df["sma_low"] = df["Low"].rolling(window=period).mean()

        # Initialize SSL column
        df["ssl"] = 0

        # Set SSL to 1 where Close > sma_high (uptrend), -1 where Close < sma_low (downtrend)
        df.loc[df["Close"] > df["sma_high"], "ssl"] = 1
        df.loc[df["Close"] < df["sma_low"], "ssl"] = -1

        # Fill forward to maintain the trend when neither condition is met
        df["ssl"] = df["ssl"].replace(0, np.nan).ffill().fillna(0)

        # Generate signals
        df["ssl_long_signal"] = (df["ssl"] == 1) & (df["ssl"].shift(1) == -1)
        df["ssl_short_signal"] = (df["ssl"] == -1) & (df["ssl"].shift(1) == 1)

        return df

    def calculate_chop(self, df, length=None):
        """Calculate Choppiness Index."""
        df = df.copy()
        length = length or self.chop_length

        # Calculate True Range
        high_low = df["High"] - df["Low"]
        high_close = abs(df["High"] - df["Close"].shift(1))
        low_close = abs(df["Low"] - df["Close"].shift(1))
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)

        # Calculate the sum of TR over the lookback period
        sum_tr = true_range.rolling(window=length).sum()

        # Calculate highest high and lowest low over the lookback period
        highest_high = df["High"].rolling(window=length).max()
        lowest_low = df["Low"].rolling(window=length).min()

        # Calculate range (highest high - lowest low)
        price_range = highest_high - lowest_low

        # Calculate Choppiness Index with safe division
        with np.errstate(divide="ignore", invalid="ignore"):
            chop = 100 * np.log10(sum_tr / price_range) / np.log10(length)

        # Handle infinity and NaN values
        df["chop"] = chop.replace([np.inf, -np.inf], np.nan).fillna(50)
        df["is_choppy"] = df["chop"] >= self.chop_threshold

        return df

    # =============== SIGNAL GENERATION METHODS ===============

    def debug_indicator_values(self, df, index=-1):
        """Print detailed indicator values for debugging"""
        if not self.debug_signals or df.empty:
            return
            
        try:
            # Get values from the specified index (default is last row)
            row = df.iloc[index]
            
            # Get server-adjusted time
            candle_time = df.index[index]
            
            print(f"\n{TEXT_COLORS['CYAN']}INDICATOR VALUES (index {index}):{TEXT_COLORS['RESET']}")
            print(f"Time: {candle_time.strftime('%Y-%m-%d %H:%M:%S')} (Candle time)")
            print(f"Current server time: {self.get_server_time().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Price: Open=${row['Open']:.2f}, High=${row['High']:.2f}, Low=${row['Low']:.2f}, Close=${row['Close']:.2f}")
            print(f"Account Balance: ${self.account_balance:.2f}")
            
            # SuperTrend values
            if 'trend' in df.columns:
                print(f"SuperTrend: {row['trend']} ({'Uptrend' if row['trend'] == 1 else 'Downtrend'})")
                print(f"SuperTrend Buy Signal: {row['buySignal']}")
                print(f"SuperTrend Sell Signal: {row['sellSignal']}")
                print(f"ATR: {row['atr']:.2f}")
            
            # SSL values
            if 'ssl' in df.columns:
                print(f"SSL: {row['ssl']} ({'Uptrend' if row['ssl'] == 1 else 'Downtrend' if row['ssl'] == -1 else 'Neutral'})")
                print(f"SSL Long Signal: {row['ssl_long_signal']}")
                print(f"SSL Short Signal: {row['ssl_short_signal']}")
            
            # Chop values
            if 'chop' in df.columns:
                print(f"Chop: {row['chop']:.2f} ({'Choppy' if row['is_choppy'] else 'Trending'})")
                
            # Previous values for reference
            print(f"\nPrevious values:")
            print(f"Previous SuperTrend: {self.prev_supertrend}")
            print(f"Previous SSL: {self.prev_ssl}")
            print(f"ST Reset Detected: {self.st_reset_detected}")
            print(f"Waiting for new candle: {self.waiting_for_new_candle}")
            
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error in debug_indicator_values: {str(e)}{TEXT_COLORS['RESET']}")

    def signal_strategy(self):
        """SuperTrend + SSL Channel strategy signal with improved error handling."""
        # Fetch candle data with retry, ensuring we exclude the current unclosed candle
        kl = self.klines_with_retry(exclude_current_candle=True)
        
        if kl is None or kl.empty or len(kl) < max(
            self.supertrend_atr_period, self.ssl_period, self.chop_length
        ):
            print(
                f"{TEXT_COLORS['RED']}Not enough data to calculate indicators{TEXT_COLORS['RESET']}"
            )
            return "none", 0, 0

        # Check if we're using the most recent closed candle
        if self.last_closed_candle_time and kl.index[-1] == self.last_closed_candle_time:
            print(f"{TEXT_COLORS['GREEN']}Using latest closed candle for signal generation{TEXT_COLORS['RESET']}")
        else:
            print(f"{TEXT_COLORS['YELLOW']}Warning: Latest candle time mismatch. Expected {self.last_closed_candle_time}, got {kl.index[-1]}.{TEXT_COLORS['RESET']}")

        # Calculate indicators
        try:
            df = self.calculate_supertrend(kl)
            df = self.calculate_ssl_channel(df)
            df = self.calculate_chop(df)
        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error calculating indicators: {str(e)}{TEXT_COLORS['RESET']}"
            )
            traceback.print_exc()
            return "none", 0, 0

        # Debug indicator values
        self.debug_indicator_values(df)

        # Get latest values
        st_trend = df["trend"].iloc[-1]
        ssl = df["ssl"].iloc[-1]
        st_buy_signal = df["buySignal"].iloc[-1]
        st_sell_signal = df["sellSignal"].iloc[-1]
        ssl_long_signal = df["ssl_long_signal"].iloc[-1]
        ssl_short_signal = df["ssl_short_signal"].iloc[-1]
        is_choppy = df["is_choppy"].iloc[-1]
        chop_value = df["chop"].iloc[-1]
        atr = df["atr"].iloc[-1]
        current_price = df["Close"].iloc[-1]

        # Initialize previous values if None
        if self.prev_supertrend is None:
            self.prev_supertrend = st_trend
        if self.prev_ssl is None:
            self.prev_ssl = ssl

        # Check for trend flips
        supertrend_up_flip = st_buy_signal
        supertrend_down_flip = st_sell_signal
        ssl_up_flip = ssl_long_signal
        ssl_down_flip = ssl_short_signal

        # Create a unique signal identifier for this candle
        current_time = df.index[-1].strftime("%Y-%m-%d %H:%M:%S") if len(df.index) > 0 else self.get_server_time().strftime("%Y-%m-%d %H:%M:%S")

        # Log trend flips
        if supertrend_up_flip:
            print(
                f"{TEXT_COLORS['GREEN']}SuperTrend flip to UPTREND detected{TEXT_COLORS['RESET']}"
            )
        if supertrend_down_flip:
            print(
                f"{TEXT_COLORS['RED']}SuperTrend flip to DOWNTREND detected{TEXT_COLORS['RESET']}"
            )
        if ssl_up_flip:
            print(
                f"{TEXT_COLORS['GREEN']}SSL flip to UPTREND detected{TEXT_COLORS['RESET']}"
            )
        if ssl_down_flip:
            print(
                f"{TEXT_COLORS['RED']}SSL flip to DOWNTREND detected{TEXT_COLORS['RESET']}"
            )

        # Check for SuperTrend reset (important for choppiness filter)
        self.st_reset_detected = supertrend_up_flip or supertrend_down_flip

        # Entry conditions - improved logic
        # Long entry conditions:
        # 1. SSL flips to uptrend AND SuperTrend is in uptrend
        # 2. OR SuperTrend flips to uptrend AND SSL is in uptrend
        long_entry = (ssl_up_flip and st_trend == 1) or (supertrend_up_flip and ssl == 1)
        
        # Short entry conditions:
        # 1. SSL flips to downtrend AND SuperTrend is in downtrend
        # 2. OR SuperTrend flips to downtrend AND SSL is in downtrend
        short_entry = (ssl_down_flip and st_trend == -1) or (supertrend_down_flip and ssl == -1)

        # Log choppiness
        if is_choppy:
            print(
                f"{TEXT_COLORS['YELLOW']}Market is choppy (Chop: {chop_value:.2f}){TEXT_COLORS['RESET']}"
            )
        else:
            print(
                f"{TEXT_COLORS['GREEN']}Market is trending (Chop: {chop_value:.2f}){TEXT_COLORS['RESET']}"
            )

        # Reject signals if market is choppy and SuperTrend just reset
        if long_entry and self.st_reset_detected and is_choppy:
            print(
                f"{TEXT_COLORS['RED']}LONG signal detected but market too choppy - REJECTED{TEXT_COLORS['RESET']}"
            )
            long_entry = False

        if short_entry and self.st_reset_detected and is_choppy:
            print(
                f"{TEXT_COLORS['RED']}SHORT signal detected but market too choppy - REJECTED{TEXT_COLORS['RESET']}"
            )
            short_entry = False
            
        # Check if we're waiting for a new candle before allowing entry
        if self.waiting_for_new_candle and self.enable_candle_wait:
            if long_entry or short_entry:
                print(f"{TEXT_COLORS['YELLOW']}Signal detected but waiting for next candle to confirm - WAITING{TEXT_COLORS['RESET']}")
                long_entry = False
                short_entry = False

        # Check if we've already attempted to trade this signal
        if (
            self.last_signal_time is not None
            and self.last_signal_time == current_time
            and (
                (long_entry and self.last_signal_type == "up")
                or (short_entry and self.last_signal_type == "down")
            )
            and self.last_attempted_entry == True
        ):
            print(
                f"{TEXT_COLORS['YELLOW']}Already attempted to trade this signal - skipping{TEXT_COLORS['RESET']}"
            )
            long_entry = False
            short_entry = False

        # Update state
        self.prev_supertrend = st_trend
        self.prev_ssl = ssl

        # Log and return signal
        if long_entry:
            print(
                f"{TEXT_COLORS['GREEN']}LONG ENTRY SIGNAL: {'SSL flip + ST uptrend' if ssl_up_flip else 'ST flip + SSL uptrend'}{TEXT_COLORS['RESET']}"
            )
            # Update last signal info
            self.last_signal_time = current_time
            self.last_signal_type = "up"
            return "up", atr, current_price
        elif short_entry:
            print(
                f"{TEXT_COLORS['RED']}SHORT ENTRY SIGNAL: {'SSL flip + ST downtrend' if ssl_down_flip else 'ST flip + SSL downtrend'}{TEXT_COLORS['RESET']}"
            )
            # Update last signal info
            self.last_signal_time = current_time
            self.last_signal_type = "down"
            return "down", atr, current_price
        else:
            return "none", atr, current_price

    def check_exit_signals(self, position):
        """Improved exit signal detection with better reversal confirmation."""
        # Get candle data and calculate indicators, excluding current unclosed candle
        kl = self.klines_with_retry(exclude_current_candle=True)
        if kl is None or kl.empty or len(kl) < max(
            self.supertrend_atr_period, self.ssl_period, self.chop_length
        ):
            print(
                f"{TEXT_COLORS['RED']}Not enough data to calculate exit signals{TEXT_COLORS['RESET']}"
            )
            return None, {"has_reversal": False}

        try:
            df = self.calculate_supertrend(kl)
            df = self.calculate_ssl_channel(df)
            df = self.calculate_chop(df)
        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error calculating indicators for exit: {str(e)}{TEXT_COLORS['RESET']}"
            )
            return None, {"has_reversal": False}

        # Debug indicator values
        if self.debug_signals:
            self.debug_indicator_values(df)

        current_price = df["Close"].iloc[-1]

        # Get key indicator values
        st_trend = df["trend"].iloc[-1]
        ssl = df["ssl"].iloc[-1]
        is_choppy = df["is_choppy"].iloc[-1]
        chop_value = df["chop"].iloc[-1]
        st_buy_signal = df["buySignal"].iloc[-1]
        st_sell_signal = df["sellSignal"].iloc[-1]

        # Prepare reversal info dictionary
        reversal_info = {
            "has_reversal": False,
            "reversal_side": None,
            "st_reset_detected": False,
            "is_choppy": is_choppy,
            "chop_value": chop_value,
            "atr": df["atr"].iloc[-1] if "atr" in df.columns else 0,
            "current_price": current_price,
        }

        # Check for SuperTrend flip exit - with potential for reversal
        if position["side"] == "long" and st_sell_signal:
            print(
                f"{TEXT_COLORS['YELLOW']}EXIT SIGNAL: SuperTrend flipped to downtrend{TEXT_COLORS['RESET']}"
            )

            # Add confirmation check for reversal - both indicators must align
            # SSL must already be in downtrend when SuperTrend flips to down
            if ssl == -1 and not is_choppy:
                reversal_info["has_reversal"] = True
                reversal_info["reversal_side"] = "sell"
                reversal_info["st_reset_detected"] = True
                print(
                    f"{TEXT_COLORS['YELLOW']}REVERSAL DETECTED: Short conditions met after SuperTrend flip{TEXT_COLORS['RESET']}"
                )

            return "exit", reversal_info

        elif position["side"] == "short" and st_buy_signal:
            print(
                f"{TEXT_COLORS['YELLOW']}EXIT SIGNAL: SuperTrend flipped to uptrend{TEXT_COLORS['RESET']}"
            )

            # Add confirmation check for reversal - both indicators must align
            # SSL must already be in uptrend when SuperTrend flips to up
            if ssl == 1 and not is_choppy:
                reversal_info["has_reversal"] = True
                reversal_info["reversal_side"] = "buy"
                reversal_info["st_reset_detected"] = True
                print(
                    f"{TEXT_COLORS['YELLOW']}REVERSAL DETECTED: Long conditions met after SuperTrend flip{TEXT_COLORS['RESET']}"
                )

            return "exit", reversal_info

        # Improved trailing stop management
        if self.trailing_stop_activated:
            if position["side"] == "long":
                new_stop = current_price - (
                    self.trailing_atr_multiple * self.position_entry_atr
                )
                if new_stop > self.current_stop_loss:
                    old_stop = self.current_stop_loss
                    self.current_stop_loss = new_stop
                    print(
                        f"{TEXT_COLORS['GREEN']}TRAILING STOP UPDATED: ${old_stop:.2f} → ${new_stop:.2f}{TEXT_COLORS['RESET']}"
                    )
                    return "update_stop", reversal_info
            else:  # short position
                new_stop = current_price + (
                    self.trailing_atr_multiple * self.position_entry_atr
                )
                if new_stop < self.current_stop_loss:
                    old_stop = self.current_stop_loss
                    self.current_stop_loss = new_stop
                    print(
                        f"{TEXT_COLORS['GREEN']}TRAILING STOP UPDATED: ${old_stop:.2f} → ${new_stop:.2f}{TEXT_COLORS['RESET']}"
                    )
                    return "update_stop", reversal_info

        # Check if we should activate trailing stop (after first take profit hit)
        elif self.first_tranche_closed and not self.trailing_stop_activated:
            activation_threshold = (
                self.position_entry_price
                + (self.trailing_atr_trigger * self.position_entry_atr)
                if position["side"] == "long"
                else self.position_entry_price
                - (self.trailing_atr_trigger * self.position_entry_atr)
            )

            activation_condition = (
                position["side"] == "long" and current_price >= activation_threshold
            ) or (position["side"] == "short" and current_price <= activation_threshold)

            if activation_condition:
                self.trailing_stop_activated = True
                self.current_stop_loss = (
                    current_price
                    - (self.trailing_atr_multiple * self.position_entry_atr)
                    if position["side"] == "long"
                    else current_price
                    + (self.trailing_atr_multiple * self.position_entry_atr)
                )

                print(
                    f"{TEXT_COLORS['GREEN']}TRAILING STOP ACTIVATED at ${self.current_stop_loss:.2f}{TEXT_COLORS['RESET']}"
                )
                return "trailing_activated", reversal_info

        return None, reversal_info

    def process_reversal(self, reversal_info):
        """Process a reversal signal after position close using market orders."""
        if not reversal_info.get("has_reversal", False):
            return False

        reversal_side = reversal_info.get("reversal_side")
        atr_value = reversal_info.get("atr", 0)
        current_price = reversal_info.get("current_price", 0)
        is_choppy = reversal_info.get("is_choppy", False)
        st_reset_detected = reversal_info.get("st_reset_detected", False)

        if not reversal_side or not atr_value or not current_price:
            print(
                f"{TEXT_COLORS['YELLOW']}Incomplete reversal info - skipping reversal{TEXT_COLORS['RESET']}"
            )
            return False

        # Reject if market is choppy and SuperTrend just reset
        if is_choppy and st_reset_detected:
            print(
                f"{TEXT_COLORS['YELLOW']}REJECTED REVERSAL: Market too choppy (Chop: {reversal_info.get('chop_value', 0):.2f}){TEXT_COLORS['RESET']}"
            )
            return False

        print(
            f"{TEXT_COLORS['YELLOW']}EXECUTING IMMEDIATE REVERSAL WITH MARKET ORDER: {reversal_side.upper()} after trend flip{TEXT_COLORS['RESET']}"
        )

        # Wait a moment to ensure position is fully closed before reversing
        sleep(1)

        # Execute the reversal entry as a market order
        return self.open_order(reversal_side, atr_value, current_price, use_market=True)

    # =============== TRADE EXECUTION METHODS ===============

    def set_margin_type_with_retry(self, max_retries=2):
        """Set margin type with retry mechanism."""
        for attempt in range(max_retries):
            try:
                response = self.client.change_margin_type(
                    symbol=self.symbol, marginType=self.margin_type, recvWindow=6000
                )
                print(
                    f"{TEXT_COLORS['GREEN']}Margin type set to {self.margin_type}{TEXT_COLORS['RESET']}"
                )
                return True
            except ClientError as error:
                if "No need to change margin type" in str(error):
                    print(
                        f"{TEXT_COLORS['GREEN']}Margin type already set to {self.margin_type}{TEXT_COLORS['RESET']}"
                    )
                    return True
                elif "Unable to adjust to isolated-margin mode" in str(error):
                    print(
                        f"{TEXT_COLORS['YELLOW']}Could not set to {self.margin_type}, using CROSS instead{TEXT_COLORS['RESET']}"
                    )
                    # Try to set it to CROSS instead
                    try:
                        response = self.client.change_margin_type(
                            symbol=self.symbol, marginType="CROSS", recvWindow=6000
                        )
                        self.margin_type = "CROSS"  # Update the instance variable
                        print(
                            f"{TEXT_COLORS['GREEN']}Margin type set to CROSS{TEXT_COLORS['RESET']}"
                        )
                        return True
                    except Exception:
                        # Probably already in CROSS mode
                        self.margin_type = "CROSS"  # Update the instance variable
                        print(
                            f"{TEXT_COLORS['YELLOW']}Assuming CROSS margin mode is active{TEXT_COLORS['RESET']}"
                        )
                        return True
                elif attempt < max_retries - 1:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Retry setting margin type: {str(error)}{TEXT_COLORS['RESET']}"
                    )
                    sleep(1)
                else:
                    print(
                        f"{TEXT_COLORS['RED']}Failed to set margin type: {str(error)}{TEXT_COLORS['RESET']}"
                    )
                    return False

    def set_leverage_with_retry(self, leverage, max_retries=2):
        """Set leverage with retry mechanism."""
        for attempt in range(max_retries):
            try:
                response = self.client.change_leverage(
                    symbol=self.symbol, leverage=leverage, recvWindow=6000
                )
                print(
                    f"{TEXT_COLORS['GREEN']}Leverage set to {leverage}x{TEXT_COLORS['RESET']}"
                )
                return leverage  # Return the actual leverage value, not True
            except ClientError as error:
                if "Maximum leverage" in str(error):
                    try:
                        # Extract maximum allowed leverage from error message
                        msg = str(error)
                        max_allowed = int(
                            "".join(
                                filter(
                                    str.isdigit,
                                    msg.split("Maximum leverage")[1].split(".")[0],
                                )
                            )
                        )
                        if max_allowed > 0:
                            print(
                                f"{TEXT_COLORS['YELLOW']}Max leverage is {max_allowed}x. Setting to that.{TEXT_COLORS['RESET']}"
                            )
                            response = self.client.change_leverage(
                                symbol=self.symbol,
                                leverage=max_allowed,
                                recvWindow=6000,
                            )
                            print(
                                f"{TEXT_COLORS['GREEN']}Leverage set to {max_allowed}x{TEXT_COLORS['RESET']}"
                            )
                            return max_allowed
                    except Exception:
                        # If we can't extract the max leverage, try common values
                        for lev in [20, 10, 5, 3, 1]:
                            try:
                                response = self.client.change_leverage(
                                    symbol=self.symbol, leverage=lev, recvWindow=6000
                                )
                                print(
                                    f"{TEXT_COLORS['GREEN']}Leverage set to {lev}x{TEXT_COLORS['RESET']}"
                                )
                                return lev
                            except Exception:
                                continue
                elif attempt < max_retries - 1:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Retry setting leverage: {str(error)}{TEXT_COLORS['RESET']}"
                    )
                    sleep(1)
                else:
                    print(
                        f"{TEXT_COLORS['RED']}Failed to set leverage: {str(error)}{TEXT_COLORS['RESET']}"
                    )
                    # Return 1 as the safest fallback
                    return 1
        return 1  # Fallback to 1x leverage if all attempts fail

    def setup_position(
        self, equity, risk_percentage=None, atr_multiple=None, equity_buffer=0.95
    ):
        """
        Calculate optimal position size and leverage based on risk parameters.

        Args:
            equity: Account equity
            risk_percentage: Percentage of equity to risk (defaults to instance value)
            atr_multiple: ATR multiple for stop loss (defaults to instance value)
            equity_buffer: Safety buffer for equity calculations (defaults to 95%)

        Returns:
            Dictionary with position details
        """
        risk_percentage = risk_percentage or self.risk_percentage
        atr_multiple = atr_multiple or self.risk_atr_multiple
        min_size = 0.002  # Minimum position size in BTC
        max_leverage = 125  # Maximum allowed leverage

        try:
            # Get current price and ATR
            ticker_data = self.client.ticker_price(symbol=self.symbol)
            current_price = float(ticker_data["price"])

            # Calculate ATR using historical data
            klines_data = self.klines_with_retry(max_retries=2, exclude_current_candle=True)
            if klines_data is None or klines_data.empty:
                # Use a fallback ATR value of 1% of price if we can't calculate it
                atr_value = current_price * 0.01
                print(
                    f"{TEXT_COLORS['YELLOW']}Using fallback ATR value of ${atr_value:.2f}{TEXT_COLORS['RESET']}"
                )
            else:
                df = self.calculate_supertrend(klines_data)
                atr_value = df["atr"].iloc[-1]

                # Validate ATR value
                if np.isnan(atr_value) or atr_value <= 0:
                    atr_value = current_price * 0.01
                    print(
                        f"{TEXT_COLORS['YELLOW']}Invalid ATR, using fallback value of ${atr_value:.2f}{TEXT_COLORS['RESET']}"
                    )

            # Calculate stop loss distance in price
            stop_distance = atr_multiple * atr_value

            # Calculate risk amount in dollars
            risk_amount = equity * risk_percentage

            # Calculate position size based on risk (the core of proper sizing)
            position_size_raw = risk_amount / stop_distance

            # Determine if this size meets the minimum requirements
            if position_size_raw < min_size:
                print(
                    f"{TEXT_COLORS['YELLOW']}Risk-based size {position_size_raw:.8f} BTC is below minimum {min_size} BTC{TEXT_COLORS['RESET']}"
                )
                # Calculate leverage needed to reach minimum size
                leverage_for_min = min_size / position_size_raw
                # Use this as our starting point for leverage
                leverage = math.ceil(leverage_for_min)
                position_size = min_size
            else:
                # No need for special leverage, start with 1x
                leverage = 1
                position_size = position_size_raw

            # Calculate position value and required margin
            position_value = position_size * current_price
            required_margin = position_value / leverage

            # Check if required margin exceeds our buffered equity
            buffered_equity = equity * equity_buffer

            if required_margin > buffered_equity:
                # Calculate minimum leverage needed
                min_leverage_needed = math.ceil(position_value / buffered_equity)

                if min_leverage_needed <= max_leverage:
                    # We can increase leverage to make this work
                    leverage = min_leverage_needed
                    required_margin = position_value / leverage
                else:
                    # We need to reduce position size to fit within max leverage
                    leverage = max_leverage
                    max_position_value = buffered_equity * max_leverage
                    position_size = max_position_value / current_price
                    required_margin = position_value / leverage
                    print(
                        f"{TEXT_COLORS['YELLOW']}Reduced position size to fit within maximum leverage{TEXT_COLORS['RESET']}"
                    )

            # Apply precision to position size
            qty_precision, min_size, step_size = self.get_qty_precision()
            position_size = round(position_size, qty_precision)

            # Minimum check
            if position_size < min_size:
                position_size = min_size

            # Recalculate final values
            position_value = position_size * current_price
            required_margin = position_value / leverage
            actual_risk = position_size * stop_distance
            risk_percentage_actual = (actual_risk / equity) * 100

            # Set the leverage (this actually communicates with the exchange)
            set_leverage = self.set_leverage_with_retry(leverage)

            # Log position details for debugging
            print(f"\n{TEXT_COLORS['CYAN']}Position Details:{TEXT_COLORS['RESET']}")
            print(f"Equity: ${equity:.2f}")
            print(f"Price: ${current_price:.2f}")
            print(f"ATR: ${atr_value:.2f}")
            print(f"Stop Distance: ${stop_distance:.2f}")
            print(f"Position Size: {position_size} BTC")
            print(f"Position Value: ${position_value:.2f}")
            print(f"Leverage: {set_leverage}x")
            print(f"Required Margin: ${required_margin:.2f}")
            print(f"Actual Risk: ${actual_risk:.2f} ({risk_percentage_actual:.2f}%)")

            return position_size, set_leverage

        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error in setup_position: {str(e)}{TEXT_COLORS['RESET']}"
            )
            traceback.print_exc()
            # Return safe fallback values
            return 0.002, 1  # Minimum size, 1x leverage

    def cancel_all_orders(self):
        """Cancel all open orders for the current symbol."""
        try:
            result = self.client.cancel_open_orders(symbol=self.symbol, recvWindow=6000)
            print(f"{TEXT_COLORS['YELLOW']}Canceled all open orders for {self.symbol}{TEXT_COLORS['RESET']}")
            # Clear our active orders tracking
            self.active_orders = {}
            return True
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error canceling orders: {str(e)}{TEXT_COLORS['RESET']}")
            return False

    def get_open_positions(self, max_retries=3, retry_delay=2):
        """Get current open positions with retry mechanism."""
        for attempt in range(max_retries):
            try:
                positions = self.client.get_position_risk()
                for pos in positions:
                    if pos["symbol"] == self.symbol and float(pos["positionAmt"]) != 0:
                        side = "long" if float(pos["positionAmt"]) > 0 else "short"
                        size = abs(float(pos["positionAmt"]))
                        entry_price = float(pos["entryPrice"])
                        mark_price = float(pos["markPrice"])
                        pnl = float(pos["unRealizedProfit"])

                        return {
                            "symbol": self.symbol,
                            "side": side,
                            "size": size,
                            "entry_price": entry_price,
                            "mark_price": mark_price,
                            "pnl": pnl,
                        }
                return None
            except Exception as e:
                if attempt < max_retries - 1:
                    print(
                        f"{TEXT_COLORS['YELLOW']}Retry {attempt+1}/{max_retries} getting positions: {str(e)}{TEXT_COLORS['RESET']}"
                    )
                    time.sleep(retry_delay)
                else:
                    print(
                        f"{TEXT_COLORS['RED']}Failed to get positions after {max_retries} attempts: {str(e)}{TEXT_COLORS['RESET']}"
                    )
                    return None

    def get_open_orders_by_type(self):
        """Get all open orders organized by type."""
        try:
            orders = self.client.get_open_orders(symbol=self.symbol, recvWindow=6000)
            order_map = {"stop": [], "tp": [], "limit": [], "other": []}
            
            for order in orders:
                order_type = order.get("type", "").upper()
                if "STOP" in order_type:
                    order_map["stop"].append(order)
                elif "TAKE_PROFIT" in order_type:
                    order_map["tp"].append(order)
                elif order_type == "LIMIT":
                    order_map["tp"].append(order)  # Consider LIMIT orders as TP for checking
                else:
                    order_map["other"].append(order)
            
            return order_map
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error fetching orders: {str(e)}{TEXT_COLORS['RESET']}")
            return {"stop": [], "tp": [], "limit": [], "other": []}

    def open_order(self, side, atr_value, current_price, use_market=False):
        """
        Open a new position with proper risk management using market orders for reliability.
        Fixed issues with orderType parameter.

        Args:
            side: 'buy' or 'sell'
            atr_value: Current ATR value
            current_price: Current price
            use_market: Whether to use market orders instead of limit orders

        Returns:
            Boolean indicating success
        """
        # Set waiting for new candle flag if enabled
        if self.enable_candle_wait:
            self.waiting_for_new_candle = True
        else:
            # If candle wait is disabled, make sure the flag is off
            self.waiting_for_new_candle = False
        
        # Mark entry attempt on current signal
        self.last_attempted_entry = True

        # Update account balance
        self.update_account_balance()

        # Get account balance
        balance = self.account_balance
        if balance is None or balance <= 0:
            print(
                f"{TEXT_COLORS['RED']}Cannot get valid balance or balance is zero{TEXT_COLORS['RESET']}"
            )
            return False

        try:
            # Set margin type
            self.set_margin_type_with_retry()

            # Calculate position size and set leverage
            position_size, leverage = self.setup_position(balance)

            # Ensure position size is valid (non-zero)
            if position_size <= 0:
                print(
                    f"{TEXT_COLORS['RED']}Invalid position size: {position_size}{TEXT_COLORS['RESET']}"
                )
                return False

            # Get precision info for correctly formatting orders
            price_precision, tick_size = self.get_price_precision()
            # Get qty precision for position size
            qty_precision, min_size, step_size = self.get_qty_precision()

            # Calculate stop loss price with correct precision
            stop_distance = self.risk_atr_multiple * atr_value
            
            # Calculate prices based on entry type
            if side == "buy":
                stop_price = round(current_price - stop_distance, price_precision)
                take_profit_price = round(current_price + (self.tp_atr_multiple * atr_value), price_precision)
            else:  # side == "sell"
                stop_price = round(current_price + stop_distance, price_precision)
                take_profit_price = round(current_price - (self.tp_atr_multiple * atr_value), price_precision)

            # Ensure minimum size
            if position_size < min_size:
                position_size = min_size
                print(
                    f"{TEXT_COLORS['YELLOW']}Using minimum position size of {min_size} BTC{TEXT_COLORS['RESET']}"
                )

            # Round to appropriate precision
            position_size = round(position_size, qty_precision)

            # First, cancel any existing orders
            self.cancel_all_orders()

            # Print order details
            order_type_str = "MARKET" if use_market else "LIMIT"
            print(f"\n{TEXT_COLORS['CYAN']}ORDER DETAILS ({order_type_str} ENTRY):{TEXT_COLORS['RESET']}")
            print(f"Symbol: {self.symbol}")
            print(
                f"Side: {TEXT_COLORS['GREEN'] if side == 'buy' else TEXT_COLORS['RED']}{side.upper()}{TEXT_COLORS['RESET']}"
            )
            print(f"Current Price: ${current_price:.2f}")
            print(f"Stop Loss: ${stop_price:.2f}")
            print(f"Take Profit: ${take_profit_price:.2f}")
            print(f"Position Size: {position_size} BTC")
            print(f"Leverage: {leverage}x")

            # Execute the orders
            # FIXED: Use market orders for primary entry to avoid "Invalid orderType" errors
            if side == "buy":
                # Place MARKET order for reliability
                order_response = self.client.new_order(
                    symbol=self.symbol,
                    side="BUY",
                    type="MARKET",  # Changed from LIMIT to MARKET 
                    quantity=position_size,
                    recvWindow=6000,
                )
                print(
                    f"{TEXT_COLORS['GREEN']}BUY market order placed at ~${current_price:.2f}{TEXT_COLORS['RESET']}"
                )

                # Allow a small delay for the order to process
                sleep(0.5)

                # Place stop loss order as a STOP_MARKET for reliability
                stop_order = self.client.new_order(
                    symbol=self.symbol,
                    side="SELL",
                    type="STOP_MARKET",  # Changed to STOP_MARKET for reliability
                    quantity=position_size,
                    stopPrice=stop_price,
                    reduceOnly="true",
                    recvWindow=6000,
                )
                self.active_orders["stop"] = stop_order["orderId"]
                print(f"{TEXT_COLORS['YELLOW']}Stop loss set as STOP_MARKET at ${stop_price:.2f}{TEXT_COLORS['RESET']}")

                # Place take profit order for half position if size permits
                half_size = round(position_size / 2, qty_precision)
                if half_size >= min_size:
                    # Use TAKE_PROFIT_MARKET for reliability
                    take_profit_order = self.client.new_order(
                        symbol=self.symbol,
                        side="SELL",
                        type="TAKE_PROFIT_MARKET",  # Changed to TAKE_PROFIT_MARKET
                        quantity=half_size,
                        stopPrice=take_profit_price,
                        reduceOnly="true",
                        recvWindow=6000,
                    )
                    
                    # Store order ID
                    self.active_orders["tp"] = take_profit_order["orderId"]
                    
                    print(
                        f"{TEXT_COLORS['GREEN']}Take profit set at ${take_profit_price:.2f} for {half_size} BTC{TEXT_COLORS['RESET']}"
                    )
                else:
                    # If half size is too small, use full size for take profit
                    print(
                        f"{TEXT_COLORS['YELLOW']}Half position too small, using full size for take profit{TEXT_COLORS['RESET']}"
                    )
                    take_profit_order = self.client.new_order(
                        symbol=self.symbol,
                        side="SELL",
                        type="TAKE_PROFIT_MARKET",  # Changed to TAKE_PROFIT_MARKET
                        quantity=position_size,
                        stopPrice=take_profit_price,
                        reduceOnly="true",
                        recvWindow=6000,
                    )
                    
                    # Store order ID
                    self.active_orders["tp"] = take_profit_order["orderId"]
                    
                    print(
                        f"{TEXT_COLORS['GREEN']}Take profit set at ${take_profit_price:.2f} for {position_size} BTC{TEXT_COLORS['RESET']}"
                    )

            else:  # side == "sell"
                # Place MARKET order for reliability
                order_response = self.client.new_order(
                    symbol=self.symbol,
                    side="SELL",
                    type="MARKET",  # Changed from LIMIT to MARKET
                    quantity=position_size,
                    recvWindow=6000,
                )
                print(
                    f"{TEXT_COLORS['RED']}SELL market order placed at ~${current_price:.2f}{TEXT_COLORS['RESET']}"
                )

                # Allow a small delay for the order to process
                sleep(0.5)

                # Place stop loss order as a STOP_MARKET for reliability
                stop_order = self.client.new_order(
                    symbol=self.symbol,
                    side="BUY",
                    type="STOP_MARKET",  # Changed to STOP_MARKET for reliability
                    quantity=position_size,
                    stopPrice=stop_price,
                    reduceOnly="true",
                    recvWindow=6000,
                )
                self.active_orders["stop"] = stop_order["orderId"]
                print(f"{TEXT_COLORS['YELLOW']}Stop loss set as STOP_MARKET at ${stop_price:.2f}{TEXT_COLORS['RESET']}")

                # Place take profit order for half position if size permits
                half_size = round(position_size / 2, qty_precision)
                if half_size >= min_size:
                    # Use TAKE_PROFIT_MARKET for reliability
                    take_profit_order = self.client.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="TAKE_PROFIT_MARKET",  # Changed to TAKE_PROFIT_MARKET
                        quantity=half_size,
                        stopPrice=take_profit_price,
                        reduceOnly="true",
                        recvWindow=6000,
                    )
                    
                    # Store order ID
                    self.active_orders["tp"] = take_profit_order["orderId"]
                    
                    print(
                        f"{TEXT_COLORS['GREEN']}Take profit set at ${take_profit_price:.2f} for {half_size} BTC{TEXT_COLORS['RESET']}"
                    )
                else:
                    # If half size is too small, use full size for take profit
                    print(
                        f"{TEXT_COLORS['YELLOW']}Half position too small, using full size for take profit{TEXT_COLORS['RESET']}"
                    )
                    take_profit_order = self.client.new_order(
                        symbol=self.symbol,
                        side="BUY",
                        type="TAKE_PROFIT_MARKET",  # Changed to TAKE_PROFIT_MARKET
                        quantity=position_size,
                        stopPrice=take_profit_price,
                        reduceOnly="true",
                        recvWindow=6000,
                    )
                    
                    # Store order ID
                    self.active_orders["tp"] = take_profit_order["orderId"]
                    
                    print(
                        f"{TEXT_COLORS['GREEN']}Take profit set at ${take_profit_price:.2f} for {position_size} BTC{TEXT_COLORS['RESET']}"
                    )

            # Update position state
            self.position_entry_price = current_price
            self.position_entry_atr = atr_value
            self.current_stop_loss = stop_price
            self.current_take_profit = take_profit_price
            self.first_tranche_closed = False
            self.trailing_stop_activated = False

            # Update account balance after trade
            self.update_account_balance()

            # Log the trade
            self.log_trade(
                "entry",
                side,
                position_size,
                current_price,
                additional_info={
                    "leverage": leverage,
                    "stop_loss": stop_price,
                    "take_profit": take_profit_price,
                    "atr": atr_value,
                    "entry_type": "MARKET"
                },
            )

            return True

        except ClientError as error:
            print(
                f"{TEXT_COLORS['RED']}Error placing orders: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}"
            )
            # Print detailed error information for debugging
            for key, value in error.__dict__.items():
                print(f"    {key}: {value}")
                
            self.last_attempted_entry = False  # Reset entry flag since it failed
            self.waiting_for_new_candle = False  # Reset waiting flag
            return False

        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error in open_order: {str(e)}{TEXT_COLORS['RESET']}"
            )
            traceback.print_exc()
            self.last_attempted_entry = False  # Reset entry flag since it failed
            self.waiting_for_new_candle = False  # Reset waiting flag
            return False

    def close_position(self, position):
        """
        Close an existing position with proper logging.

        Args:
            position: Dictionary with position information

        Returns:
            Boolean indicating success
        """
        try:
            # Cancel all open orders first
            self.cancel_all_orders()

            # Calculate PnL for better reporting
            entry_price = self.position_entry_price
            pnl = 0
            pnl_pct = 0
            
            if entry_price > 0:
                if position["side"] == "long":
                    pnl = (position["mark_price"] - entry_price) * position["size"]
                    pnl_pct = ((position["mark_price"] / entry_price) - 1) * 100
                else:  # short
                    pnl = (entry_price - position["mark_price"]) * position["size"]
                    pnl_pct = ((entry_price / position["mark_price"]) - 1) * 100

            # Close position with market order
            side = "SELL" if position["side"] == "long" else "BUY"
            order_response = self.client.new_order(
                symbol=self.symbol,
                side=side,
                type="MARKET",
                quantity=position["size"],
                reduceOnly="true",
                recvWindow=6000,
            )
            print(
                f"{TEXT_COLORS['CYAN']}Closed {position['side'].upper()} position of {position['size']} BTC at market price{TEXT_COLORS['RESET']}"
            )

            # Update account balance after close
            new_balance = self.update_account_balance()

            # Log the trade with improved PnL reporting
            exit_side = "sell" if position["side"] == "long" else "buy"
            self.log_trade(
                "exit",
                exit_side,
                position["size"],
                position["mark_price"],
                pnl,
                pnl_pct,
            )

            # Reset position tracking state
            self.first_tranche_closed = False
            self.trailing_stop_activated = False
            self.last_attempted_entry = False  # Reset for potential reversal entry
            self.active_orders = {}  # Clear active orders tracking
            
            # Only reset waiting flag if candle wait is disabled
            if not self.enable_candle_wait:
                self.waiting_for_new_candle = False

            return True

        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error in close_position: {str(e)}{TEXT_COLORS['RESET']}"
            )
            traceback.print_exc()
            return False

    def handle_take_profit_fill(self, position, half_size):
        """
        Handle a take profit order fill by updating state and moving stop loss to breakeven.

        Args:
            position: Dictionary with position information
            half_size: Size of the closed portion

        Returns:
            Boolean indicating success
        """
        try:
            # Cancel all existing orders first
            self.cancel_all_orders()
            
            # Update state
            self.first_tranche_closed = True
            self.current_stop_loss = self.position_entry_price

            # Calculate PnL for the closed portion
            if position["side"] == "long":
                pnl = (position["mark_price"] - self.position_entry_price) * half_size
                pnl_pct = (
                    (position["mark_price"] / self.position_entry_price) - 1
                ) * 100
            else:  # short
                pnl = (self.position_entry_price - position["mark_price"]) * half_size
                pnl_pct = (
                    (self.position_entry_price / position["mark_price"]) - 1
                ) * 100

            # Update account balance
            self.update_account_balance()

            # Log the take profit
            side = "sell" if position["side"] == "long" else "buy"
            self.log_trade(
                "take_profit", side, half_size, position["mark_price"], pnl, pnl_pct
            )

            print(
                f"{TEXT_COLORS['GREEN']}Take profit hit! Moving stop loss to breakeven at ${self.position_entry_price:.2f}{TEXT_COLORS['RESET']}"
            )

            # Calculate remaining position size
            remaining_size = position["size"] - half_size
            
            # Get price precision for limit price calculation
            price_precision, tick_size = self.get_price_precision()
            
            # Place new stop loss at breakeven using STOP_MARKET
            side = "SELL" if position["side"] == "long" else "BUY"
            
            try:
                new_stop_order = self.client.new_order(
                    symbol=self.symbol,
                    side=side,
                    type="STOP_MARKET",
                    quantity=remaining_size,
                    stopPrice=self.position_entry_price,
                    reduceOnly="true",
                    recvWindow=6000,
                )

                # Store order ID
                self.active_orders["stop"] = new_stop_order["orderId"]
                
                print(
                    f"{TEXT_COLORS['GREEN']}NEW BREAKEVEN STOP PLACED at ${self.position_entry_price:.2f}{TEXT_COLORS['RESET']}"
                )
                return True

            except ClientError as error:
                print(
                    f"{TEXT_COLORS['RED']}Error setting breakeven stop: {error.error_code} - {error.error_message}{TEXT_COLORS['RESET']}"
                )
                print(
                    f"{TEXT_COLORS['RED']}*** WARNING: NO STOP LOSS ACTIVE - MANUAL INTERVENTION REQUIRED ***{TEXT_COLORS['RESET']}"
                )
                return False

        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error in handle_take_profit_fill: {str(e)}{TEXT_COLORS['RESET']}"
            )
            traceback.print_exc()
            return False

    def update_stop_loss(self, position):
        """
        Update the stop loss order for an existing position using STOP_MARKET orders for reliability.

        Args:
            position: Dictionary with position information

        Returns:
            Boolean indicating success
        """
        try:
            # Cancel existing stop orders only (preserve take profit)
            orders = self.get_open_orders_by_type()
            
            for stop_order in orders["stop"]:
                self.client.cancel_order(
                    symbol=self.symbol,
                    orderId=stop_order["orderId"],
                    recvWindow=6000,
                )
                print(
                    f"{TEXT_COLORS['YELLOW']}Canceled old stop order{TEXT_COLORS['RESET']}"
                )
                
            # Clear tracking for stop orders
            if "stop" in self.active_orders:
                del self.active_orders["stop"]

            # Get price precision for calculations
            price_precision, tick_size = self.get_price_precision()
            
            # Place new stop loss order - direct call, no try/except
            side = "SELL" if position["side"] == "long" else "BUY"
            
            # Prepare stop price with correct precision
            stop_price = round(self.current_stop_loss, price_precision)
            
            # Place STOP_MARKET order
            new_stop_order = self.client.new_order(
                symbol=self.symbol,
                side=side,
                type="STOP_MARKET",
                quantity=position["size"],
                stopPrice=stop_price,
                reduceOnly="true",
                recvWindow=6000,
            )
            
            # Store new order ID
            self.active_orders["stop"] = new_stop_order["orderId"]
            
            print(
                f"{TEXT_COLORS['GREEN']}New stop loss order placed as STOP_MARKET at ${stop_price}{TEXT_COLORS['RESET']}"
            )
            
            # Log the stop loss update as a trade event
            self.log_trade(
                "stop_update",
                "update",
                position["size"],
                position["mark_price"],
                additional_info={
                    "stop_price": stop_price,
                    "order_type": "STOP_MARKET"
                }
            )
                
            return True

        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error in update_stop_loss: {str(e)}{TEXT_COLORS['RESET']}"
            )
            traceback.print_exc()
            return False

    # =============== LOGGING AND REPORTING METHODS ===============

    def log_trade(
        self, trade_type, side, size, price, pnl=0.0, pnl_pct=0.0, additional_info=None
    ):
        """
        Log trade details to history and file with enhanced PnL tracking.

        Args:
            trade_type: Type of trade ('entry', 'exit', 'take_profit', etc.)
            side: Trade side ('buy', 'sell')
            size: Position size
            price: Execution price
            pnl: Realized profit/loss
            pnl_pct: Percentage profit/loss
            additional_info: Additional information to log
        """
        # Create trade record
        timestamp = self.get_server_time()
        
        # Get current balance for more accurate tracking
        current_balance = self.account_balance
        
        # Enhance PnL calculation for exit/partial trades
        if trade_type in ['exit', 'take_profit', 'stop_loss'] and pnl == 0.0:
            # If PnL is missing, try to calculate from entry data
            if hasattr(self, 'position_entry_price') and self.position_entry_price > 0:
                if side == 'sell' and self.position_entry_price > 0:  # Closing long position
                    pnl = (price - self.position_entry_price) * size
                    if self.position_entry_price > 0:
                        pnl_pct = ((price / self.position_entry_price) - 1) * 100
                elif side == 'buy' and self.position_entry_price > 0:  # Closing short position
                    pnl = (self.position_entry_price - price) * size
                    if self.position_entry_price > 0:
                        pnl_pct = ((self.position_entry_price / price) - 1) * 100
        
        trade = {
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "date": timestamp.strftime("%Y-%m-%d"),
            "time": timestamp.strftime("%H:%M:%S"),
            "type": trade_type,
            "symbol": self.symbol,
            "side": side,
            "size": float(size),  # Ensure size is float for consistent formatting
            "price": float(price),  # Ensure price is float for consistent formatting
            "pnl": float(pnl),     # Ensure PnL is properly captured
            "pnl_pct": float(pnl_pct),
            "balance": current_balance,
            "info": additional_info or {},
        }

        # Add to history
        self.trade_history.append(trade)

        # Determine color for log
        if trade_type == "entry":
            color = (
                TEXT_COLORS["GREEN"] if side in ["buy", "long"] else TEXT_COLORS["RED"]
            )
        elif trade_type in ["exit", "stop_loss"]:
            # Color based on PnL
            color = TEXT_COLORS["GREEN"] if pnl >= 0 else TEXT_COLORS["RED"]
        elif trade_type == "take_profit":
            color = TEXT_COLORS["GREEN"]
        else:
            color = TEXT_COLORS["RESET"]

        # Create log message with enhanced formatting
        time_str = f"[{trade['date']} {trade['time']}]"
        
        if trade_type == "entry":
            log_message = f"{time_str} | {color}{trade_type.upper()}{TEXT_COLORS['RESET']} | {color}{side.upper()}{TEXT_COLORS['RESET']} {size} {self.symbol} @ ${price:.2f}"
            
            # Add entry type info
            if additional_info and "entry_type" in additional_info:
                log_message += f" ({additional_info['entry_type']})"
                
            # Add stop loss and take profit info if available
            if additional_info:
                if "stop_loss" in additional_info:
                    log_message += f" | SL: ${additional_info['stop_loss']:.2f}"
                if "take_profit" in additional_info:
                    log_message += f" | TP: ${additional_info['take_profit']:.2f}"
                if "leverage" in additional_info:
                    log_message += f" | Leverage: {additional_info['leverage']}x"
                        
        elif trade_type in ["exit", "stop_loss", "take_profit"]:
            pnl_color = TEXT_COLORS["GREEN"] if pnl >= 0 else TEXT_COLORS["RED"]
            log_message = f"{time_str} | {color}{trade_type.upper()}{TEXT_COLORS['RESET']} | {side.upper()} {size} {self.symbol} @ ${price:.2f} | PnL: {pnl_color}${pnl:.2f} ({pnl_pct:.2f}%){TEXT_COLORS['RESET']}"
            # Add current balance
            log_message += f" | Balance: ${current_balance:.2f}"
        else:
            log_message = f"{time_str} | {trade_type.upper()} | {side.upper()} {size} {self.symbol} @ ${price:.2f}"

        # Print to console
        print(log_message)

        # Write to log file
        try:
            with open(self.trade_log_file, "a") as f:
                # Remove color codes for file
                clean_message = log_message
                for color in TEXT_COLORS.values():
                    clean_message = clean_message.replace(color, "")
                f.write(clean_message + "\n")
        except Exception as e:
            print(
                f"{TEXT_COLORS['RED']}Error writing to trade log: {str(e)}{TEXT_COLORS['RESET']}"
            )

        # Save trade history immediately for more reliable tracking
        try:
            self.save_trade_history()
        except Exception as e:
            print(f"{TEXT_COLORS['YELLOW']}Error saving trade history: {str(e)}{TEXT_COLORS['RESET']}")
            
    def save_trade_history(self):
        """Save trade history to a file."""
        try:
            # Ensure log directory exists
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
                
            # Convert trade history to a structured format
            history_records = []
            for trade in self.trade_history:
                record = {k: v for k, v in trade.items() if k != "info"}
                history_records.append(record)
                
            # Save as JSON
            history_file = os.path.join(self.log_dir, "trade_history.json")
            try:
                with open(history_file, "w") as f:
                    json.dump(history_records, f, indent=2)
            except Exception as e:
                print(f"{TEXT_COLORS['YELLOW']}Error saving trade history JSON: {str(e)}{TEXT_COLORS['RESET']}")
                
            # Also save as CSV for easier analysis
            try:
                import pandas as pd
                history_df = pd.DataFrame(history_records)
                history_df.to_csv(os.path.join(self.log_dir, "trade_history.csv"), index=False)
            except Exception as e:
                print(f"{TEXT_COLORS['YELLOW']}Error saving trade history CSV: {str(e)}{TEXT_COLORS['RESET']}")
                
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error in save_trade_history: {str(e)}{TEXT_COLORS['RESET']}")

    def generate_pnl_summary(self):
        """Generate a P&L summary from trade history."""
        if not self.trade_history:
            print(f"{TEXT_COLORS['YELLOW']}No trades to generate P&L summary{TEXT_COLORS['RESET']}")
            return
            
        try:
            # Extract all closed trades (exit, take_profit)
            closed_trades = [t for t in self.trade_history 
                            if t['type'] in ['exit', 'take_profit', 'stop_loss'] and t['pnl'] != 0]
                            
            if not closed_trades:
                print(f"{TEXT_COLORS['YELLOW']}No closed trades yet{TEXT_COLORS['RESET']}")
                return
                
            # Calculate statistics
            total_trades = len(closed_trades)
            profitable_trades = len([t for t in closed_trades if t['pnl'] > 0])
            losing_trades = len([t for t in closed_trades if t['pnl'] < 0])
            win_rate = (profitable_trades / total_trades) * 100 if total_trades > 0 else 0
            
            total_profit = sum([t['pnl'] for t in closed_trades if t['pnl'] > 0])
            total_loss = sum([t['pnl'] for t in closed_trades if t['pnl'] < 0])
            net_pnl = total_profit + total_loss
            
            # Calculate additional metrics
            profit_factor = abs(total_profit / total_loss) if total_loss != 0 else float('inf')
            avg_win = total_profit / profitable_trades if profitable_trades > 0 else 0
            avg_loss = total_loss / losing_trades if losing_trades > 0 else 0
            
            # Get current equity
            current_equity = self.account_balance
            
            # Calculate unrealized P&L if we have an open position
            position = self.get_open_positions()
            unrealized_pnl = position['pnl'] if position else 0
            
            # Format the summary
            summary = f"\n{'=' * 50}\n"
            summary += f"TRADING PERFORMANCE SUMMARY\n"
            summary += f"{'=' * 50}\n\n"
            
            summary += f"OVERALL STATISTICS:\n"
            summary += f"Current Equity: ${current_equity:.2f}\n"
            
            if position:
                pos_side = position['side'].upper()
                pos_size = position['size']
                pos_entry = position['entry_price']
                pos_current = position['mark_price']
                pos_pnl = position['pnl']
                pos_pnl_pct = ((pos_current / pos_entry - 1) * 100) if pos_side == 'long' else ((pos_entry / pos_current - 1) * 100)
                
                pnl_color = TEXT_COLORS['GREEN'] if pos_pnl > 0 else TEXT_COLORS['RED']
                summary += f"Current Position: {pos_side} {pos_size} BTC @ ${pos_entry:.2f}\n"
                summary += f"Current Price: ${pos_current:.2f}\n"
                summary += f"Unrealized P&L: {pnl_color}${pos_pnl:.2f} ({pos_pnl_pct:.2f}%){TEXT_COLORS['RESET']}\n"
            
            summary += f"Total Closed Trades: {total_trades}\n"
            summary += f"Win Rate: {win_rate:.2f}%\n"
            summary += f"Net Realized P&L: ${net_pnl:.2f}\n"
            summary += f"Total Profit: ${total_profit:.2f}\n"
            summary += f"Total Loss: ${total_loss:.2f}\n"
            summary += f"Profit Factor: {profit_factor:.2f}\n"
            summary += f"Average Win: ${avg_win:.2f}\n"
            summary += f"Average Loss: ${avg_loss:.2f}\n\n"
            
            # Recent trades
            summary += f"RECENT TRADES:\n"
            recent_trades = closed_trades[-5:] if len(closed_trades) > 5 else closed_trades
            for trade in reversed(recent_trades):
                trade_date = trade['date']
                trade_time = trade['time']
                trade_type = trade['type'].upper()
                trade_side = trade['side'].upper()
                trade_size = trade['size']
                trade_price = trade['price']
                trade_pnl = trade['pnl']
                trade_pnl_pct = trade['pnl_pct']
                
                pnl_color = TEXT_COLORS['GREEN'] if trade_pnl > 0 else TEXT_COLORS['RED']
                summary += f"[{trade_date} {trade_time}] {trade_type} {trade_side} {trade_size} BTC @ ${trade_price:.2f} | PnL: {pnl_color}${trade_pnl:.2f} ({trade_pnl_pct:.2f}%){TEXT_COLORS['RESET']}\n"
            
            summary += f"\n{'=' * 50}\n"
            
            # Print the summary
            print(summary)
            
            # Save to file
            try:
                # Remove colors for file
                clean_summary = summary
                for color in TEXT_COLORS.values():
                    clean_summary = clean_summary.replace(color, "")
                    
                with open(self.pnl_summary_file, "w") as f:
                    f.write(clean_summary)
            except Exception as e:
                print(f"{TEXT_COLORS['YELLOW']}Error saving P&L summary: {str(e)}{TEXT_COLORS['RESET']}")
                
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Error generating P&L summary: {str(e)}{TEXT_COLORS['RESET']}")
            traceback.print_exc()

    def run(self):
        """Run the trading bot in continuous mode with improved reliability and candle synchronization."""
        print(f"\n{TEXT_COLORS['CYAN']}Starting trading bot for {self.symbol} on {self.timeframe} timeframe...{TEXT_COLORS['RESET']}")
        
        try:
            # Initial time sync
            self.sync_server_time()
            
            # Initial account update
            self.update_account_balance()
            
            # Main trading loop
            while True:
                # Increment cycle counter
                self.cycle_count += 1
                
                print(f"\n{'=' * 50}")
                print(f"{TEXT_COLORS['CYAN']}TRADING CYCLE #{self.cycle_count} - {self.get_server_time().strftime('%Y-%m-%d %H:%M:%S')} (Server Time){TEXT_COLORS['RESET']}")
                print(f"{'=' * 50}")
                print(f"{TEXT_COLORS['CYAN']}Account Balance: ${self.account_balance:.2f} USDC{TEXT_COLORS['RESET']}")
                
                # Get current position info
                position = self.get_open_positions()
                
                # Check for take profit hit by detecting position size decrease
                if position and self.previous_position_size > 0:
                    current_size = position["size"]
                    # If position size decreased and we haven't already processed first tranche
                    if current_size < self.previous_position_size and not self.first_tranche_closed:
                        size_diff = self.previous_position_size - current_size
                        print(f"{TEXT_COLORS['GREEN']}Take profit detected! Position size decreased from {self.previous_position_size} to {current_size}{TEXT_COLORS['RESET']}")
                        # Handle the take profit fill
                        self.handle_take_profit_fill(position, size_diff)
                
                # Store current position size for next cycle comparison
                if position:
                    self.previous_position_size = position["size"]
                else:
                    self.previous_position_size = 0
                
                # Update equity info - report P&L occasionally
                if (datetime.now() - self.last_pnl_update).total_seconds() > 3600:  # Once per hour
                    self.generate_pnl_summary()
                    self.last_pnl_update = datetime.now()
                
                # Fetch price data, ensuring we're using closed candles
                data = self.klines_with_retry(exclude_current_candle=True)
                if data is None or data.empty:
                    print(f"{TEXT_COLORS['RED']}No valid candle data available - skipping cycle{TEXT_COLORS['RESET']}")
                    print(f"{TEXT_COLORS['YELLOW']}Waiting for next candle to close before continuing...{TEXT_COLORS['RESET']}")
                    self.countdown_with_animation()
                    continue
                    
                # Store the data for access in other methods
                self.latest_data = data
                
                if position:
                    # Calculate indicators for exit signals
                    current_price = data['Close'].iloc[-1]
                    
                    # Show current position info
                    side_color = TEXT_COLORS['GREEN'] if position['side'] == 'long' else TEXT_COLORS['RED']
                    pnl_color = TEXT_COLORS['GREEN'] if position['pnl'] > 0 else TEXT_COLORS['RED']
                    pnl_pct = ((position['mark_price'] / position['entry_price'] - 1) * 100) if position['side'] == 'long' else ((position['entry_price'] / position['mark_price'] - 1) * 100)
                    
                    print(f"\n{side_color}Current {position['side'].upper()} position:{TEXT_COLORS['RESET']}")
                    print(f"Size: {position['size']} BTC")
                    print(f"Entry: ${position['entry_price']:.2f}")
                    print(f"Current: ${position['mark_price']:.2f}")
                    print(f"Unrealized P&L: {pnl_color}${position['pnl']:.2f} ({pnl_pct:.2f}%){TEXT_COLORS['RESET']}")
                    
                    # Check for exit signals
                    exit_signal, reversal_info = self.check_exit_signals(position)
                    
                    if exit_signal == "exit":
                        # Close position
                        closed = self.close_position(position)
                        
                        # If successful and we have a reversal signal, enter the other side
                        if closed and reversal_info.get("has_reversal", False):
                            print(f"{TEXT_COLORS['CYAN']}Processing reversal signal...{TEXT_COLORS['RESET']}")
                            self.process_reversal(reversal_info)
                            
                    elif exit_signal == "take_profit":
                        # Handle the case where take profit was hit
                        half_size = position["size"] / 2
                        self.handle_take_profit_fill(position, half_size)
                        
                    elif exit_signal in ["trailing_activated", "update_stop"]:
                        # Update the stop loss
                        self.update_stop_loss(position)
                else:
                    # Check for entry signals
                    signal, atr, price = self.signal_strategy()
                    
                    if signal == "up":
                        # Enter long position
                        self.open_order("buy", atr, price, use_market=True)
                        
                    elif signal == "down":
                        # Enter short position
                        self.open_order("sell", atr, price, use_market=True)
                
                # Wait for next candle with animation
                self.countdown_with_animation()
                
        except KeyboardInterrupt:
            print(f"\n{TEXT_COLORS['YELLOW']}Trading bot stopped by user{TEXT_COLORS['RESET']}")
            # Generate final P&L summary
            self.generate_pnl_summary()
        except Exception as e:
            print(f"{TEXT_COLORS['RED']}Unexpected error: {str(e)}{TEXT_COLORS['RESET']}")
            traceback.print_exc()
            # Try to save state before exit
            self.save_trade_history()
            self.generate_pnl_summary()