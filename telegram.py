"""
Simple Telegram notification module for trading bot.
This version uses a direct approach without threading for easier integration.
"""

import requests
import time
from datetime import datetime
from keys import TOKEN, CHAT_ID

# Use credentials from telegram.py
TOKEN = TOKEN
CHAT_ID = CHAT_ID

def send_message(message, parse_mode=None):
    """Send a message to Telegram."""
    params = {
        'chat_id': CHAT_ID,
        'text': message
    }
    
    if parse_mode:
        params['parse_mode'] = parse_mode
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        response = requests.get(url, params=params)
        if response.status_code != 200:
            print(f"Failed to send Telegram message: {response.status_code} - {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {str(e)}")
        return False

def notify_trade_entry(symbol, side, price, size, stop_loss, take_profit, risk_amount=None, leverage=None):
    """Send trade entry notification."""
    side_emoji = "🟢 LONG" if side.lower() == 'buy' else "🔴 SHORT"
    
    message = f"*{side_emoji} ENTRY - {symbol}*\n\n"
    message += f"📈 *Entry Price:* `${price:.2f}`\n"
    message += f"📊 *Position Size:* `{size} {symbol.replace('USDT', '')}`\n"
    message += f"🛑 *Stop Loss:* `${stop_loss:.2f}`\n"
    message += f"🎯 *Take Profit:* `${take_profit:.2f}`\n"
    
    if risk_amount:
        message += f"💰 *Risk Amount:* `${risk_amount:.2f}`\n"
    
    if leverage:
        message += f"⚡ *Leverage:* `{leverage}x`\n"
    
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_take_profit_hit(symbol, side, price, size, profit, profit_pct, remaining_size, new_stop_loss):
    """Send take profit notification."""
    message = f"🎯 *TAKE PROFIT HIT - {symbol}*\n\n"
    message += f"💰 *Profit:* `${profit:.2f} ({profit_pct:.2f}%)`\n"
    message += f"📈 *Price:* `${price:.2f}`\n"
    message += f"📊 *Closed Size:* `{size} {symbol.replace('USDT', '')}`\n"
    message += f"📊 *Remaining Size:* `{remaining_size} {symbol.replace('USDT', '')}`\n"
    message += f"🛑 *New Stop Loss:* `${new_stop_loss:.2f}`\n"
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_stop_loss_hit(symbol, side, price, size, loss, loss_pct):
    """Send stop loss notification."""
    message = f"🛑 *STOP LOSS HIT - {symbol}*\n\n"
    message += f"📉 *Loss:* `${abs(loss):.2f} ({loss_pct:.2f}%)`\n"
    message += f"📈 *Price:* `${price:.2f}`\n"
    message += f"📊 *Size:* `{size} {symbol.replace('USDT', '')}`\n"
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_trade_exit(symbol, side, price, size, pnl, pnl_pct, exit_reason="Signal"):
    """Send trade exit notification."""
    # Emoji based on P&L
    emoji = "🟢" if pnl >= 0 else "🔴"
    pnl_text = f"+${pnl:.2f} (+{pnl_pct:.2f}%)" if pnl >= 0 else f"-${abs(pnl):.2f} ({pnl_pct:.2f}%)"
    
    message = f"{emoji} *POSITION CLOSED - {symbol}*\n\n"
    message += f"📊 *Position:* `{side.upper()} {size} {symbol.replace('USDT', '')}`\n"
    message += f"📈 *Exit Price:* `${price:.2f}`\n"
    message += f"💰 *P&L:* `{pnl_text}`\n"
    message += f"❓ *Reason:* `{exit_reason}`\n"
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_trailing_stop_activated(symbol, side, current_price, stop_price, distance_pct):
    """Send trailing stop activation notification."""
    message = f"🔄 *TRAILING STOP ACTIVATED - {symbol}*\n\n"
    message += f"📊 *Position:* `{side.upper()}`\n"
    message += f"📈 *Current Price:* `${current_price:.2f}`\n"
    message += f"🛑 *Trail Stop:* `${stop_price:.2f} ({distance_pct:.2f}% away)`\n"
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_trailing_stop_updated(symbol, side, current_price, new_stop_price, old_stop_price):
    """Send trailing stop update notification."""
    # Calculate distance and change
    distance = abs(current_price - new_stop_price)
    distance_pct = (distance / current_price) * 100
    change = abs(new_stop_price - old_stop_price)
    
    message = f"🔄 *TRAILING STOP UPDATED - {symbol}*\n\n"
    message += f"📈 *Current Price:* `${current_price:.2f}`\n"
    message += f"🛑 *New Stop:* `${new_stop_price:.2f} ({distance_pct:.2f}% away)`\n"
    message += f"📏 *Change:* `${change:.2f}`\n"
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_pnl_update(symbol, side, size, entry_price, current_price, unrealized_pnl, pnl_pct, balance=None, stop_loss=None, take_profit=None, trailing_active=False):
    """Send position update notification."""
    message = f"📊 *P&L UPDATE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
    
    if balance:
        message += f"💰 *Balance:* `${balance:.2f}`\n"
    
    # Use emoji based on position side
    side_emoji = "🟢" if side.lower() == 'long' else "🔴"
    
    message += f"{side_emoji} *Position:* `{side.upper()} {size} {symbol.replace('USDT', '')}`\n"
    message += f"📈 *Entry:* `${entry_price:.2f}`\n"
    message += f"📉 *Current:* `${current_price:.2f}`\n"
    
    # Calculate price change
    if side.lower() == 'long':
        change_pct = ((current_price - entry_price) / entry_price) * 100
    else:
        change_pct = ((entry_price - current_price) / entry_price) * 100
    
    change_emoji = "🟢" if change_pct > 0 else "🔴"
    message += f"📊 *Change:* `{change_emoji} {change_pct:.2f}%`\n"
    
    pnl_emoji = "🟢" if unrealized_pnl > 0 else "🔴"
    pnl_text = f"+${unrealized_pnl:.2f} (+{pnl_pct:.2f}%)" if unrealized_pnl > 0 else f"-${abs(unrealized_pnl):.2f} ({pnl_pct:.2f}%)"
    message += f"💰 *Unrealized P&L:* `{pnl_emoji} {pnl_text}`\n"
    
    if stop_loss:
        stop_distance = abs(current_price - stop_loss)
        stop_pct = (stop_distance / current_price) * 100
        message += f"🛑 *Stop Loss:* `${stop_loss:.2f} ({stop_pct:.2f}% away)`\n"
    
    if take_profit:
        tp_distance = abs(current_price - take_profit)
        tp_pct = (tp_distance / current_price) * 100
        message += f"🎯 *Take Profit:* `${take_profit:.2f} ({tp_pct:.2f}% away)`\n"
    
    if trailing_active:
        message += f"🔄 *Trailing Stop:* `ACTIVE`\n"
    
    return send_message(message, parse_mode='Markdown')

def notify_error(error_message, error_traceback=None):
    """Send error notification."""
    message = f"⚠️ *ERROR ALERT*\n\n"
    message += f"`{error_message}`\n"
    
    if error_traceback:
        # Format traceback with code blocks, but truncate if too long
        tb_text = error_traceback[:2000] + "..." if len(error_traceback) > 2000 else error_traceback
        message += f"\n```\n{tb_text}\n```"
    
    return send_message(message, parse_mode='Markdown')

def notify_signal(symbol, signal_type, reason, current_price):
    """Send signal notification."""
    if signal_type == 'up':
        emoji = "🟢"
        signal_text = "LONG"
    elif signal_type == 'down':
        emoji = "🔴" 
        signal_text = "SHORT"
    else:
        emoji = "⚪"
        signal_text = "NO SIGNAL"
    
    message = f"{emoji} *{signal_text} SIGNAL - {symbol}*\n\n"
    message += f"📈 *Price:* `${current_price:.2f}`\n"
    message += f"❓ *Reason:* `{reason}`\n"
    message += f"⏰ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
    
    return send_message(message, parse_mode='Markdown')

# Test function
def test_notifications():
    """Send test notifications to verify everything is working."""
    print("Sending test notifications to Telegram...")
    
    # Test basic message
    send_message("🤖 Test notification from trading bot")
    
    # Test trade entry
    notify_trade_entry("BTCUSDT", "buy", 40000, 0.05, 39000, 42000, 50, 5)
    
    # Test take profit
    notify_take_profit_hit("BTCUSDT", "long", 42000, 0.025, 50, 5, 0.025, 40000)
    
    # Test position update
    notify_pnl_update("BTCUSDT", "long", 0.05, 40000, 41000, 50, 2.5, 1000, 39000, 42000, False)
    
    print("Test notifications sent. Check your Telegram!")

# Run test if executed directly
if __name__ == "__main__":
    print("Telegram Notification Module")
    print("---------------------------")
    print(f"Using bot with token: {TOKEN[:5]}...{TOKEN[-5:]}")
    print(f"Sending to chat ID: {CHAT_ID}")
    
    # Test the connection
    if send_message("🤖 Telegram notification system online!"):
        print("✅ Connection successful!")
        
        # Ask if user wants to send test notifications
        if input("\nSend test notifications? (y/n): ").lower() == 'y':
            test_notifications()
    else:
        print("❌ Connection failed. Please check your token and chat ID.")