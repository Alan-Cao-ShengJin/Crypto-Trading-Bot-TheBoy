from keys import api, secret  # Create a keys.py file with your API credentials
from binance.um_futures import UMFutures
from theboy import TheBoy, TEXT_COLORS


def main():
    # Print welcome message
    print(
        f"\n{TEXT_COLORS['CYAN']}=== The Boy v1.5 ==={TEXT_COLORS['RESET']}"
    )

    # Connect to Binance with API credentials
    try:
        client = UMFutures(key=api, secret=secret)
        # Test connection
        client.time()
        print(
            f"{TEXT_COLORS['GREEN']}Successfully connected to Binance Futures API{TEXT_COLORS['RESET']}"
        )
    except Exception as e:
        print(
            f"{TEXT_COLORS['RED']}Error connecting to Binance: {str(e)}{TEXT_COLORS['RESET']}"
        )
        return

    # Get current account balance
    try:
        balances = client.balance()
        usdc_balance = 0
        for balance in balances:
            if balance["asset"] == "USDC":
                usdc_balance = float(balance["balance"])
                break
        
        print(f"{TEXT_COLORS['GREEN']}Current USDC balance: ${usdc_balance:.2f}{TEXT_COLORS['RESET']}")
    except Exception as e:
        print(f"{TEXT_COLORS['YELLOW']}Could not fetch initial balance: {str(e)}{TEXT_COLORS['RESET']}")

    # Trading parameters (can be adjusted)
    symbol = "BTCUSDC"
    timeframe = (
        input(f"Enter timeframe (1m, 5m, 15m, 1h, 4h) [default: 1m]: ").strip() or "1m"
    )
    risk_percentage = 0.02  # 2% risk per trade

    # Ask for candle wait mode
    wait_response = input("Wait for new candles after signals? (y/n) [default: y]: ").strip().lower() or "y"
    enable_candle_wait = wait_response in ("y", "yes", "true")

    # Create trading bot instance
    bot = TheBoy(
        client=client,
        symbol=symbol,
        timeframe=timeframe,
        risk_percentage=risk_percentage,
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
    )
    
    # Set candle wait mode
    bot.enable_candle_wait = enable_candle_wait
    
    # Set debug mode
    debug_response = input("Enable detailed debug info? (y/n) [default: y]: ").strip().lower() or "y"
    bot.debug_signals = debug_response in ("y", "yes", "true")

    # Start the bot
    bot.run()


if __name__ == "__main__":
    main()