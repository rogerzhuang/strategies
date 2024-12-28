import pandas as pd
import numpy as np
from config import DEFAULT_PARAMS

class PairsTrader:
    def __init__(self, ticker1, ticker2, vol_ratio, **kwargs):
        self.ticker1 = ticker1
        self.ticker2 = ticker2
        self.vol_ratio = vol_ratio
        
        # Set parameters using defaults and any provided overrides
        params = DEFAULT_PARAMS.copy()
        params.update(kwargs)
        
        for key, value in params.items():
            setattr(self, key, value)
    
    def calculate_signals(self, returns_df):
        # Calculate volatility-adjusted difference
        vol_adjusted_diff = returns_df[self.ticker1] - (self.vol_ratio * returns_df[self.ticker2])
        
        # Calculate z-score
        rolling_mean = vol_adjusted_diff.rolling(window=self.z_score_window).mean()
        rolling_std = vol_adjusted_diff.rolling(window=self.z_score_window).std()
        z_score = (vol_adjusted_diff - rolling_mean) / rolling_std
        
        # Calculate correlation
        rolling_corr = returns_df.rolling(window=self.z_score_window).corr().unstack()[self.ticker1][self.ticker2]
        
        # Calculate volatility for option premium
        rolling_vol = returns_df[self.ticker1].rolling(window=self.z_score_window).std() * np.sqrt(252)
        
        signals = pd.DataFrame(index=returns_df.index)
        signals['z_score'] = z_score
        signals['correlation'] = rolling_corr
        signals['position'] = 0
        signals['annualized_vol'] = rolling_vol
        
        # Generate positions
        signals.loc[z_score < -self.z_score_threshold, 'position'] = 1
        signals.loc[z_score > self.z_score_threshold, 'position'] = -1
        signals.loc[rolling_corr < self.correlation_threshold, 'position'] = 0
        
        # Calculate position sizes using sigmoid function
        z_score_for_sizing = z_score.clip(-3, 3)
        position_sizes = 1 / (1 + np.exp(-1.5 * (abs(z_score_for_sizing) - self.z_score_threshold)))
        signals['position_size'] = position_sizes * self.max_position
        
        return signals
    
    def calculate_weekly_options_payoff(self, prices_df, signals):
        # Create timezone-naive index
        prices_df = prices_df.copy()
        prices_df.index = pd.to_datetime(prices_df.index).tz_localize(None)
        
        # Initialize options DataFrame
        options_df = pd.DataFrame(index=prices_df.index)
        options_df[f'{self.ticker1}_price'] = prices_df[self.ticker1]
        options_df['signal'] = signals['position']
        options_df['position_size'] = signals['position_size']
        options_df['week'] = options_df.index.to_period('W-FRI')
        
        # Get week-end prices
        week_end_prices = {}
        for week in options_df['week'].unique():
            week_data = options_df[options_df['week'] == week]
            week_end_prices[week] = week_data[f'{self.ticker1}_price'].iloc[-1]
        
        # Initialize position tracking
        options_df['active_positions'] = options_df.apply(lambda x: [], axis=1)
        options_df['option_payoff'] = 0.0
        
        # Detect signal changes
        signal_changes = options_df['signal'] != options_df['signal'].shift(1)
        change_points = options_df[signal_changes].index
        
        # Process each signal change
        for idx in change_points:
            current_signal = options_df.loc[idx, 'signal']
            if current_signal == 0:
                continue
            
            current_price = options_df.loc[idx, f'{self.ticker1}_price']
            current_week = options_df.loc[idx, 'week']
            position_size = options_df.loc[idx, 'position_size']
            
            # Calculate dynamic premium based on volatility
            entry_vol = signals.loc[idx, 'annualized_vol']
            premium = (entry_vol ** 2) * self.vol_premium_multiplier
            
            # Determine expiration week
            is_last_day_of_week = idx == options_df[options_df['week'] == current_week].index[-1]
            expiration_week = current_week + 1 if is_last_day_of_week else current_week
            
            if expiration_week in week_end_prices:
                # Create new option position
                new_position = {
                    'signal': current_signal,
                    'entry_price': current_price,
                    'strike_price': current_price * (1.1 if current_signal == -1 else 0.9),
                    'expiration_week': expiration_week,
                    'position_size': position_size,
                    'premium': premium,
                    'entry_date': idx,
                    'vol': entry_vol
                }
                
                # Add position to active positions
                mask = (options_df.index >= idx) & (options_df['week'] <= expiration_week)
                for day in options_df[mask].index:
                    options_df.at[day, 'active_positions'].append(new_position.copy())
        
        # Calculate payoffs at expiration
        for week in options_df['week'].unique():
            week_end_idx = options_df[options_df['week'] == week].index[-1]
            week_end_price = options_df.loc[week_end_idx, f'{self.ticker1}_price']
            
            for position in options_df.loc[week_end_idx, 'active_positions']:
                if position['expiration_week'] == week:
                    # Calculate payoff based on option type
                    if position['signal'] == -1:  # Short call
                        payoff = min(0, position['strike_price'] - week_end_price)
                    else:  # Short put
                        payoff = min(0, week_end_price - position['strike_price'])
                    
                    # Add premium and adjust for position size
                    adjusted_payoff = (payoff + position['entry_price'] * position['premium']) * position['position_size']
                    options_df.loc[week_end_idx, 'option_payoff'] += adjusted_payoff
        
        # Add analytics columns
        options_df['active_option_count'] = options_df['active_positions'].apply(len)
        options_df['avg_position_vol'] = options_df['active_positions'].apply(
            lambda x: np.mean([p['vol'] for p in x]) if x else 0)
        options_df['avg_premium'] = options_df['active_positions'].apply(
            lambda x: np.mean([p['premium'] for p in x]) if x else 0)
        
        return options_df
    
    def backtest(self, returns_df, prices_df):
        # Calculate signals
        signals = self.calculate_signals(returns_df)
        
        # Use previously calculated position sizes
        ticker1_position = signals['position_size'] * signals['position']
        ticker2_position = ticker1_position * self.vol_ratio
        
        strategy_returns = pd.DataFrame(index=returns_df.index)
        strategy_returns[f'{self.ticker1}_return'] = ticker1_position.shift(1) * returns_df[self.ticker1]
        strategy_returns[f'{self.ticker2}_return'] = -ticker2_position.shift(1) * returns_df[self.ticker2]
    
        # Calculate options payoffs
        options_df = self.calculate_weekly_options_payoff(prices_df, signals)
        strategy_returns['options_return'] = options_df['option_payoff'] / prices_df[self.ticker1]
        
        # Calculate total strategy return
        strategy_returns['total_return'] = (
            strategy_returns[f'{self.ticker1}_return'] + 
            strategy_returns[f'{self.ticker2}_return'] + 
            strategy_returns['options_return']
        )
        
        strategy_returns['cumulative_return'] = (1 + strategy_returns['total_return']).cumprod()
        
        return strategy_returns, signals, options_df