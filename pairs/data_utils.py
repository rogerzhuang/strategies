import yfinance as yf
import pandas as pd
import numpy as np

def prepare_pair_data(ticker1, ticker2, start_date, end_date):
    ticker1_data = yf.download(ticker1, start=start_date, end=end_date)
    ticker2_data = yf.download(ticker2, start=start_date, end=end_date)
    
    # Align data
    common_dates = ticker1_data.index.intersection(ticker2_data.index)
    ticker1_data = ticker1_data.loc[common_dates]
    ticker2_data = ticker2_data.loc[common_dates]
    
    # Calculate returns and volatility
    ticker1_returns = ticker1_data['Adj Close'].pct_change()
    ticker2_returns = ticker2_data['Adj Close'].pct_change()
    
    ticker1_vol = ticker1_returns.std() * np.sqrt(252)
    ticker2_vol = ticker2_returns.std() * np.sqrt(252)
    vol_ratio = ticker1_vol / ticker2_vol
    
    returns_df = pd.DataFrame({
        ticker1: ticker1_returns,
        ticker2: ticker2_returns
    })
    
    prices_df = pd.DataFrame({
        ticker1: ticker1_data['Adj Close'],
        ticker2: ticker2_data['Adj Close']
    })
    
    return returns_df, prices_df, vol_ratio