from flask import Flask, jsonify, request
import time
import random
import yfinance as yf
import requests
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor, as_completed
import config
import pandas as pd
import json
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone
import os
import threading

app = Flask(__name__)

def get_proxies():
    """Fetch proxies from webshare"""
    try:
        proxy_url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{config.WEBSHARE_API_KEY}/-/any/sourceip/direct/-/"
        response = requests.get(proxy_url)
        if response.status_code == 200:
            return [f"http://{line.strip()}" for line in response.text.split('\n') if line.strip()]
    except Exception as e:
        print(f"Error fetching proxies: {e}")
    return []

def is_us_listed(ticker, proxy_pool):
    """Check if ticker is listed on US exchanges using yfinance with rotating proxies"""
    for _ in range(config.MAX_RETRIES):
        try:
            # Get next proxy from the pool for each attempt
            proxy = next(proxy_pool)

            # Configure session with proxy
            if proxy:
                session = requests.Session()
                session.proxies = {'http': proxy, 'https': proxy}
                # Pass the session to yfinance
                stock = yf.Ticker(ticker, session=session)
            else:
                stock = yf.Ticker(ticker)

            info = stock.info
            exchange = info.get('exchange', '')
            return exchange in config.US_EXCHANGES
        except Exception as e:
            print(f"Error checking {ticker} with proxy {proxy}: {e}")
            time.sleep(random.uniform(1, 3))  # Random delay between retries
            continue
    return False

def scrape_companies():
    # Download CSV data from companiesmarketcap.com
    url = "https://companiesmarketcap.com/?download=csv"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to download CSV: HTTP {response.status_code}")
    
    # Use StringIO to create a file-like object from the response content
    from io import StringIO
    df = pd.read_csv(StringIO(response.text))
    
    # Take first 3000 companies
    df = df.head(3000)
    
    # Clean and format the data
    companies = []
    for _, row in df.iterrows():
        company = {
            'name': row['Name'],
            'symbol': row['Symbol'],
            'market_cap': float(row['marketcap']),
            'price': float(row['price (USD)']),
            'country': row['country']
        }
        companies.append(company)
    
    return companies

def filter_us_companies(companies):
    """Filter for US-listed companies using concurrent requests with proxies"""
    us_companies = []
    print("\nChecking for US listings...")

    # Get and set up proxy pool
    try:
        proxies = get_proxies()
        if not proxies:
            raise ValueError("No proxies fetched")
        proxy_pool = cycle(proxies)
    except Exception as e:
        print(f"Error setting up proxies: {e}")
        print("Continuing with limited concurrency...")
        proxy_pool = cycle([None])

    with ThreadPoolExecutor(max_workers=min(config.MARKETCAP_MAX_WORKERS, len(proxies) if proxies else 3)) as executor:
        # Pass proxy_pool instead of single proxy
        future_to_company = {
            executor.submit(is_us_listed, company['symbol'], proxy_pool): company
            for company in companies
        }

        # Process completed futures
        for future in as_completed(future_to_company):
            company = future_to_company[future]
            try:
                is_us = future.result()
                if is_us:
                    us_companies.append(company)
                    print(f"Added {company['symbol']} (US-listed)")
                else:
                    print(f"Skipped {company['symbol']} (not US-listed)")
            except Exception as e:
                print(f"Error checking {company['symbol']}: {e}")

    return us_companies

def update_market_data():
    """Background job to update market data"""
    try:
        print("Starting market data update...")
        # Get fresh data directly without caching
        all_companies = scrape_companies()
        us_companies = filter_us_companies(all_companies)
        
        # Save to data directory (mounted volume)
        os.makedirs('data', exist_ok=True)  # Ensure directory exists
        with open('data/marketcaps.json', 'w') as f:
            json.dump(us_companies, f)
            
        print(f"Market data updated successfully. Found {len(us_companies)} US companies.")
    except Exception as e:
        print(f"Error updating market data: {e}")

def run_startup_jobs():
    print("Running startup jobs in background...")
    def run_jobs():
        update_market_data()
        print("Market data update completed")
    
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
        eastern = timezone('US/Eastern')

        scheduler.add_job(
            update_market_data,
            trigger='cron',
            day_of_week='fri',
            hour=16,
            minute=15,
            timezone=eastern,
            name='market_data_job'
        )

        scheduler.start()
        return scheduler
    return None

@app.route('/stock', methods=['GET'])
def get_largest_stocks():
    try:
        # Get the parameters
        n = request.args.get('n', type=int)  # Now optional
        min_cap = request.args.get('min_cap', default=0, type=float)  # in billions

        if n is not None and n <= 0:  # Only check if n is provided
            return jsonify({'error': 'Parameter n must be positive'}), 400
        if min_cap < 0:
            return jsonify({'error': 'Parameter min_cap must be non-negative'}), 400

        # Load cached data from data directory
        try:
            with open('data/marketcaps.json', 'r') as f:
                us_companies = json.load(f)
        except FileNotFoundError:
            return jsonify({'error': 'Market data not yet available'}), 503

        # Filter by minimum market cap (convert min_cap to same unit as stored data)
        if min_cap > 0:
            us_companies = [company for company in us_companies 
                          if company['market_cap'] >= (min_cap * 1e9)]  # convert billions to actual value

        # Sort by market cap
        sorted_companies = sorted(us_companies, key=lambda x: x['market_cap'], reverse=True)
        
        # Apply limit only if n is provided
        if n is not None:
            sorted_companies = sorted_companies[:n]

        return jsonify({
            'count': len(sorted_companies),
            'stocks': sorted_companies
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    scheduler = None
    try:
        scheduler = init_scheduler()  # Capture the scheduler instance
        app.run(debug=True, port=5050, host='0.0.0.0')
    finally:
        if scheduler:  # Only shutdown if scheduler exists
            scheduler.shutdown()