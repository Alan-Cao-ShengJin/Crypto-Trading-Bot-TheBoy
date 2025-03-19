#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperTrend + SSL Trading Bot for Binance Futures Testnet

This bot implements a trading strategy combining SuperTrend and SSL Channel 
indicators with choppiness filtering. It connects to Binance Futures Testnet
and executes trades based on the strategy signals.

Usage:
    python testnet.py --timeframe 1m --leverage 5 --interval 15 --risk_percentage 0.02
"""

import ccxt
import pandas as pd
import numpy as np
import time
import logging
import traceback
import requests
import json
from datetime import datetime, timedelta
import sys
import os
import math

# Configure logging with special handling for Windows
class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                # Fall back to ascii encoding for Windows command prompt
                safe_msg = msg.encode(stream.encoding, errors='replace').decode(stream.encoding)
                stream.write(safe_msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

# Define text colors for console output
TEXT_COLORS = {
    'CYAN': '\033[1;36m', 'GREEN': '\033[1;32m', 'YELLOW': '\033[1;33m',
    'RED': '\033[1;31m', 'BLUE': '\033[1;34m', 'MAGENTA': '\033[1;35m',
    'RESET': '\033[0m'
}

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[94m', 'INFO': '\033[92m', 'WARNING': '\033[93m',
        'ERROR': '\033[91m', 'CRITICAL': '\033[91m\033[1m', 'RESET': '\033[0m'
    }
    
    def format(self, record):
        if hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
            levelname = record.levelname
            record.levelname = f"{self.COLORS.get(levelname, '')}{levelname}{self.COLORS['RESET']}"
        return super().format(record)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log", encoding='utf-8'),
    ]
)
logger = logging.getLogger()

# Replace the default StreamHandler with our SafeStreamHandler
for handler in logger.handlers:
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
        logger.removeHandler(handler)
        
# Add colored console handler
console_handler = SafeStreamHandler(sys.stdout)
console_handler.setFormatter(ColoredFormatter('%(levelname)s | %(message)s'))
logger.addHandler(console_handler)

class TradingBot:
    def __init__(self, api_key, api_secret, symbol='BTC/USDT', timeframe='1m', leverage=1):
        """Initialize the trading bot with API credentials and settings."""
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol
        self.timeframe = timeframe
        self.leverage = leverage
        
        # Strategy parameters
        self.risk_percentage = 0.02  # Risk 2% of equity per trade
        self.risk_atr_period = 14
        self.risk_atr_multiple = 1.5  # Stop loss at 1.5x ATR
        self.tp_atr_multiple = 1.0  # Take profit at 1x ATR
        self.trailing_atr_multiple = 1.0  # Trailing stop at 1x ATR distance
        self.trailing_atr_trigger = 2.0  # Activate trailing stop at 2x ATR
        self.supertrend_atr_period = 10
        self.supertrend_factor = 2.0
        self.ssl_period = 10
        self.chop_length = 14
        self.chop_threshold = 50.0  # Above this value market is considered choppy
        
        # State variables
        self.data = None
        self.position = None
        self.entry_price = None
        self.entry_atr = None
        self.current_stop_loss = None
        self.current_take_profit = None
        self.first_tranche_closed = False
        self.trailing_stop_activated = False
        self.trailing_distance = None
        self.prev_supertrend = None
        self.prev_ssl = None
        self.st_reset_detected = False
        self.cycles_completed = 0
        
        # Initialize exchange
        self.initialize_exchange()
        
    def initialize_exchange(self):
        """Initialize the CCXT exchange object."""
        try:
            # Create exchange object with futures trading
            self.exchange = ccxt.binance({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True,
                    'recvWindow': 10000
                },
                'enableRateLimit': True
            })
            
            # Enable testnet mode
            self.exchange.set_sandbox_mode(True)
            
            # Test connection
            self.exchange.fetch_balance()
            logger.info(f"Connected to Binance Futures Testnet successfully")
            
            # Check markets to verify that the symbol is valid
            markets = self.exchange.load_markets()
            if self.symbol not in markets:
                symbol_variations = [
                    self.symbol,
                    self.symbol.replace('/', ''),
                    self.symbol + ':USDT',
                    self.symbol.replace('/', '') + '_PERP'
                ]
                
                valid_symbols = []
                for sym in symbol_variations:
                    if sym in markets:
                        valid_symbols.append(sym)
                
                if valid_symbols:
                    logger.info(f"Symbol {self.symbol} not found, but found similar symbols: {valid_symbols}")
                    # Update to use the first valid symbol
                    self.symbol = valid_symbols[0]
                    logger.info(f"Using symbol: {self.symbol}")
                else:
                    logger.warning(f"Symbol {self.symbol} not found in available markets. Will try to use it anyway.")
            
            # Set leverage
            self.set_leverage(self.leverage)
            
        except Exception as e:
            logger.error(f"Error connecting to exchange: {e}")
            traceback.print_exc()
            sys.exit(1)
    
    def set_leverage(self, leverage):
        """Set leverage for the trading pair, with retry mechanism."""
        try:
            # Convert symbol format from BTC/USDT to BTCUSDT for leverage setting
            market_symbol = self.symbol.replace('/', '')
            
            # Max attempts for setting leverage
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    # Use the standard CCXT method to set leverage
                    self.exchange.set_leverage(leverage, market_symbol)
                    logger.info(f"Leverage set to {leverage}x for {self.symbol}")
                    return True
                except Exception as lev_error:
                    # If this is due to leverage exceeding maximum allowed
                    if "leverage not available" in str(lev_error).lower() and attempt < max_attempts - 1:
                        # Try with a lower leverage
                        leverage = max(1, leverage - 5)
                        logger.warning(f"Requested leverage not available, trying {leverage}x instead...")
                    else:
                        # Re-raise if we've tried enough times or it's another error
                        raise
            
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage: {e}")
            traceback.print_exc()
            logger.info("Continuing without setting leverage - you may need to set it manually in the Binance interface")
            return False
    
    def fetch_ohlcv(self, limit=100):
        """Fetch OHLCV data from exchange using CCXT."""
        try:
            logger.info(f"Using CCXT to fetch {self.timeframe} data for {self.symbol}")
            
            # Fetch candles with proper parameters
            candles = self.exchange.fetch_ohlcv(
                symbol=self.symbol,
                timeframe=self.timeframe,
                limit=limit,
                params={'contractType': 'PERPETUAL'}  # Ensure we get perpetual futures data
            )
            
            # Convert to dataframe
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            # Log the latest candle information
            if not df.empty:
                latest = df.iloc[-1]
                formatted_volume = f"{latest['volume']:,.2f}"
                open_time = df.index[-1].strftime('%Y-%m-%d %H:%M:%S')
                logger.info(f"OHLC DATA | Time: {open_time} | O: {latest['open']:.2f} | H: {latest['high']:.2f} | L: {latest['low']:.2f} | C: {latest['close']:.2f} | V: {formatted_volume}")
            
            logger.info(f"Fetched {len(df)} candles from {df.index.min()} to {df.index.max()}")
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV data: {e}")
            traceback.print_exc()
            return None
    
    def fetch_klines_direct(self, limit=100):
        """
        Fetch kline/candlestick data directly from Binance Futures Testnet REST API.
        
        Args:
            limit (int): Number of candles to fetch (max 1500)
            
        Returns:
            pd.DataFrame: DataFrame with OHLCV data or None if error
        """
        try:
            # Convert symbol format from BTC/USDT to BTCUSDT for API
            market_symbol = self.symbol.replace('/', '')
            
            # For perpetual futures, make sure we're using the correct symbol
            if not market_symbol.endswith('PERP'):
                futures_symbol = market_symbol
                logger.info(f"Using futures symbol: {futures_symbol}")
            else:
                futures_symbol = market_symbol
            
            # Map timeframe to Binance's interval format
            timeframe_map = {
                '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
                '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '8h': '8h', 
                '12h': '12h', '1d': '1d', '3d': '3d', '1w': '1w', '1M': '1M'
            }
            interval = timeframe_map.get(self.timeframe, '1m')  # Default to 1m if not found
            
            # Log the actual interval being used
            logger.info(f"Fetching {interval} timeframe data for {futures_symbol}")
            
            # Construct API URL - Binance Futures Testnet
            base_url = "https://testnet.binancefuture.com"
            endpoint = "/fapi/v1/klines"
            url = f"{base_url}{endpoint}"
            
            # Set parameters
            params = {
                'symbol': futures_symbol,
                'interval': interval,
                'limit': limit
            }
            
            # Make the request
            response = requests.get(url, params=params)
            response.raise_for_status()  # Raise exception for HTTP errors
            
            # Parse response
            klines = response.json()
            
            # Log the raw kline data for the most recent candle
            if klines and len(klines) > 0:
                latest_candle = klines[-1]
                
                # Format nice log message with OHLC data for latest candle
                open_time = datetime.fromtimestamp(latest_candle[0]/1000).strftime('%Y-%m-%d %H:%M:%S')
                open_price = float(latest_candle[1])
                high_price = float(latest_candle[2])
                low_price = float(latest_candle[3])
                close_price = float(latest_candle[4])
                volume = float(latest_candle[5])
                
                # Fix volume formatting - use proper formatting without spaces
                formatted_volume = f"{volume:,.2f}"  
                
                logger.info(f"OHLC DATA | Time: {open_time} | O: {open_price:.2f} | H: {high_price:.2f} | L: {low_price:.2f} | C: {close_price:.2f} | V: {formatted_volume}")
            
            # Convert to DataFrame
            columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 
                       'close_time', 'quote_volume', 'trades', 
                       'taker_buy_base', 'taker_buy_quote', 'ignored']
            
            df = pd.DataFrame(klines, columns=columns)
            
            # Convert types
            numeric_columns = ['open', 'high', 'low', 'close', 'volume', 
                               'quote_volume', 'taker_buy_base', 'taker_buy_quote']
            df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric)
            
            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            logger.info(f"Fetched {len(df)} candles via REST API from {df.index.min()} to {df.index.max()}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching klines via REST API: {e}")
            traceback.print_exc()
            
            # Fall back to CCXT method
            logger.info("Falling back to CCXT fetch_ohlcv method")
            return self.fetch_ohlcv(limit=limit)
    
    def calculate_supertrend(self, df, period=10, multiplier=3.0):
        """Calculate SuperTrend indicator."""
        # Create a copy of the dataframe to avoid modifying the original
        df = df.copy()
        
        # Calculate ATR
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = abs(df['high'] - df['close'].shift())
        df['tr3'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        df['atr'] = df['tr'].rolling(period).mean()
        
        # Calculate basic bands
        df['hl2'] = (df['high'] + df['low']) / 2
        df['upperband'] = df['hl2'] + (multiplier * df['atr'])
        df['lowerband'] = df['hl2'] - (multiplier * df['atr'])
        
        # Initialize SuperTrend columns
        df['in_uptrend'] = True
        df['supertrend'] = np.nan
        
        # Calculate SuperTrend
        for i in range(1, len(df)):
            # Current and previous values
            curr_close = df['close'].iloc[i]
            curr_upper = df['upperband'].iloc[i]
            curr_lower = df['lowerband'].iloc[i]
            
            prev_close = df['close'].iloc[i-1]
            prev_upper = df['upperband'].iloc[i-1]
            prev_lower = df['lowerband'].iloc[i-1]
            prev_supertrend = df['supertrend'].iloc[i-1] if not np.isnan(df['supertrend'].iloc[i-1]) else df['lowerband'].iloc[i-1]
            prev_uptrend = df['in_uptrend'].iloc[i-1]
            
            # Determine trend
            if prev_uptrend:
                if curr_close <= prev_supertrend:
                    df.loc[df.index[i], 'in_uptrend'] = False
                else:
                    df.loc[df.index[i], 'in_uptrend'] = True
            else:
                if curr_close >= prev_supertrend:
                    df.loc[df.index[i], 'in_uptrend'] = True
                else:
                    df.loc[df.index[i], 'in_uptrend'] = False
                    
            # Calculate current SuperTrend value
            if df['in_uptrend'].iloc[i]:
                df.loc[df.index[i], 'supertrend'] = curr_lower
            else:
                df.loc[df.index[i], 'supertrend'] = curr_upper
        
        # Convert boolean trend to integer (1 for uptrend, -1 for downtrend)
        df['trend'] = np.where(df['in_uptrend'], 1, -1)
        
        # Generate buy/sell signals (when trend changes)
        df['buySignal'] = (df['trend'] == 1) & (df['trend'].shift(1) == -1)
        df['sellSignal'] = (df['trend'] == -1) & (df['trend'].shift(1) == 1)
        
        return df
    
    def calculate_ssl_channel(self, df, period=10):
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
    
    def calculate_chop(self, df, length=14):
        """Calculate Choppiness Index."""
        df = df.copy()
        
        # Calculate True Range
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift(1))
        low_close = abs(df['low'] - df['close'].shift(1))
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
        df['is_choppy'] = df['chop'] >= self.chop_threshold
        
        return df
    
    def calculate_indicators(self, df):
        """Calculate all indicators for trading decisions."""
        # Calculate SuperTrend
        df = self.calculate_supertrend(df, self.supertrend_atr_period, self.supertrend_factor)
        
        # Calculate SSL Channel
        df = self.calculate_ssl_channel(df, self.ssl_period)
        
        # Calculate Choppiness Index
        df = self.calculate_chop(df, self.chop_length)
        
        return df
    
    def check_entry_signals(self, df):
        """Check for entry signals based on strategy rules."""
        # Get current indicator values
        st_trend = df['trend'].iloc[-1]
        ssl = df['ssl'].iloc[-1]
        
        # Check for signals
        st_buy_signal = df['buySignal'].iloc[-1]
        st_sell_signal = df['sellSignal'].iloc[-1]
        ssl_long_signal = df['ssl_long_signal'].iloc[-1]
        ssl_short_signal = df['ssl_short_signal'].iloc[-1]
        
        # Check for SuperTrend reset
        self.st_reset_detected = st_buy_signal or st_sell_signal
        
        # Check for trend flips
        supertrend_up_flip = st_buy_signal or (self.prev_supertrend == -1 and st_trend == 1)
        supertrend_down_flip = st_sell_signal or (self.prev_supertrend == 1 and st_trend == -1)
        ssl_up_flip = ssl_long_signal or (self.prev_ssl == -1 and ssl == 1)
        ssl_down_flip = ssl_short_signal or (self.prev_ssl == 1 and ssl == -1)
        
        # Log trend flips with colors
        if supertrend_up_flip:
            logger.info(f"{TEXT_COLORS['GREEN']}SuperTrend flip to UPTREND detected{TEXT_COLORS['RESET']}")
        if supertrend_down_flip:
            logger.info(f"{TEXT_COLORS['RED']}SuperTrend flip to DOWNTREND detected{TEXT_COLORS['RESET']}")
        if ssl_up_flip:
            logger.info(f"{TEXT_COLORS['GREEN']}SSL flip to UPTREND detected{TEXT_COLORS['RESET']}")
        if ssl_down_flip:
            logger.info(f"{TEXT_COLORS['RED']}SSL flip to DOWNTREND detected{TEXT_COLORS['RESET']}")
        
        # Entry conditions
        long_entry = (ssl_up_flip and st_trend == 1) or (supertrend_up_flip and ssl == 1)
        short_entry = (ssl_down_flip and st_trend == -1) or (supertrend_down_flip and ssl == -1)
        
        # Choppiness filtering
        is_choppy = df['is_choppy'].iloc[-1]
        chop_value = df['chop'].iloc[-1]
        
        # Log chop status
        if is_choppy:
            logger.info(f"{TEXT_COLORS['YELLOW']}Market is choppy (Chop: {chop_value:.2f}){TEXT_COLORS['RESET']}")
        else:
            logger.info(f"{TEXT_COLORS['BLUE']}Market is trending (Chop: {chop_value:.2f}){TEXT_COLORS['RESET']}")
        
        # Reject signals if market is choppy and SuperTrend just reset
        if long_entry and self.st_reset_detected and is_choppy:
            logger.info(f"{TEXT_COLORS['RED']}LONG signal detected but market is choppy - REJECTED{TEXT_COLORS['RESET']}")
            long_entry = False
        
        if short_entry and self.st_reset_detected and is_choppy:
            logger.info(f"{TEXT_COLORS['RED']}SHORT signal detected but market is choppy - REJECTED{TEXT_COLORS['RESET']}")
            short_entry = False
        
        # Update state
        self.prev_supertrend = st_trend
        self.prev_ssl = ssl
        
        # Log signals
        if long_entry:
            logger.info(f"{TEXT_COLORS['GREEN']}LONG ENTRY SIGNAL: {'SSL flip + ST uptrend' if ssl_up_flip else 'ST flip + SSL uptrend'}{TEXT_COLORS['RESET']}")
        if short_entry:
            logger.info(f"{TEXT_COLORS['RED']}SHORT ENTRY SIGNAL: {'SSL flip + ST downtrend' if ssl_down_flip else 'ST flip + SSL downtrend'}{TEXT_COLORS['RESET']}")
        
        # Return signal data
        current_price = df['close'].iloc[-1]
        atr_value = df['atr'].iloc[-1]
        
        return {
            'long_entry': long_entry,
            'short_entry': short_entry,
            'is_choppy': is_choppy,
            'atr': atr_value,
            'current_price': current_price
        }
    
    def check_exit_signals(self, df):
        """Check for exit signals."""
        if not self.position:
            return False
        
        current_price = df['close'].iloc[-1]
        
        # Check SuperTrend flip for exit
        if self.position['side'] == 'long' and df['sellSignal'].iloc[-1]:
            logger.info(f"{TEXT_COLORS['RED']}EXIT SIGNAL: SuperTrend flipped to downtrend at ${current_price:.2f}{TEXT_COLORS['RESET']}")
            return 'exit'
        
        elif self.position['side'] == 'short' and df['buySignal'].iloc[-1]:
            logger.info(f"{TEXT_COLORS['GREEN']}EXIT SIGNAL: SuperTrend flipped to uptrend at ${current_price:.2f}{TEXT_COLORS['RESET']}")
            return 'exit'
        
        # Check take profit
        if self.position['side'] == 'long' and not self.first_tranche_closed and current_price >= self.current_take_profit:
            logger.info(f"{TEXT_COLORS['GREEN']}TAKE PROFIT: Long position at ${current_price:.2f}{TEXT_COLORS['RESET']}")
            return 'take_profit'
        
        elif self.position['side'] == 'short' and not self.first_tranche_closed and current_price <= self.current_take_profit:
            logger.info(f"{TEXT_COLORS['GREEN']}TAKE PROFIT: Short position at ${current_price:.2f}{TEXT_COLORS['RESET']}")
            return 'take_profit'
        
        # Check trailing stop activation (only if first take profit hit)
        if self.first_tranche_closed and not self.trailing_stop_activated:
            if (self.position['side'] == 'long' and 
                current_price >= (self.entry_price + (self.trailing_atr_trigger * self.entry_atr))):
                
                self.trailing_stop_activated = True
                self.trailing_distance = self.entry_atr * self.trailing_atr_multiple
                self.current_stop_loss = current_price - self.trailing_distance
                
                logger.info(f"{TEXT_COLORS['BLUE']}TRAILING STOP ACTIVATED: New stop at ${self.current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
                return 'trailing_stop_activated'
                
            elif (self.position['side'] == 'short' and 
                  current_price <= (self.entry_price - (self.trailing_atr_trigger * self.entry_atr))):
                
                self.trailing_stop_activated = True
                self.trailing_distance = self.entry_atr * self.trailing_atr_multiple
                self.current_stop_loss = current_price + self.trailing_distance
                
                logger.info(f"{TEXT_COLORS['BLUE']}TRAILING STOP ACTIVATED: New stop at ${self.current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
                return 'trailing_stop_activated'
        
        # Update trailing stop if activated
        if self.trailing_stop_activated:
            if self.position['side'] == 'long':
                new_stop = current_price - self.trailing_distance
                if new_stop > self.current_stop_loss:
                    old_stop = self.current_stop_loss
                    self.current_stop_loss = new_stop
                    logger.info(f"{TEXT_COLORS['BLUE']}TRAILING STOP UPDATED: ${old_stop:.2f} → ${new_stop:.2f}{TEXT_COLORS['RESET']}")
                    return 'trailing_stop_updated'
                    
            elif self.position['side'] == 'short':
                new_stop = current_price + self.trailing_distance
                if new_stop < self.current_stop_loss:
                    old_stop = self.current_stop_loss
                    self.current_stop_loss = new_stop
                    logger.info(f"{TEXT_COLORS['BLUE']}TRAILING STOP UPDATED: ${old_stop:.2f} → ${new_stop:.2f}{TEXT_COLORS['RESET']}")
                    return 'trailing_stop_updated'
        
        return False
    
    def calculate_position_size(self, entry_price, stop_price):
        """Calculate position size based on risk percentage with dynamic leverage adjustment."""
        try:
            # Get account balance
            balance = self.exchange.fetch_balance()
            equity = balance['USDT']['free']
            
            # Calculate risk amount
            risk_amount = equity * self.risk_percentage
            
            # Calculate price distance
            price_distance = abs(entry_price - stop_price)
            
            # Calculate raw position size based on risk (without leverage)
            raw_position_size = risk_amount / price_distance
            
            # Calculate notional value of the position
            position_notional = raw_position_size * entry_price
            
            # Calculate required margin with current leverage
            required_margin = position_notional / self.leverage
            
            # Check if we have enough equity
            available_margin = equity * 0.95  # Use 95% of equity max
            
            if required_margin > available_margin:
                # Find the minimum effective leverage needed (starting from current and incrementing)
                current_lev = self.leverage
                min_effective_leverage = current_lev
                
                # Increase leverage until we find one that works, or hit max
                while (position_notional / min_effective_leverage) > available_margin:
                    min_effective_leverage += 1
                
                # Ensure we get at least to the minimum needed leverage, rounded up
                min_needed_leverage = math.ceil(position_notional / available_margin)
                min_effective_leverage = max(min_effective_leverage, min_needed_leverage)
                
                # Cap at maximum allowed (Binance Futures allows up to 125x for some pairs)
                max_allowed_leverage = 125
                new_leverage = min(min_effective_leverage, max_allowed_leverage)
                
                logger.info(f"Adjusting leverage from {self.leverage}x to {new_leverage}x to fit position size")
                
                try:
                    # Update leverage setting on exchange
                    market_symbol = self.symbol.replace('/', '')
                    
                    # Try to set leverage, with fallback to lower values if necessary
                    success = self.set_leverage(new_leverage)
                    if success:
                        self.leverage = new_leverage
                        logger.info(f"Successfully adjusted leverage to {new_leverage}x")
                    else:
                        # If setting leverage failed completely, try a safer value
                        fallback_leverage = min(10, new_leverage)
                        if fallback_leverage != new_leverage:
                            self.set_leverage(fallback_leverage)
                            self.leverage = fallback_leverage
                            logger.info(f"Falling back to {fallback_leverage}x leverage")
                    
                    # Recalculate position size with new leverage
                    position_size = raw_position_size
                except Exception as e:
                    logger.error(f"Failed to adjust leverage: {e}")
                    # If leverage adjustment fails, scale down position to fit current leverage
                    scale_factor = available_margin / required_margin
                    position_size = raw_position_size * scale_factor
                    logger.warning(f"Reduced position size to {position_size} BTC to fit current leverage constraints")
            else:
                # Use calculated position size if margin is sufficient
                position_size = raw_position_size
            
            # Limit to 8 decimal places for BTC
            position_size = round(position_size, 8)
            
            # Ensure minimum position size (0.001 BTC for most exchanges)
            min_size = 0.001
            if position_size < min_size:
                logger.warning(f"Position size {position_size} below minimum. Using {min_size} instead.")
                position_size = min_size
            
            # Final margin check
            final_margin_required = (position_size * entry_price) / self.leverage
            logger.info(f"Position sizing: Equity=${equity:.2f}, Risk=${risk_amount:.2f}, " +
                        f"Price=${entry_price:.2f}, Stop=${stop_price:.2f}, " +
                        f"Distance=${price_distance:.2f}, Leverage={self.leverage}x, " +
                        f"Required Margin=${final_margin_required:.2f}, Size={position_size} BTC")
            
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            traceback.print_exc()
            return 0.001  # Fallback to minimum size
    
    def enter_long(self, current_price, atr):
        """Enter a long position with retry logic."""
        try:
            # Calculate stop loss
            stop_loss = current_price - (self.risk_atr_multiple * atr)
            
            # Calculate position size
            position_size = self.calculate_position_size(current_price, stop_loss)
            
            # Calculate take profit
            take_profit = current_price + (self.tp_atr_multiple * atr)
            
            logger.info(f"{TEXT_COLORS['GREEN']}LONG ENTRY: Price=${current_price:.2f}, Stop=${stop_loss:.2f}, TP=${take_profit:.2f}, Size={position_size}{TEXT_COLORS['RESET']}")
            
            # Try to place the market order with retry logic
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    # Place market order
                    order = self.exchange.create_market_buy_order(self.symbol, position_size)
                    
                    # If order succeeded, break out of retry loop
                    break
                except Exception as order_error:
                    if "Margin is insufficient" in str(order_error) and attempt < max_attempts - 1:
                        # Reduce position size by 20% and try again
                        position_size = position_size * 0.8
                        logger.warning(f"{TEXT_COLORS['YELLOW']}Insufficient margin. Reducing position size to {position_size} BTC and retrying...{TEXT_COLORS['RESET']}")
                        continue
                    else:
                        # Re-raise if it's not a margin issue or we've tried enough times
                        raise
            
            # Store position info
            self.position = {
                'side': 'long',
                'size': position_size,
                'entry_price': current_price
            }
            
            # Set state variables
            self.entry_price = current_price
            self.entry_atr = atr
            self.current_stop_loss = stop_loss
            self.current_take_profit = take_profit
            self.first_tranche_closed = False
            self.trailing_stop_activated = False
            
            # Place stop loss order - using stop_market which is more compatible
            try:
                stop_order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='stop_market',  # lowercase is more standard in ccxt
                    side='sell',
                    amount=position_size,
                    params={
                        'stopPrice': stop_loss,
                        'reduceOnly': True
                    }
                )
            except Exception as stop_error:
                logger.error(f"{TEXT_COLORS['RED']}Could not create stop loss order: {stop_error}{TEXT_COLORS['RESET']}")
                logger.info("Continuing without automatic stop loss - you may need to set it manually")
            
            logger.info(f"{TEXT_COLORS['GREEN']}Long position opened: {position_size} BTC at ${current_price:.2f}{TEXT_COLORS['RESET']}")
            logger.info(f"{TEXT_COLORS['YELLOW']}Stop loss set at ${stop_loss:.2f}{TEXT_COLORS['RESET']}")
            
            return True
            
        except Exception as e:
            logger.error(f"{TEXT_COLORS['RED']}Error entering long position: {e}{TEXT_COLORS['RESET']}")
            traceback.print_exc()
            return False
    
    def enter_short(self, current_price, atr):
        """Enter a short position with retry logic."""
        try:
            # Calculate stop loss
            stop_loss = current_price + (self.risk_atr_multiple * atr)
            
            # Calculate position size
            position_size = self.calculate_position_size(current_price, stop_loss)
            
            # Calculate take profit
            take_profit = current_price - (self.tp_atr_multiple * atr)
            
            logger.info(f"{TEXT_COLORS['RED']}SHORT ENTRY: Price=${current_price:.2f}, Stop=${stop_loss:.2f}, TP=${take_profit:.2f}, Size={position_size}{TEXT_COLORS['RESET']}")
            
            # Try to place the market order with retry logic
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    # Place market order
                    order = self.exchange.create_market_sell_order(self.symbol, position_size)
                    
                    # If order succeeded, break out of retry loop
                    break
                except Exception as order_error:
                    if "Margin is insufficient" in str(order_error) and attempt < max_attempts - 1:
                        # Reduce position size by 20% and try again
                        position_size = position_size * 0.8
                        logger.warning(f"{TEXT_COLORS['YELLOW']}Insufficient margin. Reducing position size to {position_size} BTC and retrying...{TEXT_COLORS['RESET']}")
                        continue
                    else:
                        # Re-raise if it's not a margin issue or we've tried enough times
                        raise
            
            # Store position info
            self.position = {
                'side': 'short',
                'size': position_size,
                'entry_price': current_price
            }
            
            # Set state variables
            self.entry_price = current_price
            self.entry_atr = atr
            self.current_stop_loss = stop_loss
            self.current_take_profit = take_profit
            self.first_tranche_closed = False
            self.trailing_stop_activated = False
            
            # Place stop loss order - using stop_market which is more compatible
            try:
                stop_order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='stop_market',  # lowercase is more standard in ccxt
                    side='buy',
                    amount=position_size,
                    params={
                        'stopPrice': stop_loss,
                        'reduceOnly': True
                    }
                )
            except Exception as stop_error:
                logger.error(f"{TEXT_COLORS['RED']}Could not create stop loss order: {stop_error}{TEXT_COLORS['RESET']}")
                logger.info("Continuing without automatic stop loss - you may need to set it manually")
            
            logger.info(f"{TEXT_COLORS['RED']}Short position opened: {position_size} BTC at ${current_price:.2f}{TEXT_COLORS['RESET']}")
            logger.info(f"{TEXT_COLORS['YELLOW']}Stop loss set at ${stop_loss:.2f}{TEXT_COLORS['RESET']}")
            
            return True
            
        except Exception as e:
            logger.error(f"{TEXT_COLORS['RED']}Error entering short position: {e}{TEXT_COLORS['RESET']}")
            traceback.print_exc()
            return False
    
    def close_position(self):
        """Close the current position."""
        if not self.position:
            return False
        
        try:
            # Cancel any open stop loss orders
            try:
                open_orders = self.exchange.fetch_open_orders(self.symbol)
                for order in open_orders:
                    order_type = order['type'].upper() if isinstance(order.get('type'), str) else ''
                    if order_type in ('STOP_MARKET', 'STOP', 'STOP_LOSS', 'STOP_LOSS_MARKET', 'STOP MARKET'):
                        try:
                            self.exchange.cancel_order(order['id'], self.symbol)
                            logger.info(f"Cancelled stop order {order['id']}")
                        except Exception as cancel_error:
                            logger.warning(f"Could not cancel order {order['id']}: {cancel_error}")
            except Exception as fetch_error:
                logger.warning(f"Could not fetch open orders: {fetch_error}")
            
            # Close position with market order
            if self.position['side'] == 'long':
                self.exchange.create_market_sell_order(
                    symbol=self.symbol,
                    amount=self.position['size']
                )
                logger.info(f"Closed long position: {self.position['size']} BTC")
            else:
                self.exchange.create_market_buy_order(
                    symbol=self.symbol,
                    amount=self.position['size']
                )
                logger.info(f"Closed short position: {self.position['size']} BTC")
            
            # Reset position state
            self.position = None
            self.first_tranche_closed = False
            self.trailing_stop_activated = False
            
            return True
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            traceback.print_exc()
            return False
    
    def take_partial_profit(self):
        """Close half position at take profit and move stop to breakeven."""
        if not self.position or self.first_tranche_closed:
            return False
        
        try:
            # Calculate half position size
            half_size = self.position['size'] / 2
            
            # Cancel existing stop loss orders
            try:
                open_orders = self.exchange.fetch_open_orders(self.symbol)
                for order in open_orders:
                    order_type = order['type'].upper() if isinstance(order.get('type'), str) else ''
                    if order_type in ('STOP_MARKET', 'STOP', 'STOP_LOSS', 'STOP_LOSS_MARKET', 'STOP MARKET'):
                        try:
                            self.exchange.cancel_order(order['id'], self.symbol)
                            logger.info(f"{TEXT_COLORS['YELLOW']}Cancelled stop order {order['id']}{TEXT_COLORS['RESET']}")
                        except Exception as cancel_error:
                            logger.warning(f"{TEXT_COLORS['YELLOW']}Could not cancel order {order['id']}: {cancel_error}{TEXT_COLORS['RESET']}")
            except Exception as fetch_error:
                logger.warning(f"{TEXT_COLORS['YELLOW']}Could not fetch open orders: {fetch_error}{TEXT_COLORS['RESET']}")
            
            # Close half position
            if self.position['side'] == 'long':
                self.exchange.create_market_sell_order(
                    symbol=self.symbol,
                    amount=half_size
                )
                logger.info(f"{TEXT_COLORS['GREEN']}Partial profit: Sold {half_size} BTC at take profit{TEXT_COLORS['RESET']}")
            else:
                self.exchange.create_market_buy_order(
                    symbol=self.symbol,
                    amount=half_size
                )
                logger.info(f"{TEXT_COLORS['GREEN']}Partial profit: Bought {half_size} BTC at take profit{TEXT_COLORS['RESET']}")
            
            # Update position size
            self.position['size'] = half_size
            
            # Set breakeven stop loss
            self.current_stop_loss = self.entry_price
            
            # Place new stop loss order at breakeven
            side = 'sell' if self.position['side'] == 'long' else 'buy'
            try:
                stop_order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='stop_market',  # lowercase is more standard in ccxt
                    side=side,
                    amount=half_size,
                    params={
                        'stopPrice': self.entry_price,
                        'reduceOnly': True
                    }
                )
            except Exception as stop_error:
                logger.error(f"{TEXT_COLORS['RED']}Could not create breakeven stop order: {stop_error}{TEXT_COLORS['RESET']}")
                logger.info("Continuing without automatic stop - you may need to set it manually")
            
            logger.info(f"{TEXT_COLORS['YELLOW']}Set breakeven stop at ${self.entry_price:.2f}{TEXT_COLORS['RESET']}")
            
            # Update state
            self.first_tranche_closed = True
            
            return True
            
        except Exception as e:
            logger.error(f"{TEXT_COLORS['RED']}Error taking partial profit: {e}{TEXT_COLORS['RESET']}")
            traceback.print_exc()
            return False
    
    def update_trailing_stop(self):
        """Update the trailing stop loss order."""
        if not self.position or not self.trailing_stop_activated:
            return False
        
        try:
            # Cancel existing stop loss orders
            try:
                open_orders = self.exchange.fetch_open_orders(self.symbol)
                for order in open_orders:
                    order_type = order['type'].upper() if isinstance(order.get('type'), str) else ''
                    if order_type in ('STOP_MARKET', 'STOP', 'STOP_LOSS', 'STOP_LOSS_MARKET', 'STOP MARKET'):
                        try:
                            self.exchange.cancel_order(order['id'], self.symbol)
                            logger.info(f"Cancelled stop order {order['id']}")
                        except Exception as cancel_error:
                            logger.warning(f"Could not cancel order {order['id']}: {cancel_error}")
            except Exception as fetch_error:
                logger.warning(f"Could not fetch open orders: {fetch_error}")
            
            # Place new stop loss order
            side = 'sell' if self.position['side'] == 'long' else 'buy'
            try:
                stop_order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='stop_market',  # lowercase is more standard in ccxt
                    side=side,
                    amount=self.position['size'],
                    params={
                        'stopPrice': self.current_stop_loss,
                        'reduceOnly': True
                    }
                )
            except Exception as stop_error:
                logger.error(f"Could not create trailing stop order: {stop_error}")
                logger.info("Continuing without automatic trailing stop - you may need to set it manually")
            
            logger.info(f"Updated trailing stop to {self.current_stop_loss}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating trailing stop: {e}")
            traceback.print_exc()
            return False
    
    def check_open_positions(self):
        """Check for any open positions on the exchange."""
        try:
            positions = self.exchange.fetch_positions([self.symbol])
            
            # Find open positions
            open_positions = [p for p in positions if abs(float(p['contracts'])) > 0]
            
            if not open_positions:
                self.position = None
                return False
            
            # We should have at most one position
            pos = open_positions[0]
            contracts = float(pos['contracts'])
            side = pos['side']
            entry_price = float(pos['entryPrice'])
            
            if contracts > 0:
                # Record position if we don't have it tracked already
                if not self.position:
                    logger.info(f"Found existing {side} position: {contracts} contracts at ${entry_price}")
                    self.position = {
                        'side': side,
                        'size': contracts,
                        'entry_price': entry_price
                    }
                    
                    # Set some reasonable defaults for risk management
                    # if we're picking up an existing position
                    if not self.entry_atr:
                        # Get current ATR
                        df = self.fetch_klines_direct(limit=30)
                        if df is not None:
                            df = self.calculate_supertrend(df, self.supertrend_atr_period, self.supertrend_factor)
                            self.entry_atr = df['atr'].iloc[-1]
                            self.entry_price = entry_price
                            
                            # Set reasonable stop loss and take profit
                            if side == 'long':
                                self.current_stop_loss = entry_price - (self.risk_atr_multiple * self.entry_atr)
                                self.current_take_profit = entry_price + (self.tp_atr_multiple * self.entry_atr)
                            else:
                                self.current_stop_loss = entry_price + (self.risk_atr_multiple * self.entry_atr)
                                self.current_take_profit = entry_price - (self.tp_atr_multiple * self.entry_atr)
                            
                            logger.info(f"Set risk parameters for existing position: Stop=${self.current_stop_loss}, TP=${self.current_take_profit}")
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking open positions: {e}")
            traceback.print_exc()
            return False
    
    def manage_risk(self, df):
        """Manage risk for open positions."""
        if not self.position:
            return False
        
        # Get current price
        current_price = df['close'].iloc[-1]
        
        # Check stop loss
        if self.position['side'] == 'long' and current_price <= self.current_stop_loss:
            logger.info(f"{TEXT_COLORS['RED']}STOP LOSS HIT: Long position at ${current_price:.2f}, stop at ${self.current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
            self.close_position()
            return 'stop_loss'
        
        elif self.position['side'] == 'short' and current_price >= self.current_stop_loss:
            logger.info(f"{TEXT_COLORS['RED']}STOP LOSS HIT: Short position at ${current_price:.2f}, stop at ${self.current_stop_loss:.2f}{TEXT_COLORS['RESET']}")
            self.close_position()
            return 'stop_loss'
        
        # Check for exit signals
        exit_signal = self.check_exit_signals(df)
        
        if exit_signal == 'exit':
            self.close_position()
            return 'signal_exit'
        
        elif exit_signal == 'take_profit':
            self.take_partial_profit()
            return 'take_profit'
        
        elif exit_signal in ['trailing_stop_activated', 'trailing_stop_updated']:
            self.update_trailing_stop()
            return exit_signal
        
        return False
    
    def display_balance(self):
        """Display current account balance and open positions."""
        try:
            balance = self.exchange.fetch_balance()
            positions = self.exchange.fetch_positions([self.symbol])
            
            # Print a separator line
            separator = "─" * 70
            logger.info(f"\n{separator}")
            logger.info(f"{TEXT_COLORS['CYAN']}ACCOUNT SUMMARY{TEXT_COLORS['RESET']}")
            logger.info(f"USDT Balance: ${balance['USDT']['free']:.2f}")
            
            # Display open positions
            open_positions = [p for p in positions if abs(float(p['contracts'])) > 0]
            if open_positions:
                for pos in open_positions:
                    side = pos['side']
                    size = float(pos['contracts'])
                    entry_price = float(pos['entryPrice'])
                    current_price = float(pos['markPrice'])
                    pnl = float(pos['unrealizedPnl'])
                    pnl_percent = (pnl / (entry_price * size)) * 100 if size > 0 else 0
                    
                    # Determine color based on PnL
                    if pnl > 0:
                        pnl_color = TEXT_COLORS['GREEN']
                    else:
                        pnl_color = TEXT_COLORS['RED']
                    
                    position_color = TEXT_COLORS['GREEN'] if side == 'long' else TEXT_COLORS['RED']
                    logger.info(f"Position: {position_color}{size} BTC {side.upper()}{TEXT_COLORS['RESET']} @ ${entry_price:.2f}")
                    logger.info(f"Current Price: ${current_price:.2f} | P&L: {pnl_color}${abs(pnl):.2f} ({pnl_percent:.2f}%){TEXT_COLORS['RESET']}")
                    
                    # Add risk management parameters if available
                    if self.current_stop_loss and self.current_take_profit:
                        sl_distance = abs(self.current_stop_loss - current_price)
                        sl_percent = (sl_distance / current_price) * 100
                        logger.info(f"Stop Loss: ${self.current_stop_loss:.2f} ({sl_percent:.2f}% away)")
                        
                        if not self.first_tranche_closed:
                            tp_distance = abs(self.current_take_profit - current_price)
                            tp_percent = (tp_distance / current_price) * 100
                            logger.info(f"Take Profit: ${self.current_take_profit:.2f} ({tp_percent:.2f}% away)")
                        
                        if self.trailing_stop_activated:
                            logger.info(f"Trailing Stop Active: {TEXT_COLORS['BLUE']}ON{TEXT_COLORS['RESET']} | Distance: ${self.trailing_distance:.2f}")
            else:
                logger.info(f"{TEXT_COLORS['YELLOW']}No open positions{TEXT_COLORS['RESET']}")
            
            logger.info(separator)
            return True
        except Exception as e:
            logger.error(f"Error displaying balance: {e}")
            return False
    
    def execute_trading_cycle(self):
        """Execute a single trading cycle."""
        try:
            separator = "═" * 60
            logger.info(f"\n{separator}")
            logger.info(f"{TEXT_COLORS['CYAN']}TRADING CYCLE #{self.cycles_completed+1}{TEXT_COLORS['RESET']}")
            logger.info(f"{separator}")
            
            # Display account balance
            self.display_balance()
            
            # Fetch OHLCV data using direct REST API
            df = self.fetch_klines_direct(limit=100)
            if df is None:
                logger.error(f"{TEXT_COLORS['RED']}Failed to fetch data, skipping cycle{TEXT_COLORS['RESET']}")
                return
            
            # Calculate indicators
            df = self.calculate_indicators(df)
            
            # Check for open positions from exchange
            self.check_open_positions()
            
            # Manage risk for existing positions
            if self.position:
                risk_action = self.manage_risk(df)
                if risk_action:
                    logger.info(f"{TEXT_COLORS['BLUE']}Risk management action taken: {risk_action}{TEXT_COLORS['RESET']}")
                    
                    # If we closed the position, check for reversal signals
                    if risk_action in ['stop_loss', 'signal_exit']:
                        signals = self.check_entry_signals(df)
                        
                        if self.position is None and signals['long_entry']:
                            self.enter_long(signals['current_price'], signals['atr'])
                        elif self.position is None and signals['short_entry']:
                            self.enter_short(signals['current_price'], signals['atr'])
                
                return
            
            # Check for entry signals if we don't have a position
            if not self.position:
                signals = self.check_entry_signals(df)
                
                if signals['long_entry']:
                    self.enter_long(signals['current_price'], signals['atr'])
                elif signals['short_entry']:
                    self.enter_short(signals['current_price'], signals['atr'])
            
            # Increment cycle counter
            self.cycles_completed += 1
            
        except Exception as e:
            logger.error(f"{TEXT_COLORS['RED']}Error in trading cycle: {e}{TEXT_COLORS['RESET']}")
            traceback.print_exc()
    
    def run(self, interval_seconds=60):
        """Run the trading bot in a loop with countdown timer."""
        logger.info(f"{TEXT_COLORS['CYAN']}Starting trading bot for {self.symbol} on {self.timeframe} timeframe{TEXT_COLORS['RESET']}")
        logger.info(f"{TEXT_COLORS['YELLOW']}Risk: {self.risk_percentage*100}% per trade, Leverage: {self.leverage}x{TEXT_COLORS['RESET']}")
        
        try:
            while True:
                self.execute_trading_cycle()
                
                # Create countdown timer
                logger.info(f"{TEXT_COLORS['BLUE']}Next cycle in {interval_seconds} seconds...{TEXT_COLORS['RESET']}")
                spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']  # Braille spinner characters
                start_time = time.time()
                end_time = start_time + interval_seconds
                
                try:
                    i = 0
                    while time.time() < end_time:
                        elapsed = time.time() - start_time
                        remaining = max(0, interval_seconds - elapsed)
                        percent = int((elapsed / interval_seconds) * 100)
                        
                        # Create progress bar
                        bar_length = 20
                        filled_length = int(bar_length * elapsed / interval_seconds)
                        bar = '█' * filled_length + '░' * (bar_length - filled_length)
                        
                        # Print countdown with spinner and progress bar
                        countdown = f"\r{spinner_chars[i % len(spinner_chars)]} Next cycle in {int(remaining)}s [{bar}] {percent}%"
                        sys.stdout.write(countdown)
                        sys.stdout.flush()
                        
                        # Sleep briefly and update spinner
                        time.sleep(0.1)
                        i += 1
                    
                    # Clear the line before starting next cycle
                    sys.stdout.write("\r" + " " * len(countdown) + "\r")
                    sys.stdout.flush()
                    
                except KeyboardInterrupt:
                    # Allow KeyboardInterrupt to be caught by the outer try/except
                    raise
                except Exception as countdown_error:
                    # If there's any error with the countdown, just sleep normally
                    logger.debug(f"Countdown display error: {countdown_error}")
                    remaining_time = max(0, end_time - time.time())
                    if remaining_time > 0:
                        time.sleep(remaining_time)
                
        except KeyboardInterrupt:
            logger.info(f"\n{TEXT_COLORS['YELLOW']}Trading bot stopped by user{TEXT_COLORS['RESET']}")
            # Clean up any open orders before exiting
            try:
                if self.position:
                    logger.info(f"{TEXT_COLORS['YELLOW']}Attempting to close open position before exit...{TEXT_COLORS['RESET']}")
                    self.close_position()
            except Exception as e:
                logger.error(f"{TEXT_COLORS['RED']}Error closing position on exit: {e}{TEXT_COLORS['RESET']}")
        except Exception as e:
            logger.error(f"{TEXT_COLORS['RED']}Unexpected error: {e}{TEXT_COLORS['RESET']}")
            traceback.print_exc()


# Main entry point
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="SuperTrend + SSL Channel Trading Bot")
    parser.add_argument("--api_key", default="d51b81641de05b697ab134fd991227adff144f312270322e7a347e692b1fd813", help="Binance API Key")
    parser.add_argument("--api_secret", default="a38f5e80a222539b3dad351f80d41b247f3515b6b9c5fa0aac56b38849326203", help="Binance API Secret")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--timeframe", default="1m", help="Timeframe for analysis")
    parser.add_argument("--leverage", type=int, default=1, help="Leverage to use")
    parser.add_argument("--interval", type=int, default=60, help="Update interval in seconds")
    parser.add_argument("--risk_percentage", type=float, default=0.02, help="Risk percentage per trade (0.01 = 1%)")
    parser.add_argument("--log_level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    
    args = parser.parse_args()
    
    # Set log level
    if args.log_level:
        numeric_level = getattr(logging, args.log_level.upper(), None)
        if isinstance(numeric_level, int):
            logger.setLevel(numeric_level)
            
    # Display startup info
    print(f"\n{'═' * 70}\n  {TEXT_COLORS['CYAN']}SUPERTREND + SSL CHANNEL TRADING BOT{TEXT_COLORS['RESET']}\n{'═' * 70}\n")
    logger.info(f"{TEXT_COLORS['CYAN']}Starting SuperTrend + SSL Trading Bot{TEXT_COLORS['RESET']}")
    logger.info(f"Trading Pair: {TEXT_COLORS['YELLOW']}{args.symbol}{TEXT_COLORS['RESET']}")
    logger.info(f"Timeframe: {TEXT_COLORS['YELLOW']}{args.timeframe}{TEXT_COLORS['RESET']}")
    logger.info(f"Leverage: {TEXT_COLORS['YELLOW']}{args.leverage}x{TEXT_COLORS['RESET']}")
    logger.info(f"Risk per trade: {TEXT_COLORS['YELLOW']}{args.risk_percentage * 100}%{TEXT_COLORS['RESET']}")
    logger.info(f"Update interval: {TEXT_COLORS['YELLOW']}{args.interval} seconds{TEXT_COLORS['RESET']}")
    
    try:
        # Create and run the trading bot
        bot = TradingBot(
            api_key=args.api_key,
            api_secret=args.api_secret,
            symbol=args.symbol,
            timeframe=args.timeframe,
            leverage=args.leverage
        )
        
        # Set risk percentage if provided
        if args.risk_percentage:
            bot.risk_percentage = args.risk_percentage
            
        bot.run(interval_seconds=args.interval)
    except KeyboardInterrupt:
        logger.info(f"{TEXT_COLORS['YELLOW']}Bot stopped by user{TEXT_COLORS['RESET']}")
    except Exception as e:
        logger.error(f"{TEXT_COLORS['RED']}Fatal error: {e}{TEXT_COLORS['RESET']}")
        traceback.print_exc()