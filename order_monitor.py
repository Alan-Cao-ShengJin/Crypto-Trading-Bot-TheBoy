"""
Real-time order monitoring module for trading bot.
This module polls for order fills and updates stop losses immediately when take profit targets are hit.
"""

import threading
import time
from binance.error import ClientError

class OrderMonitor:
    def __init__(self, client, rate_limiter, handlers):
        """
        Initialize the order monitor.
        
        Args:
            client: Binance client instance
            rate_limiter: Rate limiter instance
            handlers: Dictionary of handler functions
                - get_open_positions: Function to get current positions
                - handle_take_profit_fill: Function to handle take profit fills
                - update_stop_loss: Function to update stop loss
        """
        self.client = client
        self.rate_limiter = rate_limiter
        self.handlers = handlers
        
        # State variables
        self.stop_monitoring = False
        self.monitor_thread = None
        self.text_colors = {
            'GREEN': '\033[1;92m',
            'RED': '\033[1;91m',
            'YELLOW': '\033[1;93m',
            'RESET': '\033[0m'
        }
        
    def _monitor_orders(self, symbol, first_tranche_closed):
        """
        Worker function that monitors order fills.
        
        Args:
            symbol: Trading symbol to monitor
            first_tranche_closed: Function reference to check if first tranche is closed
        """
        print(f"{self.text_colors['GREEN']}Order monitoring thread started{self.text_colors['RESET']}")
        
        while not self.stop_monitoring:
            try:
                # Check if we have an active position
                position = self.handlers['get_open_positions'](symbol)
                if not position:
                    time.sleep(5)
                    continue
                    
                # Skip if first tranche already closed
                if first_tranche_closed():
                    time.sleep(5)
                    continue
                    
                # Check for filled take profit orders
                try:
                    self.rate_limiter.check_rate_limit()
                    closed_orders = self.client.get_all_orders(symbol=symbol, limit=10)
                    
                    for order in closed_orders:
                        if (order['status'] == 'FILLED' and 
                            'TAKE_PROFIT' in order['type'] and 
                            not first_tranche_closed()):
                            
                            print(f"{self.text_colors['GREEN']}Take profit order filled! Updating stop loss immediately.{self.text_colors['RESET']}")
                            
                            half_size = position['size']
                            self.handlers['handle_take_profit_fill'](symbol, position, half_size)
                            self.handlers['update_stop_loss'](symbol, position)
                            break
                    
                except ClientError as error:
                    print(f"{self.text_colors['RED']}API error checking orders: {error.error_code} - {error.error_message}{self.text_colors['RESET']}")
                    if error.status_code == 429:  # Rate limit error
                        time.sleep(60)
                    else:
                        time.sleep(10)
                
                # Sleep for a short period before checking again
                time.sleep(5)
                
            except Exception as e:
                print(f"{self.text_colors['RED']}Error in order monitoring thread: {str(e)}{self.text_colors['RESET']}")
                time.sleep(10)
        
        print(f"{self.text_colors['YELLOW']}Order monitoring thread stopped{self.text_colors['RESET']}")

    def start(self, symbol, first_tranche_closed_func):
        """
        Start the order monitoring thread.
        
        Args:
            symbol: Symbol to monitor
            first_tranche_closed_func: Function reference to check if first tranche is closed
            
        Returns:
            bool: True if started successfully, False otherwise
        """
        if self.monitor_thread and self.monitor_thread.is_alive():
            print(f"{self.text_colors['YELLOW']}Order monitoring already running{self.text_colors['RESET']}")
            return False
        
        self.stop_monitoring = False
        self.monitor_thread = threading.Thread(
            target=self._monitor_orders, 
            args=(symbol, first_tranche_closed_func),
            daemon=True
        )
        self.monitor_thread.start()
        return True

    def stop(self):
        """
        Stop the order monitoring thread.
        
        Returns:
            bool: True if stopped successfully, False otherwise
        """
        if not self.monitor_thread or not self.monitor_thread.is_alive():
            return False
        
        self.stop_monitoring = True
        return True