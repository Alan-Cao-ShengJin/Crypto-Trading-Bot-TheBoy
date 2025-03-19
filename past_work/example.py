import ccxt

API_KEY = 'd51b81641de05b697ab134fd991227adff144f312270322e7a347e692b1fd813'
API_SECRET = 'a38f5e80a222539b3dad351f80d41b247f3515b6b9c5fa0aac56b38849326203'

# Initialize the exchange
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'options': {'defaultType': 'future'}
})

# Testing on Binance testnet
exchange.set_sandbox_mode(True)

# Print account balance
balance = exchange.fetch_balance()
print(f"Available USDT: {balance['USDT']['free']}")

# Set trading parameters
symbol = 'BTC/USDT'
quantity = 0.01
leverage = 5

try:
    # Step 1: Set leverage for the symbol BEFORE placing the order
    exchange.set_leverage(leverage, symbol)
    print(f"Leverage set to {leverage}x for {symbol}")
    
    # Step 2: Set margin type to isolated (recommended for most traders)
    exchange.set_margin_mode('isolated', symbol)
    print(f"Margin mode set to isolated for {symbol}")
    
    # Step 3: Place the order
    order = exchange.create_order(
        symbol=symbol,
        type='limit',
        side='buy',
        price='84300',
        amount=quantity
    )
    
    # Print order details
    print(f"Order placed successfully:")
    print(f"Order ID: {order['id']}")
    print(f"Symbol: {order['symbol']}")
    print(f"Type: {order['type']}")
    print(f"Side: {order['side']}")
    print(f"Price: {order['price']}")
    print(f"Amount: {order['amount']}")
    
except Exception as e:
    print(f"An error occurred: {str(e)}")


