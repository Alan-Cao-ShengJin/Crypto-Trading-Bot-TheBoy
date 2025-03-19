import pandas as pd
import numpy as np

def supertrend_indicator(df, periods=10, multiplier=3.0, change_atr=True, src='hl2'):
    """
    Calculate the SuperTrend indicator and generate buy/sell signals.
    
    Parameters:
    - df (pd.DataFrame): DataFrame with 'high', 'low', 'close' columns.
    - periods (int): ATR calculation period (default: 10).
    - multiplier (float): ATR multiplier for bands (default: 3.0).
    - change_atr (bool): True for EMA-based ATR, False for SMA-based (default: True).
    - src (str or pd.Series): Source price column name or 'hl2' for (high + low)/2 (default: 'hl2').
    
    Returns:
    - pd.DataFrame: DataFrame with added columns: 'src', 'tr', 'atr', 'up', 'dn', 'trend',
                    'buySignal', 'sellSignal'.
    """
    # Ensure DataFrame has required columns
    required_cols = ['high', 'low', 'close']
    if not all(col in df.columns for col in required_cols):
        raise ValueError("DataFrame must contain 'high', 'low', 'close' columns")

    # Calculate source price
    if src == 'hl2':
        df['src'] = (df['high'] + df['low']) / 2
    elif src in df.columns:
        df['src'] = df[src]
    else:
        raise ValueError("Invalid src: must be 'hl2' or a column name in DataFrame")

    # Calculate True Range (TR)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )

    # Calculate ATR
    if change_atr:
        # EMA with alpha = 1/periods to match Pine Script's atr(Periods)
        df['atr'] = df['tr'].ewm(alpha=1/periods, adjust=False).mean()
    else:
        # SMA over periods
        df['atr'] = df['tr'].rolling(window=periods).mean()

    # Initialize columns
    df['up'] = np.nan
    df['dn'] = np.nan
    df['trend'] = 1  # Start with uptrend

    # Set initial up and dn for the first bar
    first_idx = df.index[0]
    df.loc[first_idx, 'up'] = df.loc[first_idx, 'src'] - multiplier * df.loc[first_idx, 'atr']
    df.loc[first_idx, 'dn'] = df.loc[first_idx, 'src'] + multiplier * df.loc[first_idx, 'atr']

    # Iterate over subsequent bars
    for i in range(1, len(df)):
        curr_idx = df.index[i]
        prev_idx = df.index[i - 1]

        # Candidate up and dn
        candidate_up = df.loc[curr_idx, 'src'] - multiplier * df.loc[curr_idx, 'atr']
        candidate_dn = df.loc[curr_idx, 'src'] + multiplier * df.loc[curr_idx, 'atr']

        # Previous up and dn
        up1 = df.loc[prev_idx, 'up']
        dn1 = df.loc[prev_idx, 'dn']

        # Update up
        df.loc[curr_idx, 'up'] = (
            max(candidate_up, up1) if df.loc[prev_idx, 'close'] > up1 else candidate_up
        )

        # Update dn
        df.loc[curr_idx, 'dn'] = (
            min(candidate_dn, dn1) if df.loc[prev_idx, 'close'] < dn1 else candidate_dn
        )

        # Update trend
        trend_prev = df.loc[prev_idx, 'trend']
        if trend_prev == 1 and df.loc[curr_idx, 'close'] < up1:
            df.loc[curr_idx, 'trend'] = -1
        elif trend_prev == -1 and df.loc[curr_idx, 'close'] > dn1:
            df.loc[curr_idx, 'trend'] = 1
        else:
            df.loc[curr_idx, 'trend'] = trend_prev

    # Generate buy and sell signals
    df['buySignal'] = (df['trend'] == 1) & (df['trend'].shift(1) == -1)
    df['sellSignal'] = (df['trend'] == -1) & (df['trend'].shift(1) == 1)

    return df

# Example usage (uncomment to test)
# if __name__ == "__main__":
#     # Sample DataFrame
#     data = {
#         'high': [105, 106, 107, 108, 107, 106, 105, 104],
#         'low': [103, 104, 105, 106, 105, 104, 103, 102],
#         'close': [104, 105, 106, 107, 106, 105, 104, 103]
#     }
#     df = pd.DataFrame(data)
#     df = supertrend_indicator(df, periods=3, multiplier=1.0, change_atr=True)
#     print(df[['close', 'trend', 'buySignal', 'sellSignal']])