from flask import Flask, jsonify
from datetime import datetime, timedelta
import yfinance as yf
import json
import os
import glob
from live_signals import get_next_weekly_expiry, find_closest_strike_simple
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import marketcaps
import weeklies
import live_signals
import threading

app = Flask(__name__)

def run_startup_jobs():
    print("Running startup jobs in background...")
    def run_jobs():
        # Run marketcaps first
        marketcaps.main()
        print("Marketcaps job completed")
        # Then run weeklies
        weeklies.main()
        print("Weeklies job completed")
    
    # Start jobs in a separate thread
    job_thread = threading.Thread(target=run_jobs)
    job_thread.daemon = True  # Thread will exit when main program exits
    job_thread.start()

def init_scheduler():
    # Check if we're in the reloader process
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        # Run startup jobs in background
        run_startup_jobs()
        
        scheduler = BackgroundScheduler(daemon=True)
        eastern = pytz.timezone('US/Eastern')

        scheduler.add_job(
            marketcaps.main,
            trigger=CronTrigger(
                day_of_week='fri',
                hour=16,
                minute=15,
                timezone=eastern
            ),
            name='marketcaps_job'
        )

        scheduler.add_job(
            weeklies.main,
            trigger=CronTrigger(
                day_of_week='fri',
                hour=17,
                minute=0,
                timezone=eastern
            ),
            name='weeklies_job'
        )

        scheduler.add_job(
            live_signals.main,
            trigger=CronTrigger(
                day_of_week='thu',
                hour=12,
                minute=45,
                timezone=eastern
            ),
            name='live_signals_midday'
        )

        scheduler.add_job(
            live_signals.main,
            trigger=CronTrigger(
                day_of_week='thu',
                hour=16,
                minute=15,
                timezone=eastern
            ),
            name='live_signals_close'
        )

        scheduler.start()
        return scheduler
    return None

@app.route('/signals/<int:strategy>/<date>/<int:capital>')
def get_signals_with_allocation(strategy, date, capital):
    try:
        # Validate strategy
        if strategy not in [1, 2]:
            return jsonify({"error": "Invalid strategy. Use 1 for Thursday or 2 for Friday"}), 400

        # Validate date format (yyyymmdd)
        try:
            target_date = datetime.strptime(date, '%Y%m%d')
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYYMMDD"}), 400

        # Check if it's the correct trading day for the strategy
        weekday = target_date.weekday()
        if (strategy == 1 and weekday != 3) or (strategy == 2 and weekday != 4):  # 3 is Thursday, 4 is Friday
            return jsonify({"options_trades": []})

        # Get previous day's date in YYYYMMDD format
        prev_date = (target_date - timedelta(days=1)).strftime('%Y%m%d')

        # Find files for both target date and previous day
        pattern_today = f'signals/live_signals_{date}_*.json'
        pattern_yesterday = f'signals/live_signals_{prev_date}_*.json'

        matching_files = glob.glob(pattern_today) + \
            glob.glob(pattern_yesterday)

        if not matching_files:
            return jsonify({"error": "No signals file found for the given date or previous day"}), 404

        # Get the latest file
        filename = max(matching_files)

        with open(filename, 'r') as f:
            signals = json.load(f)

        num_trades = len(signals['options_trades'])
        if num_trades == 0:
            return jsonify({"options_trades": []})

        # Update strikes based on current prices
        for trade in signals['options_trades']:
            ticker = trade['contract'].split()[0]
            stock = yf.Ticker(ticker)
            try:
                # Try fast_info first
                current_price = stock.fast_info['lastPrice']
            except Exception as e:
                print(
                    f"Could not get real-time price for {ticker}, error: {e}, skipping strike update")
                continue

            new_strike, expiry, put_data = find_closest_strike_simple(
                stock, current_price)
            if new_strike is not None and new_strike <= trade['strike']:
                trade['strike'] = new_strike

        # Calculate allocation per trade (minimum 20 positions)
        positions = max(20, num_trades)
        allocation_per_trade = capital / positions

        # Add allocation to each trade
        for trade in signals['options_trades']:
            raw_contracts = allocation_per_trade / (trade['strike'] * 100)
            contracts = int(raw_contracts)
            if contracts == 0:
                contracts = 1 if raw_contracts >= 0.67 else 0
            trade['contracts'] = contracts
            trade['allocation'] = contracts * trade['strike'] * 100

        # Filter out trades with 0 contracts
        signals['options_trades'] = [
            t for t in signals['options_trades'] if t['contracts'] > 0]

        return jsonify(signals)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    scheduler = None
    try:
        scheduler = init_scheduler()  # Capture the scheduler instance
        app.run(debug=True, port=5001, host='0.0.0.0')
    finally:
        if scheduler:  # Only shutdown if scheduler exists
            scheduler.shutdown()
