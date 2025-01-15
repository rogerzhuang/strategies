from flask import Flask, jsonify
import requests
import yfinance as yf
import pandas as pd
from scipy.stats import zscore
from pandas_market_calendars import get_calendar
from datetime import datetime, timedelta
from itertools import cycle
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import time
import random
import logging
import config
import concurrent.futures
import json
import glob
import pytz
import os

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def get_proxies():
    proxy_url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{config.WEBSHARE_API_KEY}/-/any/sourceip/direct/-/"
    response = requests.get(proxy_url)
    if response.status_code == 200:
        proxies = [f"http://{line.strip()}" for line in response.text.split('\n') if line.strip()]
        return proxies
    else:
        raise ValueError(f"Failed to fetch proxies. Status code: {response.status_code}")

def get_last_trading_days(weeks=1, end_date=None):
    nyse = get_calendar('NYSE')
    eastern = pytz.timezone('US/Eastern')
    
    if end_date is None:
        end_date = datetime.now(eastern)
    elif isinstance(end_date, str):
        # Convert string date to eastern timezone
        end_date = eastern.localize(datetime.strptime(end_date, '%Y%m%d'))
    
    # Get a longer period to ensure we have enough weeks
    start_date = end_date - timedelta(weeks=weeks+1)
    
    # Get schedule including future dates of current week
    week_end = end_date + timedelta(days=(6 - end_date.weekday()))  # Next Sunday
    schedule = nyse.schedule(start_date=start_date, end_date=week_end)
    trading_days = schedule.index.strftime('%Y-%m-%d').tolist()
    
    # Group by week
    weekly_groups = {}
    for day in trading_days:
        date_obj = datetime.strptime(day, '%Y-%m-%d')
        week_key = date_obj.isocalendar()[0:2]  # (year, week)
        weekly_groups.setdefault(week_key, []).append(day)
    
    # Find the week of the end_date
    end_week_key = end_date.isocalendar()[0:2]
    end_date_str = end_date.strftime('%Y-%m-%d')
    
    # Get last trading day of each week
    weekly_last_days = []
    for week_key in sorted(weekly_groups.keys(), reverse=True):
        last_trading_day = max(weekly_groups[week_key])
        if week_key == end_week_key:
            # For current week, only include if end_date is >= last trading day
            if end_date_str >= last_trading_day:
                weekly_last_days.append(last_trading_day)
        elif week_key < end_week_key and len(weekly_last_days) < weeks:
            weekly_last_days.append(last_trading_day)
    
    return list(reversed(weekly_last_days))

def fetch_stock_data(args):
    ticker, proxy_pool = args
    logger.info(f"Starting to fetch data for {ticker}")
    for attempt in range(config.MAX_RETRIES):
        try:
            proxy = next(proxy_pool)
            logger.info(f"{ticker} - Using proxy: {proxy}")

            session = requests.Session()
            if proxy:
                session.proxies = {'http': proxy, 'https': proxy}

            stock = yf.Ticker(ticker, session=session)
            info = stock.info

            if info.get('marketCap', 0) > 0:
                try:
                    hist = stock.history(period="3mo")
                    avg_turnover = hist['Volume'].mean() * hist['Close'].mean() if not hist.empty else 0
                except Exception as hist_error:
                    logger.warning(f"HISTORY ERROR: {ticker} - {str(hist_error)}")
                    avg_turnover = 0

                market_cap = info['marketCap']
                logger.info(f"{ticker} - Successfully fetched data: MarketCap={market_cap}, AvgTurnover={avg_turnover}")
                return ticker, market_cap, avg_turnover
            else:
                if attempt < config.MAX_RETRIES - 1:
                    logger.warning(f"{ticker} - No market cap data on attempt {attempt + 1}, retrying...")
                    time.sleep(random.uniform(3, 6))
                    continue
                logger.warning(f"FAILED TICKER: {ticker} - No market cap data after {config.MAX_RETRIES} attempts")
                return ticker, 0, 0

        except Exception as e:
            if attempt < config.MAX_RETRIES - 1:
                logger.error(f"{ticker} - Error on attempt {attempt + 1}: {str(e)}, retrying...")
                time.sleep(random.uniform(3, 6))
                continue
            else:
                logger.error(f"FAILED TICKER: {ticker} - Error after {config.MAX_RETRIES} attempts: {str(e)}")
                return ticker, 0, 0

    return ticker, 0, 0

def calculate_scores_and_rank(data):
    df = pd.DataFrame(data, columns=['Ticker', 'MarketCap', 'AvgTurnover'])
    # Filter out entries with zero values
    df = df[(df['MarketCap'] > 0) & (df['AvgTurnover'] > 0)]
    
    if len(df) > 0:
        df['MarketCapZ'] = zscore(df['MarketCap'])
        df['AvgTurnoverZ'] = zscore(df['AvgTurnover'])
        df['FinalScore'] = (df['MarketCapZ'] + df['AvgTurnoverZ']) / 2
        return df.sort_values(by='FinalScore', ascending=False).head(800)['Ticker'].tolist()
    return []

def save_portfolio_to_json(portfolio_data):
    # Create signals directory if it doesn't exist
    os.makedirs('signals', exist_ok=True)
    
    # Use the date from portfolio_data instead of current time
    trading_date = portfolio_data['date']  # This is the actual trading date
    
    # Get current time in US Eastern for the timestamp part
    eastern = pytz.timezone('US/Eastern')
    current_time = datetime.now(eastern)
    
    # Format filename using trading date but current time for uniqueness
    filename = f"signals/live_signals_{trading_date.replace('-', '')}_{current_time.strftime('%H%M%S')}.json"
    
    # Save to file
    with open(filename, 'w') as f:
        json.dump(portfolio_data, f)
    
    logger.info(f"Saved portfolio to {filename}")
    return filename

def init_scheduler():
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler = BackgroundScheduler(daemon=True)
        eastern = pytz.timezone('US/Eastern')

        def scheduled_job():
            try:
                result = main()
                save_portfolio_to_json(result)
            except Exception as e:
                logger.error(f"Error in scheduled job: {e}")

        scheduler.add_job(
            scheduled_job,
            trigger=CronTrigger(
                day_of_week='sat',
                hour=0,
                minute=15,
                timezone=eastern
            ),
            name='portfolio_generation'
        )

        scheduler.start()
        return scheduler
    return None

def main():
    logger.info("Starting main function")
    try:
        # Set up proxy pool
        try:
            logger.info("Fetching proxies...")
            proxies = get_proxies()
            if not proxies:
                raise ValueError("No proxies fetched")
            logger.info(f"Successfully fetched {len(proxies)} proxies")
        except Exception as e:
            logger.error(f"Error setting up proxies: {e}")
            logger.info("Falling back to no proxy")
            proxies = [None]

        # Get universe from zacks_data service
        logger.info("Fetching universe from zacks_data service...")
        response = requests.get(f'{config.ZACKS_DATA_URL}/tickers')
        universe_data = response.json()
        tickers = [stock['symbol'] for stock in universe_data['stocks']]
        logger.info(f"Fetched {len(tickers)} tickers from universe")

        # Create tasks list with tickers and proxy pools
        tasks = []
        # Advance the cycle differently for each ticker
        for i, ticker in enumerate(tickers):
            # Create a new cycle starting from a different position for each ticker
            shifted_proxies = proxies[i % len(proxies):] + proxies[:i % len(proxies)]
            tasks.append((ticker, cycle(shifted_proxies)))

        # Process tickers concurrently
        logger.info(f"Processing {len(tickers)} tickers with {config.MAX_WORKERS} workers...")
        stock_data = []
        successful_downloads = 0
        failed_downloads = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_stock_data, task) for task in tasks]
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                if completed % 10 == 0:
                    logger.info(f"Progress: {completed}/{len(tickers)} tickers processed")
                result = future.result()
                if result and result[1] > 0 and result[2] > 0:
                    successful_downloads += 1
                    stock_data.append(result)
                else:
                    failed_downloads += 1

        logger.info(f"Download summary: {successful_downloads} successful, {failed_downloads} failed")

        # Calculate scores and get top tickers
        logger.info("Calculating scores and ranking stocks...")
        top_tickers = calculate_scores_and_rank(stock_data)
        logger.info(f"Found {len(top_tickers)} qualified stocks")

        # Get last trading day
        logger.info("Getting last trading day...")
        trading_days = get_last_trading_days(1)
        logger.info(f"Trading day: {trading_days[0]}")

        # Get portfolio for the date
        portfolios = {}
        date = trading_days[0]
        logger.info(f"Fetching portfolio for date: {date}")
        response = requests.post(
            f'{config.ZACKS_DATA_URL}/portfolio/{date}',
            json={'tickers': top_tickers}
        )
        portfolio = response.json()['portfolio']
        logger.info(f"Portfolio for {date}:")
        logger.info(f"Long positions ({len(portfolio['long'])} stocks): {', '.join(portfolio['long'])}")
        logger.info(f"Short positions ({len(portfolio['short'])} stocks): {', '.join(portfolio['short'])}")
        portfolios[date] = portfolio

        result = {
            'date': date,  # This is the actual trading date
            'portfolio': portfolios[date]
        }
        
        logger.info("Successfully completed portfolio generation")
        return result

    except Exception as e:
        logger.error(f"Error in main function: {str(e)}")
        raise

@app.route('/signals/<date>/<int:capital>')
def get_signals_with_allocation(date, capital):
    try:
        # Get last three trading days ending at the specified date
        trading_days = get_last_trading_days(weeks=3, end_date=date)
        
        # Find portfolio files for these dates
        portfolios = []
        for trade_date in trading_days:
            date_str = trade_date.replace('-', '')
            pattern = f'signals/live_signals_{date_str}_*.json'
            matching_files = glob.glob(pattern)
            
            if matching_files:
                latest_file = max(matching_files)  # Get the latest file for this date
                with open(latest_file, 'r') as f:
                    portfolio = json.load(f)
                    portfolios.append(portfolio)
        
        if not portfolios:
            return jsonify({
                "message": "Signals not ready for the requested date",
                "status": "pending"
            }), 404
            
        # Calculate aggregated positions
        position_weights = {}
        for portfolio in portfolios:
            portfolio_weight = 1.0 / len(portfolios)  # Equal weight for each portfolio
            
            # Calculate weights within the portfolio
            long_stocks = portfolio['portfolio']['long']
            short_stocks = portfolio['portfolio']['short']
            
            # Equal weight between long and short sides
            if long_stocks:  # Only process if there are long positions
                stock_weight = portfolio_weight * 0.5 / len(long_stocks)  # Half of portfolio weight divided by number of stocks
                for ticker in long_stocks:
                    position_weights[ticker] = position_weights.get(ticker, 0) + stock_weight
            
            if short_stocks:  # Only process if there are short positions
                stock_weight = portfolio_weight * 0.5 / len(short_stocks)  # Half of portfolio weight divided by number of stocks
                for ticker in short_stocks:
                    position_weights[ticker] = position_weights.get(ticker, 0) - stock_weight
        
        # Set up proxy pool
        try:
            logger.info("Fetching proxies for price data...")
            proxies = get_proxies()
            if not proxies:
                raise ValueError("No proxies fetched")
            logger.info(f"Successfully fetched {len(proxies)} proxies")
        except Exception as e:
            logger.error(f"Error setting up proxies: {e}")
            logger.info("Falling back to no proxy")
            proxies = [None]

        def fetch_price_data(args):
            ticker, weight, proxy_pool = args
            for attempt in range(config.MAX_RETRIES):
                try:
                    proxy = next(proxy_pool)
                    session = requests.Session()
                    if proxy:
                        session.proxies = {'http': proxy, 'https': proxy}
                        logger.info(f"Using proxy: {proxy} for {ticker}")

                    stock = yf.Ticker(ticker, session=session)
                    current_price = stock.fast_info['lastPrice']
                    
                    dollar_allocation = capital * weight
                    shares = int(dollar_allocation / current_price)
                    
                    if shares != 0:
                        return {
                            "ticker": ticker,
                            "shares": shares,
                            "price": current_price,
                            "weight": weight,
                            "allocation": shares * current_price
                        }
                    return None

                except Exception as e:
                    if attempt < config.MAX_RETRIES - 1:
                        logger.error(f"{ticker} - Error on attempt {attempt + 1}: {str(e)}, retrying...")
                        time.sleep(random.uniform(1, 3))
                        continue
                    logger.error(f"Failed to process {ticker} after {config.MAX_RETRIES} attempts: {str(e)}")
                    return None
            return None

        # Create tasks list with tickers and proxy pools
        tasks = []
        for i, (ticker, weight) in enumerate(position_weights.items()):
            # Create a new cycle starting from a different position for each ticker
            shifted_proxies = proxies[i % len(proxies):] + proxies[:i % len(proxies)]
            tasks.append((ticker, weight, cycle(shifted_proxies)))

        # Process tickers concurrently
        positions = []
        successful_fetches = 0
        failed_fetches = 0
        
        logger.info(f"Processing {len(tasks)} tickers with {config.MAX_WORKERS} workers...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_price_data, task) for task in tasks]
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                if completed % 10 == 0:
                    logger.info(f"Progress: {completed}/{len(tasks)} tickers processed")
                result = future.result()
                if result:
                    successful_fetches += 1
                    positions.append(result)
                else:
                    failed_fetches += 1

        logger.info(f"Price fetch summary: {successful_fetches} successful, {failed_fetches} failed")

        return jsonify({
            "trading_days": trading_days,
            "positions": positions,
            "total_positions": len(positions)
        })
        
    except Exception as e:
        logger.error(f"Error in get_signals_with_allocation: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    scheduler = None
    try:
        scheduler = init_scheduler()
        app.run(debug=True, host='0.0.0.0', port=5003)
    finally:
        if scheduler:
            scheduler.shutdown()
