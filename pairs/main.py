from datetime import datetime, timedelta
import numpy as np
from tqdm import tqdm  # for progress bar
from pairs_trader import PairsTrader
from data_utils import prepare_pair_data
from portfolio_utils import create_portfolio_df, save_results, calculate_risk_metrics
from config import PAIRS
import pandas as pd
import pytz


def print_summary(portfolio_df, pair_results):
    """Print summary statistics for the backtest"""
    print("\n=== Backtest Summary ===")
    print(
        f"Period: {portfolio_df.index[0].date()} to {portfolio_df.index[-1].date()}")
    print(f"Total Trading Days: {len(portfolio_df)}")

    # Print latest inverse volatility weights
    print("\n=== Latest Portfolio Weights ===")
    weight_cols = [
        col for col in portfolio_df.columns if col.endswith('_weight')]
    if weight_cols:
        # Get the second last row
        latest_weights = portfolio_df[weight_cols].iloc[-2]
        print("\nCurrent Inverse Volatility Weights:")
        total_weight = 0
        for col in sorted(weight_cols):
            pair_name = col.replace('_weight', '')
            weight = latest_weights[col]
            total_weight += weight
            print(f"  {pair_name}: {weight:.2%}")
        print(f"  Total Weight: {total_weight:.2%}")

    # Print individual pair statistics
    print("\n=== Individual Pair Performance ===")
    for pair_name, result in pair_results.items():
        returns = result['strategy_returns']['total_return']
        metrics = calculate_risk_metrics(returns)
        print(f"\n{pair_name}:")
        print(f"  Annual Return: {metrics['Annual Return']:.2%}")
        print(f"  Annual Volatility: {metrics['Annual Volatility']:.2%}")
        print(f"  Sharpe Ratio: {metrics['Sharpe Ratio']:.2f}")
        print(f"  Max Drawdown: {metrics['Max Drawdown']:.2%}")

        # Print average position size and utilization
        if 'position_size' in result['signals'].columns:
            avg_pos_size = result['signals']['position_size'].abs().mean()
            print(f"  Average Position Size: {avg_pos_size:.2f}")

    # Print portfolio statistics
    print("\n=== Portfolio Performance ===")
    for strategy in ['equal_weighted', 'inv_vol_weighted']:
        returns = portfolio_df[f'{strategy}_return']
        metrics = calculate_risk_metrics(returns)
        print(f"\n{strategy.replace('_', ' ').title()}:")
        print(f"  Annual Return: {metrics['Annual Return']:.2%}")
        print(f"  Annual Volatility: {metrics['Annual Volatility']:.2%}")
        print(f"  Sharpe Ratio: {metrics['Sharpe Ratio']:.2f}")
        print(f"  Max Drawdown: {metrics['Max Drawdown']:.2%}")

    # Print active pairs statistics
    avg_active_pairs = portfolio_df['active_pairs'].mean()
    max_active_pairs = portfolio_df['active_pairs'].max()
    print(f"\nActive Pairs Statistics:")
    print(f"  Average Active Pairs: {avg_active_pairs:.2f}")
    print(f"  Maximum Active Pairs: {max_active_pairs:.0f}")

    # Print correlation matrix of strategy returns
    print("\n=== Strategy Correlation Matrix ===")
    strategy_returns = pd.DataFrame()
    for pair_name in pair_results.keys():
        strategy_returns[pair_name] = pair_results[pair_name]['strategy_returns']['total_return']
    corr_matrix = strategy_returns.corr()
    print(corr_matrix.round(2))


def main():
    # Set date range
    end_date = datetime.now(pytz.timezone('US/Eastern'))
    start_date = end_date - timedelta(days=5*365)

    print(f"Starting backtest from {start_date.date()} to {end_date.date()}")

    # Store results for each pair
    pair_results = {}
    all_dates = set()

    # Run backtest for each pair
    print("\nProcessing pairs:")
    for pair in tqdm(PAIRS, desc="Running backtests"):
        ticker1, ticker2 = pair['ticker1'], pair['ticker2']
        pair_name = f"{ticker1}_{ticker2}"

        print(f"\nProcessing {pair_name}")
        returns_df, prices_df, vol_ratio = prepare_pair_data(
            ticker1, ticker2, start_date, end_date)
        all_dates.update(returns_df.index)

        trader = PairsTrader(
            ticker1=ticker1, ticker2=ticker2, vol_ratio=vol_ratio)
        strategy_returns, signals, options_df = trader.backtest(
            returns_df, prices_df)

        pair_results[pair_name] = {
            'strategy_returns': strategy_returns,
            'signals': signals,
            'options_df': options_df,
            'volatility': strategy_returns['total_return'].std() * np.sqrt(252)
        }

        # Print pair-specific statistics
        pair_metrics = calculate_risk_metrics(strategy_returns['total_return'])
        print(f"  Sharpe Ratio: {pair_metrics['Sharpe Ratio']:.2f}")
        print(f"  Annual Return: {pair_metrics['Annual Return']:.2%}")

    print("\nCreating portfolio and calculating returns...")
    portfolio_df = create_portfolio_df(pair_results, all_dates)

    print("\nSaving results...")
    save_results(portfolio_df, pair_results)

    # Print final summary
    print_summary(portfolio_df, pair_results)

    print("\nBacktest completed! Results saved in the 'results' directory.")


if __name__ == "__main__":
    main()
