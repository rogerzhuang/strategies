import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pandas_market_calendars as mcal
from py_vollib.black_scholes import implied_volatility
import concurrent.futures
import time
import json
import os
import requests
from itertools import cycle
import config
import random
import pytz


def get_next_weekly_expiry():
    nyse = mcal.get_calendar('NYSE')
    today = datetime.today()
    next_week = today + timedelta(days=7)
    end_of_next_week = next_week + timedelta(days=(4 - next_week.weekday()))
    schedule = nyse.schedule(start_date=today, end_date=end_of_next_week)
    return schedule.index[-1].strftime('%Y-%m-%d') if not schedule.empty else None


def find_closest_strike(stock, current_price):
    """Find the closest valid put strike to 90% of current price."""
    expiry = get_next_weekly_expiry()
    if not expiry:
        return None, None, None

    try:
        options = stock.option_chain(expiry)
        if options is None or not hasattr(options, 'puts') or options.puts.empty:
            return None, None, None

        # Find closest put strike within 88-92% range
        lower_bound = current_price * 0.88
        upper_bound = current_price * 0.92
        target_strike = current_price * 0.9

        valid_puts = options.puts[
            (options.puts['strike'] >= lower_bound) &
            (options.puts['strike'] <= upper_bound)
        ]

        if valid_puts.empty:
            return None, None, None

        # Filter out invalid bid/ask prices
        valid_puts = valid_puts[
            (valid_puts['bid'] > 0.01) &
            (valid_puts['ask'] > 0.01) &
            (~pd.isna(valid_puts['bid'])) &
            (~pd.isna(valid_puts['ask'])) &
            (valid_puts['ask'] > valid_puts['bid']) &
            ((valid_puts['ask'] - valid_puts['bid']) / valid_puts['bid'] <= 1)
        ]

        if valid_puts.empty:
            return None, None, None

        # Find the closest strike to target_strike
        valid_puts['distance'] = abs(valid_puts['strike'] - target_strike)
        closest_put = valid_puts.loc[valid_puts['distance'].idxmin()]

        return closest_put['strike'], expiry, closest_put

    except (requests.RequestException, requests.exceptions.ProxyError,
            requests.exceptions.ConnectTimeout, ConnectionError):
        # Re-raise network and proxy-related errors for retry
        raise
    except Exception as e:
        # Return None for all other errors (data-related)
        print(f"Error finding closest strike: {str(e)}")
        return None, None, None

def find_closest_strike_simple(stock, current_price):
    """Find the closest valid put strike to 90% of current price, without bid/ask validation."""
    expiry = get_next_weekly_expiry()
    if not expiry:
        return None, None, None

    try:
        options = stock.option_chain(expiry)
        if options is None or not hasattr(options, 'puts') or options.puts.empty:
            return None, None, None

        # Find closest put strike within 88-92% range
        lower_bound = current_price * 0.88
        upper_bound = current_price * 0.92
        target_strike = current_price * 0.9

        valid_puts = options.puts[
            (options.puts['strike'] >= lower_bound) &
            (options.puts['strike'] <= upper_bound)
        ]

        if valid_puts.empty:
            return None, None, None

        # Find the closest strike to target_strike
        valid_puts['distance'] = abs(valid_puts['strike'] - target_strike)
        closest_put = valid_puts.loc[valid_puts['distance'].idxmin()]

        return closest_put['strike'], expiry, closest_put

    except Exception as e:
        print(f"Error finding closest strike: {str(e)}")
        return None, None, None

def get_proxies():
    """Fetch proxies from webshare"""
    proxy_url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{config.WEBSHARE_API_KEY}/-/any/sourceip/direct/-/"
    response = requests.get(proxy_url)
    if response.status_code == 200:
        proxies = [
            f"http://{line.strip()}" for line in response.text.split('\n') if line.strip()]
        return proxies
    else:
        print(f"Failed to fetch proxies. Status code: {response.status_code}")
        return None


def get_stock_and_option_data(args):
    ticker, proxy_pool = args

    # Add retry logic
    for attempt in range(config.MAX_RETRIES):
        try:
            # Get a new proxy for each attempt
            proxy = next(proxy_pool)

            # Configure session with proxy
            session = requests.Session()
            if proxy:
                session.proxies = {'http': proxy, 'https': proxy}

            stock = yf.Ticker(ticker, session=session)
            hist_data = stock.history(period='3mo')

            if hist_data.empty:
                if attempt < config.MAX_RETRIES - 1:  # If not the last attempt
                    print(
                        f"{ticker} - No data on attempt {attempt + 1}, retrying...")
                    # Random delay between retries
                    time.sleep(random.uniform(1, 3))
                    continue
                else:
                    print(
                        f"{ticker} - No historical data available after {config.MAX_RETRIES} attempts")
                    return None

            current_price = hist_data['Close'].iloc[-1]
            if pd.isna(current_price) or current_price <= 0:
                if attempt < config.MAX_RETRIES - 1:
                    print(
                        f"{ticker} - Invalid price on attempt {attempt + 1}, retrying...")
                    time.sleep(random.uniform(1, 3))
                    continue
                else:
                    print(
                        f"{ticker} - Invalid price data after {config.MAX_RETRIES} attempts")
                    return None

            two_month_return = (
                current_price / hist_data['Close'].iloc[-min(42, len(hist_data))] - 1)
            strike, expiry, put_data = find_closest_strike(
                stock, current_price)

            if strike is None:
                return None

            # Calculate option mid price
            option_price = (put_data['bid'] + put_data['ask']) / 2

            # Calculate IV
            days_to_expiry = (datetime.strptime(
                expiry, '%Y-%m-%d') - datetime.now()).days
            if days_to_expiry <= 0:
                return None

            iv = implied_volatility.implied_volatility(
                option_price,
                current_price,
                strike,
                days_to_expiry / 365,
                0.0,  # risk-free rate
                'p'   # put option flag
            )

            if iv < 0.1 or iv > 5.0:
                print(f"{ticker} - IV outside valid range: {iv:.1%}")
                return None

            print(f"{ticker} - Stock: ${current_price:.2f}, Put Strike: ${strike}, "
                  f"Bid: ${put_data['bid']:.2f}, Ask: ${put_data['ask']:.2f}, Mid: ${option_price:.2f}, "
                  f"IV: {iv:.1%}, 2M Return: {two_month_return:.1%}")

            return {
                'ticker': ticker,
                'current_price': current_price,
                'two_month_return': two_month_return,
                'strike': strike,
                'premium': option_price / current_price,
                'iv': iv,
                'expiry': expiry
            }

        except Exception as e:
            if attempt < config.MAX_RETRIES - 1:
                print(
                    f"{ticker} - Error on attempt {attempt + 1}: {str(e)}, retrying...")
                time.sleep(random.uniform(1, 3))
                continue
            else:
                print(
                    f"{ticker} - Error after {config.MAX_RETRIES} attempts: {str(e)}")
                return None

    return None


def generate_live_signals():
    try:
        with open('ticker_list.csv', 'r') as f:
            TICKERS = [line.strip() for line in f if line.strip()]
    except Exception as e:
        print(f"Error reading ticker list: {str(e)}")
        return {"options_trades": []}

    print(
        f"\nProcessing {len(TICKERS)} tickers for {get_next_weekly_expiry()} expiry...")

    # Get and set up proxy pool
    try:
        proxies = get_proxies()
        if not proxies:
            print("No proxies available, continuing without proxies")
            proxies = [None]
    except Exception as e:
        print(f"Error setting up proxies: {e}")
        proxies = [None]

    proxy_pool = cycle(proxies)
    ticker_proxy_pairs = [(ticker, proxy_pool) for ticker in TICKERS]

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.LIVE_SIGNALS_MAX_WORKERS) as executor:
        results = list(executor.map(
            get_stock_and_option_data, ticker_proxy_pairs))

    valid_results = [r for r in results if r is not None]
    print(f"\nFound {len(valid_results)} tickers with valid puts")

    filtered_results = [
        r for r in valid_results
        if r['iv'] > 0.6 and r['two_month_return'] < 0.2
    ]
    print(
        f"Found {len(filtered_results)} tickers meeting IV and return criteria")

    filtered_results.sort(key=lambda x: x['iv'], reverse=True)

    options_trades = []
    for result in filtered_results:
        options_trades.append({
            "action": "SELL",
            "contract": f"{result['ticker']} PUT",
            "expiry": result['expiry'],
            "strike": result['strike'],
            "premium": result['premium'],
            "iv": round(result['iv'], 3)
        })

    return {"options_trades": options_trades}


def main():
    signals = generate_live_signals()

    # Create signals directory if it doesn't exist
    if not os.path.exists('signals'):
        os.makedirs('signals')

    # Generate filename with US Eastern timestamp
    eastern = pytz.timezone('US/Eastern')
    timestamp = datetime.now(eastern).strftime('%Y%m%d_%H%M%S')
    filename = f'signals/live_signals_{timestamp}.json'

    # Save signals to JSON file
    with open(filename, 'w') as f:
        json.dump(signals, f, indent=2)


if __name__ == "__main__":
    main()
