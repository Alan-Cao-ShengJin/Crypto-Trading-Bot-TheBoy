import threading
import time
from binance.error import ClientError
import traceback

class OrderMonitor:
    """
    Real-time order monitor for trading bot.
    Monitors order status and triggers callbacks for trade management.
    """
    
    def __init__(self, client, rate_limiter, handlers, stop_loss_provider=None):
        """
        Initialize the order monitor.
        
        Args:
            client: Binance client instance
            rate_limiter: Rate limiter instance to manage API call frequency
            handlers: Dictionary of callback functions used by the monitor
            stop_loss_provider: Function that returns the current stop loss price
        """
        self.client = client
        self.rate_limiter = rate_limiter
        self.handlers = handlers
        self.stop_loss_provider = stop_loss_provider
        self.running = False
        self.thread = None
        self.symbol = None
        self.check_first_tranche = None
        self.monitoring_interval = 2  # seconds
        
    def start(self, symbol, check_first_tranche_func):
        """
        Start order monitoring in a background thread.
        
        Args:
            symbol: Trading pair symbol to monitor (e.g., 'BTCUSDC')
            check_first_tranche_func: Function to check if first tranche is closed
        """
        if self.running:
            print("OrderMonitor: Already running")
            return
            
        self.symbol = symbol
        self.check_first_tranche = check_first_tranche_func
        self.running = True
        
        # Start the monitoring thread
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        
        print(f"OrderMonitor: Started monitoring orders for {symbol}")
        
    def stop(self):
        """Stop the order monitoring thread."""
        if not self.running:
            print("OrderMonitor: Not running")
            return
            
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("OrderMonitor: Stopped")
        
    def _monitor_loop(self):
        """Main monitoring loop that runs in a background thread."""
        print("OrderMonitor: Monitoring thread started")
        
        while self.running:
            try:
                # Check if we have an open position
                position = self.handlers['get_open_positions'](self.symbol)
                
                if position:
                    # If we have a position, check for take profit hits
                    self._check_take_profit_hit(position)
                    
                    # Add other monitoring logic as needed
                
                # Sleep to avoid excessive API calls
                time.sleep(self.monitoring_interval)
                
            except Exception as e:
                print(f"Error in order monitoring thread: {str(e)}")
                traceback.print_exc()
                time.sleep(10)  # Longer sleep on error
    
    def _check_take_profit_hit(self, position):
        """
        Check if a take profit order has been filled.
        
        Args:
            position: Current position information
        """
        try:
            # Check if first tranche already closed
            if self.check_first_tranche():
                return
                
            # Get all orders for this symbol
            self.rate_limiter.check_rate_limit()
            orders = self.client.get_all_orders(symbol=self.symbol, limit=10)
            
            # Check for filled take profit orders
            take_profit_hit = False
            for order in orders:
                if (order['status'] == 'FILLED' and 
                    'TAKE_PROFIT' in order['type']):
                    take_profit_hit = True
                    break
                    
            # If no explicit take profit order found, check position size
            if not take_profit_hit:
                # This check helps detect if position size has been reduced by half
                # Get recent trades to check if a take profit may have executed
                self.rate_limiter.check_rate_limit()
                recent_trades = self.client.get_account_trades(symbol=self.symbol, limit=5)
                
                for trade in recent_trades:
                    # Look for trades that reduced the position (opposite side of position)
                    side_to_check = 'SELL' if position['side'] == 'long' else 'BUY'
                    if (trade['side'] == side_to_check and 
                        float(trade['time']) > time.time() * 1000 - 3600000):  # Last hour
                        take_profit_hit = True
                        break
            
            if take_profit_hit:
                print("OrderMonitor: Take profit hit detected")
                # Calculate half size (the amount that was closed)
                half_size = position['size']  # Already half of original after TP hit
                
                # Call handlers to process the take profit
                self.handlers['handle_take_profit_fill'](self.symbol, position, half_size)
                self.update_stop_loss(self.symbol, position)
                
        except Exception as e:
            print(f"Error in order monitoring - checking take profit: {str(e)}")
    
    def update_stop_loss(self, symbol, position):
        """
        Update the stop loss after a take profit hit with improved error handling.
        
        Args:
            symbol: Trading pair symbol
            position: Current position information
        """
        try:
            # Try to cancel existing stop orders first
            cancel_success = self.cancel_stop_orders(symbol)
            
            # Even if cancellation fails, still try to place new stop order
            # Let the main handler update the stop loss
            try:
                self.handlers['update_stop_loss'](symbol, position)
                print(f"OrderMonitor: Successfully updated stop loss")
                return True
            except Exception as handler_error:
                # If the handler fails, try to place the stop order directly
                print(f"OrderMonitor: Handler error: {str(handler_error)}")
                print(f"OrderMonitor: Attempting to place stop order directly...")
                
                try:
                    # Get current stop loss from the provider
                    current_stop_loss = None
                    if self.stop_loss_provider:
                        current_stop_loss = self.stop_loss_provider()
                    
                    if current_stop_loss is None:
                        print("OrderMonitor: No stop loss value available")
                        return False
                    
                    # Place stop order directly
                    side = 'SELL' if position['side'] == 'long' else 'BUY'
                    self.rate_limiter.check_rate_limit()
                    new_order = self.client.new_order(
                        symbol=symbol,
                        side=side,
                        type='STOP_MARKET',
                        quantity=position['size'],
                        stopPrice=current_stop_loss,
                        reduceOnly='true'
                    )
                    print(f"OrderMonitor: Directly placed stop loss at ${current_stop_loss}")
                    return True
                except Exception as direct_error:
                    print(f"OrderMonitor: Direct stop order placement failed: {str(direct_error)}")
                    return False
            
        except Exception as e:
            print(f"OrderMonitor: Error in update_stop_loss: {str(e)}")
            traceback.print_exc()
            return False
    
    def cancel_stop_orders(self, symbol):
        """
        Cancel all stop orders for a symbol with improved error handling.
        
        Args:
            symbol: Trading pair symbol
        """
        try:
            # Use cancel_open_orders instead of cancel_all_open_orders
            self.rate_limiter.check_rate_limit()
            try:
                result = self.client.cancel_open_orders(symbol=symbol)
                print(f"OrderMonitor: Canceled all open orders for {symbol}")
                return True
            except Exception as e:
                print(f"OrderMonitor: Could not cancel all orders: {str(e)}, trying alternative method...")
                
            # Fallback: Try to get open orders and cancel them individually
            self.rate_limiter.check_rate_limit()
            try:
                orders = self.client.get_open_orders(symbol=symbol)
                
                # Cancel stop orders
                cancelled = False
                for order in orders:
                    if 'STOP' in order['type']:
                        self.cancel_order(symbol, order)
                        cancelled = True
                        
                if cancelled:
                    print(f"OrderMonitor: Canceled stop orders for {symbol} individually")
                else:
                    print(f"OrderMonitor: No stop orders found to cancel for {symbol}")
                    
                return True
            except Exception as e:
                print(f"OrderMonitor: Error getting and canceling open orders: {str(e)}")
                return False
                    
        except Exception as error:
            print(f"OrderMonitor: Error in cancel_stop_orders: {str(error)}")
            return False
    
    def cancel_order(self, symbol, order):
        """
        Safely cancel an order with proper error handling.
        
        Args:
            symbol: Trading pair symbol
            order: Order data to cancel
        
        Returns:
            bool: True if cancellation was successful or not needed, False otherwise
        """
        if 'orderId' in order and order['orderId']:
            try:
                self.rate_limiter.check_rate_limit()
                self.client.cancel_order(symbol=symbol, orderId=order['orderId'])
                print(f"OrderMonitor: Canceled order (ID: {order['orderId']})")
                return True
            except ClientError as cancel_error:
                if "Unknown order" in str(cancel_error) or "Order does not exist" in str(cancel_error):
                    print(f"OrderMonitor: Order already cancelled or filled: {order['orderId']}")
                    return True  # Consider this a success since the order is no longer active
                else:
                    print(f"OrderMonitor: Error cancelling order: {cancel_error.error_code} - {cancel_error.error_message}")
                    return False
        else:
            print(f"OrderMonitor: Found order without valid orderId, skipping cancel")
            return False