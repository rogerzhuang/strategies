import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import json
import os

def calculate_risk_metrics(returns):
    annual_return = (1 + returns.mean()) ** 252 - 1
    annual_vol = returns.std() * np.sqrt(252)
    sharpe_ratio = annual_return / annual_vol if annual_vol != 0 else 0
    max_drawdown = (1 + returns).cumprod().div((1 + returns).cumprod().cummax()) - 1
    return {
        'Annual Return': float(annual_return),
        'Annual Volatility': float(annual_vol),
        'Sharpe Ratio': float(sharpe_ratio),
        'Max Drawdown': float(max_drawdown.min())
    }

def create_portfolio_df(pair_results, all_dates):
    portfolio_df = pd.DataFrame(index=sorted(all_dates))
    
    # Calculate pair volatilities
    pair_vols = {pair_name: result['volatility'] 
                 for pair_name, result in pair_results.items()}
    
    # Add pair returns
    for pair_name, result in pair_results.items():
        returns = result['strategy_returns']['total_return']
        portfolio_df[f'{pair_name}_return'] = returns
        portfolio_df[f'{pair_name}_cum_return'] = (1 + returns).cumprod()
    
    # Calculate active pairs
    portfolio_df['active_pairs'] = portfolio_df[
        [col for col in portfolio_df.columns if col.endswith('_return') and not col.startswith(('equal_', 'inv_vol_'))]
    ].notna().sum(axis=1)
    
    # Calculate inverse volatility weights for each day
    for date in portfolio_df.index:
        weights = calculate_inv_vol_weights(portfolio_df.loc[date], pair_vols)
        for pair_name in pair_results.keys():
            portfolio_df.loc[date, f'{pair_name}_weight'] = weights[pair_name]
    
    # Calculate equal-weighted returns
    equal_weighted_returns = calculate_equal_weighted_returns(portfolio_df, pair_results.keys())
    portfolio_df['equal_weighted_return'] = equal_weighted_returns
    portfolio_df['equal_weighted_cum_return'] = (1 + equal_weighted_returns).cumprod()
    
    # Calculate inverse-volatility weighted returns
    inv_vol_returns = calculate_inv_vol_weighted_returns(portfolio_df, pair_results)
    portfolio_df['inv_vol_weighted_return'] = inv_vol_returns
    portfolio_df['inv_vol_weighted_cum_return'] = (1 + inv_vol_returns).cumprod()
    
    return portfolio_df

def calculate_equal_weighted_returns(portfolio_df, pair_names):
    equal_weighted_returns = pd.DataFrame(index=portfolio_df.index)
    for pair_name in pair_names:
        equal_weighted_returns[pair_name] = portfolio_df[f'{pair_name}_return'].divide(portfolio_df['active_pairs']) * 2
    return equal_weighted_returns.sum(axis=1)

def calculate_inv_vol_weighted_returns(portfolio_df, pair_results):
    # Calculate pair volatilities
    pair_vols = {pair_name: result['volatility'] for pair_name, result in pair_results.items()}
    
    # Calculate weights and returns
    inv_vol_weighted_returns = pd.DataFrame(index=portfolio_df.index)
    for date in portfolio_df.index:
        weights = calculate_inv_vol_weights(portfolio_df.loc[date], pair_vols)
        for pair_name in pair_results.keys():
            pair_return = portfolio_df.loc[date, f'{pair_name}_return']
            inv_vol_weighted_returns.loc[date, pair_name] = (
                pair_return * weights[pair_name] if not pd.isna(pair_return) else 0
            )
    
    return inv_vol_weighted_returns.sum(axis=1)

def calculate_inv_vol_weights(row, pair_vols):
    """
    Calculate inverse volatility weights for active pairs on a given day.
    
    Args:
        row: DataFrame row containing pair returns
        pair_vols: Dictionary of pair volatilities
    
    Returns:
        pd.Series: Weights for each pair
    """
    active_pairs = {pair_name: vol for pair_name, vol in pair_vols.items() 
                   if not pd.isna(row[f'{pair_name}_return'])}
    
    if not active_pairs:
        return pd.Series(0, index=pair_vols.keys())
    
    total_inv_vol = sum(1/vol for vol in active_pairs.values() if vol > 0)
    weights = {pair_name: (1/vol)/total_inv_vol if vol > 0 else 0 
              for pair_name, vol in active_pairs.items()}
    
    # Add 0 weight for inactive pairs
    weights.update({pair_name: 0 for pair_name in pair_vols.keys() 
                   if pair_name not in active_pairs})
    
    return pd.Series(weights)

def save_results(portfolio_df, pair_results, output_dir='results'):
    os.makedirs(output_dir, exist_ok=True)
    
    # Save portfolio results
    portfolio_df.to_csv(f'{output_dir}/portfolio_results.csv')
    
    # Save performance metrics
    metrics = {
        'equal_weighted': calculate_risk_metrics(portfolio_df['equal_weighted_return']),
        'inverse_vol_weighted': calculate_risk_metrics(portfolio_df['inv_vol_weighted_return'])
    }
    
    with open(f'{output_dir}/performance_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=4)
    
    # Create visualization
    create_performance_plot(portfolio_df, pair_results, output_dir)

def create_performance_plot(portfolio_df, pair_results, output_dir):
    plt.figure(figsize=(15, 10))
    
    # Plot individual pair returns
    for pair_name in pair_results.keys():
        cum_returns = portfolio_df[f'{pair_name}_cum_return']
        valid_data = cum_returns.dropna()
        if not valid_data.empty:
            plt.plot(valid_data.index, valid_data, alpha=0.3, linestyle='--', label=f'{pair_name}')
    
    # Plot portfolio returns
    plt.plot(portfolio_df['equal_weighted_cum_return'], 
             linewidth=2, label='Equal-Weighted Portfolio', color='brown')
    plt.plot(portfolio_df['inv_vol_weighted_cum_return'], 
             linewidth=2, label='Inverse-Vol Weighted Portfolio', color='magenta')
    
    plt.title('Portfolio Performance')
    plt.xlabel('Date')
    plt.ylabel('Cumulative Return')
    plt.legend(bbox_to_anchor=(0.5, -0.15), loc='lower center', ncol=3)
    plt.grid(True)
    plt.tight_layout()
    
    # Save and display the plot
    plt.savefig(f'{output_dir}/portfolio_performance.png')
    # plt.show()  # This will display the plot
    plt.close()