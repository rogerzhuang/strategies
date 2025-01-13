from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from datetime import datetime, timedelta
import glob
import os
import sys
import yfinance as yf
import pandas_market_calendars as mcal
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess
import pytz
import threading

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DEFAULT_PARAMS

app = Flask(__name__)

def run_startup_jobs():
    """Run startup jobs in background thread"""
    print("Running startup jobs in background...")
    def run_jobs():
        try:
            print(f"Running main.py at {datetime.now(pytz.timezone('US/Eastern'))}")
            subprocess.run(['python', '../main.py'])
            print("main.py completed")
        except Exception as e:
            print(f"Error running main.py: {e}")

    # Start jobs in a separate thread
    job_thread = threading.Thread(target=run_jobs)
    job_thread.daemon = True  # Thread will exit when main program exits
    job_thread.start()

def init_scheduler():
    """Initialize the APScheduler"""
    # Check if we're in the main Flask process
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return None
        
    sched = BackgroundScheduler(daemon=True)
    et_tz = pytz.timezone('US/Eastern')

    # Define the job functions
    def run_main():
        print(f"Running main.py at {datetime.now(et_tz)}")
        subprocess.run(['python', '../main.py'])

    def run_live_signals():
        print(f"Running live_signals.py at {datetime.now(et_tz)}")
        subprocess.run(['python', '../live_signals.py'])

    # Run startup jobs in background
    run_startup_jobs()

    # Schedule main.py to run at 4:15 PM ET on weekdays
    sched.add_job(
        run_main,
        trigger='cron',
        day_of_week='mon-fri',
        hour=16,
        minute=15,
        timezone=et_tz
    )

    # Schedule live_signals.py to run at 3:50 PM ET on weekdays
    sched.add_job(
        run_live_signals,
        trigger='cron',
        day_of_week='mon-fri',
        hour=15,
        minute=50,
        timezone=et_tz
    )

    sched.start()
    return sched


scheduler = init_scheduler()


def load_signals(date_str):
    """Load signals for a specific date"""
    signals_dir = './signals'
    # Find the file for the specified date
    date_pattern = date_str.replace('-', '')
    matching_files = glob.glob(f"{signals_dir}/live_signals_{date_pattern}*.csv")
    
    if not matching_files:
        return None
    
    # Use the latest file if multiple exist for the same date
    latest_file = max(matching_files)
    return pd.read_csv(latest_file)

def load_portfolio_weights():
    """Load the latest weights from portfolio results"""
    results_path = './results/portfolio_results.csv'
    try:
        df = pd.read_csv(results_path)
        # Get second last row for weights
        weights = df.iloc[-2]
        # Extract only weight columns
        weight_cols = [col for col in df.columns if col.endswith('_weight') and not col.startswith('equal_') and not col.startswith('inv_vol_')]
        weights = weights[weight_cols].to_dict()
        # Clean up column names to match pair format
        weights = {col.replace('_weight', '').replace('_', '/'): val for col, val in weights.items()}
        return weights
    except Exception as e:
        print(f"Error loading portfolio weights: {e}")
        return {}

def get_next_option_expiry(date_str, week='this'):
    """
    Get the exact expiry date for options, accounting for shortened trading weeks
    Args:
        date_str: YYYY-MM-DD format
        week: 'this' or 'next'
    Returns:
        expiry date string in YYYY-MM-DD format
    """
    nyse = mcal.get_calendar('NYSE')
    start_date = datetime.strptime(date_str, '%Y-%m-%d')
    
    # Get next 10 trading days to ensure we capture this week and next week
    schedule = nyse.schedule(start_date=start_date, end_date=start_date + timedelta(days=14))
    trading_days = schedule.index.strftime('%Y-%m-%d').tolist()
    
    if not trading_days:
        return None
        
    # Find the current week's trading days
    current_date = start_date.strftime('%Y-%m-%d')
    current_date_idx = trading_days.index(current_date)
    
    # Group trading days by week
    current_week = []
    next_week = []
    current_week_num = datetime.strptime(trading_days[current_date_idx], '%Y-%m-%d').isocalendar()[1]
    
    for day in trading_days[current_date_idx:]:
        day_obj = datetime.strptime(day, '%Y-%m-%d')
        week_num = day_obj.isocalendar()[1]
        
        if week_num == current_week_num:
            current_week.append(day)
        elif not next_week or week_num == datetime.strptime(next_week[0], '%Y-%m-%d').isocalendar()[1]:
            next_week.append(day)
        else:  # Different week than both current and next week
            break
    
    # Get the last trading day of the requested week
    if week == 'this':
        return current_week[-1] if current_week else None
    else:
        return next_week[-1] if next_week else None

def validate_option_strike(ticker, current_price, expiry_date, option_type='put'):
    """
    Validate and find the closest appropriate strike price
    Args:
        ticker: Stock symbol
        current_price: Current stock price
        expiry_date: Option expiry date
        option_type: 'call' or 'put'
    Returns:
        tuple of (strike_price, premium) or (None, None) if no valid strike found
    """
    try:
        stock = yf.Ticker(ticker)
        options = stock.option_chain(expiry_date)
        
        if option_type == 'put':
            chain = options.puts
            # Target range for puts: -8% to -12% below current price
            min_strike = current_price * 0.88
            max_strike = current_price * 0.92
        else:
            chain = options.calls
            # Target range for calls: +8% to +12% above current price
            min_strike = current_price * 1.08
            max_strike = current_price * 1.12
        
        # Filter strikes within our range
        valid_strikes = chain[
            (chain['strike'] >= min_strike) & 
            (chain['strike'] <= max_strike)
        ]
        
        if valid_strikes.empty:
            return None, None
            
        # Find strike closest to the middle of our range
        target_strike = (min_strike + max_strike) / 2
        closest_strike = valid_strikes.iloc[
            (valid_strikes['strike'] - target_strike).abs().argsort()[:1]
        ]
        
        return closest_strike['strike'].iloc[0], closest_strike['lastPrice'].iloc[0]
        
    except Exception as e:
        print(f"Error validating option strike for {ticker}: {e}")
        return None, None

def calculate_orders(signals_df, total_capital):
    """Calculate specific orders based on signals"""
    max_position = DEFAULT_PARAMS['max_position']
    orders = []
    
    # Load portfolio weights
    portfolio_weights = load_portfolio_weights()
    
    # Track pairs that need position squaring
    active_pairs = set()
    
    for _, row in signals_df.iterrows():
        pair = row['pair']
        ticker1, ticker2 = pair.split('/')
        
        if row['trade_type'] == 'pairs':
            active_pairs.add(pair)
            
            # Get pair weight from portfolio weights
            pair_weight = portfolio_weights.get(pair, 0)
            
            # Calculate position sizes with weight
            pair_capital = total_capital * pair_weight * row['position_size'] * max_position
            
            if row['position'] != 0:
                # Calculate number of shares for ticker1
                shares1 = int(pair_capital / row['ticker1_price'])
                # Calculate number of shares for ticker2 using vol_ratio
                shares2 = int(pair_capital * row['vol_ratio'] / row['ticker2_price'])
                
                action1 = "BUY" if row['position'] > 0 else "SELL"
                action2 = "SELL" if row['position'] > 0 else "BUY"
                
                orders.append({
                    'pair': pair,
                    'type': 'PAIRS',
                    'ticker': ticker1,
                    'action': action1,
                    'shares': abs(shares1),
                    'price': row['ticker1_price'],
                    'notional': abs(shares1 * row['ticker1_price'])
                })
                
                orders.append({
                    'pair': pair,
                    'type': 'PAIRS',
                    'ticker': ticker2,
                    'action': action2,
                    'shares': abs(shares2),
                    'price': row['ticker2_price'],
                    'notional': abs(shares2 * row['ticker2_price'])
                })
            else:
                # Add square position orders if needed
                orders.append({
                    'pair': pair,
                    'type': 'PAIRS',
                    'ticker': ticker1,
                    'action': "SQUARE",
                    'shares': 0,
                    'price': row['ticker1_price'],
                    'notional': 0
                })
                orders.append({
                    'pair': pair,
                    'type': 'PAIRS',
                    'ticker': ticker2,
                    'action': "SQUARE",
                    'shares': 0,
                    'price': row['ticker2_price'],
                    'notional': 0
                })
        
        elif row['trade_type'] == 'options':
            # Get exact expiry date
            expiry_date = get_next_option_expiry(
                row['timestamp'].split()[0],
                'this' if row['expiry'] == 'this_week' else 'next'
            )
            
            if not expiry_date:
                continue
                
            # Validate option strike
            strike_price, premium = validate_option_strike(
                ticker1,
                row['ticker1_price'],
                expiry_date,
                row['option_type']
            )
            
            if strike_price is None:
                continue
                
            # Get pair weight from portfolio weights
            pair_weight = portfolio_weights.get(pair, 0)
            
            # Calculate options contracts based on stock position with weight
            pair_capital = total_capital * pair_weight * row['position_size'] * max_position
            stock_shares = int(pair_capital / row['ticker1_price'])
            contracts = abs(stock_shares) // 100
            
            orders.append({
                'pair': pair,
                'type': 'OPTIONS',
                'ticker': f"{ticker1} {row['option_type'].upper()}",
                'action': 'SELL',
                'shares': contracts,
                'price': strike_price,
                'notional': contracts * 100 * strike_price,
                'expiry': expiry_date,
                'premium': premium,
                'premium_target': row['premium_target']
            })
    
    return orders

@app.route('/', methods=['GET', 'POST'])
def index():
    available_dates = []
    signals_dir = './signals'
    
    # Get available dates from signal files
    for file in glob.glob(f"{signals_dir}/live_signals_*.csv"):
        date_str = os.path.basename(file).split('_')[2][:8]  # Extract YYYYMMDD
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        available_dates.append(date_obj.strftime('%Y-%m-%d'))
    
    available_dates = sorted(set(available_dates), reverse=True)
    
    if request.method == 'POST':
        date = request.form.get('date')
        total_capital = float(request.form.get('capital', 1000000))
        
        signals_df = load_signals(date)
        if signals_df is not None:
            orders = calculate_orders(signals_df, total_capital)
            return render_template('orders.html', 
                                orders=orders, 
                                dates=available_dates,
                                selected_date=date,
                                total_capital=total_capital)
    
    return render_template('index.html', 
                         dates=available_dates,
                         selected_date=available_dates[0] if available_dates else None,
                         total_capital=1000000)

@app.route('/signals/<date>/<capital>')
def get_pair_trade_signals(date, capital):
    """
    REST endpoint for pair trade signals
    Args:
        date (str): Date in YYYYMMDD format
        capital (str): Total capital to trade
    Returns:
        JSON with structured signals for trading execution
    """
    try:
        # Convert date to expected format and validate
        date_obj = datetime.strptime(date, '%Y%m%d')
        formatted_date = date_obj.strftime('%Y-%m-%d')
        total_capital = float(capital)
        
        signals_df = load_signals(formatted_date)
        if signals_df is None:
            return jsonify({'error': 'No signals found for specified date'}), 404
            
        # Structure the response
        response = {
            'timestamp': signals_df['timestamp'].iloc[0],
            'total_capital': total_capital,
            'pairs_trades': [],
            'options_trades': []
        }
        
        orders = calculate_orders(signals_df, total_capital)
        
        # Group orders by pair and type
        pairs_map = {}
        options_list = []
        
        for order in orders:
            if order['type'] == 'PAIRS':
                pair = order['pair']
                if pair not in pairs_map:
                    pairs_map[pair] = {
                        'pair': pair,
                        'legs': [],
                        'action': 'SQUARE' if order['action'] == 'SQUARE' else 'TRADE'
                    }
                if order['action'] != 'SQUARE':
                    pairs_map[pair]['legs'].append({
                        'ticker': order['ticker'],
                        'action': order['action'],
                        'quantity': order['shares'],
                        'price': order['price']
                    })
            else:  # OPTIONS
                options_list.append({
                    'pair': order['pair'],
                    'contract': order['ticker'],
                    'action': order['action'],
                    'strike': order['price'],
                    'contracts': order['shares'],
                    'expiry': order['expiry'],
                    'premium_target': order['premium_target']
                })
        
        response['pairs_trades'] = list(pairs_map.values())
        response['options_trades'] = options_list
        
        return jsonify(response)
        
    except ValueError as e:
        return jsonify({'error': f'Invalid input format: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/backtest/performance')
def backtest_performance():
    """
    REST endpoint to serve the backtest performance plot
    Returns:
        Portfolio performance plot image
    """
    try:
        image_path = './results/portfolio_performance.png'
        
        if not os.path.exists(image_path):
            return jsonify({'error': 'Portfolio performance image not found'}), 404
            
        return send_file(
            image_path,
            mimetype='image/png',
            max_age=300
        )
        
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/backtest/results')
def show_results():
    """
    Endpoint to render the results dashboard
    Returns:
        Rendered results.html template
    """
    return render_template('results.html')

@app.route('/backtest/data')
def backtest_data():
    """
    REST endpoint to serve the backtest results in tabular format
    Returns:
        JSON formatted portfolio results data
    """
    try:
        csv_path = './results/portfolio_results.csv'
        
        if not os.path.exists(csv_path):
            return jsonify({'error': 'Portfolio results file not found'}), 404
            
        # Read CSV file and convert to simple list of dictionaries
        df = pd.read_csv(csv_path)
        
        # Basic data cleaning - replace NaN with 0
        df = df.fillna(0)
        
        # Convert to simple dictionary format
        data = {
            'columns': df.columns.tolist(),
            'data': df.values.tolist()
        }
        
        return jsonify(data)
        
    except Exception as e:
        print(f"Error in backtest_data: {str(e)}")  # Log the actual error
        return jsonify({'error': f'Server error: {str(e)}'}), 500

if __name__ == '__main__':
    try:
        app.run(debug=True, port=5002, host='0.0.0.0')
    finally:
        if scheduler:  # Only shutdown if scheduler exists
            scheduler.shutdown()
