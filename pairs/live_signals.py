import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from pairs_trader import PairsTrader
from config import PAIRS, DEFAULT_PARAMS
import os


class LiveSignalGenerator:
    def __init__(self):
        self.pairs = PAIRS
        self.lookback_days = max(
            # Ensure enough data for calculations
            DEFAULT_PARAMS['z_score_window'] * 2,
            365 * 5  # Minimum of 5 years
        )
        self.et_tz = pytz.timezone('US/Eastern')

        # Create signals directory if it doesn't exist
        os.makedirs('signals', exist_ok=True)

    def get_live_data(self, ticker, lookback_days):
        """Fetch historical + current day data for a ticker"""
        # Map BTC-USD to IBIT for live trading
        live_ticker = ticker
        for pair in self.pairs:
            if ticker == pair.get('ticker2') and 'live_ticker2' in pair:
                live_ticker = pair['live_ticker2']
                print(f"Mapping {ticker} to {live_ticker} for live trading")
                break

        end_date = datetime.now(self.et_tz)
        start_date = end_date - timedelta(days=lookback_days)

        # Get historical daily data using the mapped ticker
        hist_df = yf.download(live_ticker, start=start_date, end=end_date)

        # Get today's intraday data (1-minute intervals)
        ticker_obj = yf.Ticker(ticker)
        today_df = ticker_obj.history(period='1d', interval='1m')

        if not today_df.empty:
            # Use the latest price to update today's data
            latest_price = today_df['Close'].iloc[-1]
            current_date = end_date.date()
            
            # Convert index to date for comparison
            hist_df.index = hist_df.index.date
            
            if current_date not in hist_df.index:
                # Add today's data as a new row
                new_row = pd.Series({
                    'Open': today_df['Open'].iloc[0],
                    'High': today_df['High'].max(),
                    'Low': today_df['Low'].min(),
                    'Close': latest_price,
                    'Adj Close': latest_price,  # Approximation for today
                    'Volume': today_df['Volume'].sum()
                }, name=current_date)
                hist_df = pd.concat([hist_df, pd.DataFrame([new_row])])
            else:
                # Update today's data
                hist_df.loc[current_date, 'Adj Close'] = latest_price

            # Convert index back to datetime
            hist_df.index = pd.to_datetime(hist_df.index)

        # Print last 5 rows of data
        print(f"\nLatest data for {ticker}:")
        print(hist_df['Adj Close'].tail())

        return hist_df['Adj Close']

    def prepare_pair_data(self, ticker1, ticker2):
        """Prepare data for a single pair"""
        # For historical data preparation, use original tickers
        price1 = self.get_live_data(ticker1, self.lookback_days)
        price2 = self.get_live_data(ticker2, self.lookback_days)

        # Align data
        common_dates = price1.index.intersection(price2.index)
        price1 = price1[common_dates]
        price2 = price2[common_dates]

        # Calculate returns
        returns_df = pd.DataFrame({
            ticker1: price1.pct_change(),
            ticker2: price2.pct_change()
        })

        prices_df = pd.DataFrame({
            ticker1: price1,
            ticker2: price2
        })

        # Calculate volatility ratio
        vol1 = returns_df[ticker1].std() * np.sqrt(252)
        vol2 = returns_df[ticker2].std() * np.sqrt(252)
        vol_ratio = vol1 / vol2

        return returns_df, prices_df, vol_ratio

    def should_trade_options(self, current_time, previous_signals, current_signal):
        """Determine if we should trade options based on signal changes"""
        # Convert to US Eastern time if not already
        if current_time.tzinfo is None:
            current_time = self.et_tz.localize(current_time)
        elif current_time.tzinfo != self.et_tz:
            current_time = current_time.astimezone(self.et_tz)

        # Check if we're near market close (Eastern time)
        is_near_close = current_time.hour >= 15 and current_time.minute >= 00

        # Check if signal has changed
        signal_changed = (previous_signals is None or
                         previous_signals['position'].iloc[-1] != current_signal['position'])

        return is_near_close and signal_changed and abs(current_signal['position']) > 0

    def generate_signals(self):
        """Generate live signals for all pairs"""
        current_time = datetime.now(self.et_tz)
        signals = []

        # Load previous signals if available
        try:
            previous_signals = pd.read_csv('signals/previous_signals.csv')
            previous_signals['timestamp'] = pd.to_datetime(
                previous_signals['timestamp'])
        except FileNotFoundError:
            previous_signals = None

        for pair in self.pairs:
            ticker1, ticker2 = pair['ticker1'], pair['ticker2']

            try:
                # Prepare data
                returns_df, prices_df, vol_ratio = self.prepare_pair_data(
                    ticker1, ticker2)

                # Initialize trader
                trader = PairsTrader(
                    ticker1=ticker1, ticker2=ticker2, vol_ratio=vol_ratio)

                # Get latest signals
                pair_signals = trader.calculate_signals(returns_df)
                latest_signals = pair_signals.iloc[-1]

                # Get latest prices
                latest_prices = prices_df.iloc[-1]

                # Get previous signals for this pair
                prev_pair_signals = (
                    previous_signals[previous_signals['pair']
                                     == f"{ticker1}/{ticker2}"]
                    if previous_signals is not None else None
                )

                signal_dict = {
                    'timestamp': current_time,
                    'trade_type': 'pairs',
                    'pair': f"{ticker1}/{ticker2}",
                    'position': latest_signals['position'],
                    'position_size': latest_signals['position_size'],
                    'z_score': latest_signals['z_score'],
                    'correlation': latest_signals['correlation'],
                    'vol_ratio': vol_ratio,
                    'ticker1_price': latest_prices[ticker1],
                    'ticker2_price': latest_prices[ticker2]
                }

                # Check if we should trade options
                if self.should_trade_options(current_time, prev_pair_signals, latest_signals):
                    # Calculate options parameters
                    strike_price = latest_prices[ticker1] * \
                        (1.1 if latest_signals['position'] == -1 else 0.9)
                    premium = (
                        latest_signals['annualized_vol'] ** 2) * trader.vol_premium_multiplier

                    # Determine expiry based on day of week
                    expiry = 'next_week' if current_time.weekday() == 4 else 'this_week'

                    options_dict = signal_dict.copy()
                    options_dict.update({
                        'trade_type': 'options',
                        'option_type': 'call' if latest_signals['position'] == -1 else 'put',
                        'strike_price': strike_price,
                        'premium_target': premium,
                        'expiry': expiry
                    })
                    signals.append(options_dict)

                signals.append(signal_dict)

            except Exception as e:
                print(f"Error processing {ticker1}/{ticker2}: {str(e)}")

        # Create DataFrame and organize columns
        signals_df = pd.DataFrame(signals)

        # Define column order
        column_order = [
            'timestamp',
            'trade_type',
            'pair',
            'position',
            'position_size',
            'z_score',
            'correlation',
            'vol_ratio',
            'ticker1_price',
            'ticker2_price'
        ]

        # Add optional columns if they exist (for options)
        optional_columns = ['option_type',
                            'strike_price', 'premium_target', 'expiry']
        column_order.extend(
            [col for col in optional_columns if col in signals_df.columns])

        # Reorder columns
        signals_df = signals_df[column_order]

        # Save current signals for next comparison
        signals_df.to_csv('signals/previous_signals.csv', index=False)

        return signals_df

    def format_signals_report(self, signals_df):
        """Format signals into a readable report"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        report = [f"=== Live Trading Signals ({current_time}) ===\n"]

        # Separate pairs and options signals
        pairs_signals = signals_df[signals_df['trade_type'] == 'pairs']
        options_signals = signals_df[signals_df['trade_type'] == 'options']

        # Format pairs signals
        report.append("=== Pairs Trading Signals ===")
        for _, row in pairs_signals.iterrows():
            if row['position'] != 0:
                action = "LONG" if row['position'] > 0 else "SHORT"
                report.append(f"\n{row['pair']}:")
                report.append(f"  Action: {action}")
                report.append(f"  Position Size: {row['position_size']:.2%}")
                report.append(f"  Z-Score: {row['z_score']:.2f}")
                report.append(f"  Correlation: {row['correlation']:.2f}")

        # Format options signals
        if not options_signals.empty:
            report.append("\n=== Weekly Options Signals ===")
            for _, row in options_signals.iterrows():
                report.append(f"\n{row['pair']} Options:")
                report.append(f"  Type: {row['option_type'].upper()}")
                report.append(f"  Strike: ${row['strike_price']:.2f}")
                report.append(f"  Premium Target: {row['premium_target']:.2%}")
                report.append(f"  Position Size: {row['position_size']:.2%}")

        return "\n".join(report)


def main():
    """Main function to generate and display live signals"""
    generator = LiveSignalGenerator()
    signals_df = generator.generate_signals()
    report = generator.format_signals_report(signals_df)

    # Print report
    print(report)

    # Save signals to CSV
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    signals_df.to_csv(f'signals/live_signals_{timestamp}.csv', index=False)

    return signals_df, report


if __name__ == "__main__":
    main()
