import yfinance as yf
import pandas as pd
from datetime import datetime
import requests
from itertools import cycle
import time
import logging
import concurrent.futures
import random
import config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_proxies():
    proxy_url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{config.WEBSHARE_API_KEY}/-/any/sourceip/direct/-/"
    response = requests.get(proxy_url)
    if response.status_code == 200:
        proxies = [
            f"http://{line.strip()}" for line in response.text.split('\n') if line.strip()]
        return proxies
    else:
        raise ValueError(
            f"Failed to fetch proxies. Status code: {response.status_code}")


def has_weekly_options(args):
    ticker, proxy_pool = args

    for attempt in range(config.MAX_RETRIES):
        try:
            # Get a new proxy for each attempt
            proxy = next(proxy_pool)

            # Configure session with proxy
            session = requests.Session()
            if proxy:
                session.proxies = {'http': proxy, 'https': proxy}

            stock = yf.Ticker(ticker, session=session)
            expirations = stock.options

            if len(expirations) < 3:
                if attempt < config.MAX_RETRIES - 1:
                    logger.info(
                        f"{ticker} - No options data on attempt {attempt + 1}, retrying...")
                    time.sleep(random.uniform(1, 3))
                    continue
                return None

            # Convert expiration strings to datetime objects
            exp_dates = [datetime.strptime(exp, '%Y-%m-%d')
                         for exp in expirations[:3]]

            # Calculate differences in days
            diff1 = (exp_dates[1] - exp_dates[0]).days
            diff2 = (exp_dates[2] - exp_dates[1]).days

            if diff1 <= 14 and diff2 <= 14:
                logger.info(f"{ticker} has weekly options")
                return ticker
            return None

        except Exception as e:
            if attempt < config.MAX_RETRIES - 1:
                logger.error(
                    f"{ticker} - Error on attempt {attempt + 1}: {str(e)}, retrying...")
                time.sleep(random.uniform(1, 3))
                continue
            else:
                logger.error(
                    f"{ticker} - Error after {config.MAX_RETRIES} attempts: {str(e)}")
                return None

    return None


def main():
    # Read market caps CSV with proper header parsing
    df = pd.read_csv('marketcaps.csv')
    tickers = df['Ticker'].tolist()
    logger.info(f"Processing {len(tickers)} tickers...")

    # Set up proxy pool
    try:
        proxies = get_proxies()
        if not proxies:
            raise ValueError("No proxies fetched")
        proxy_pool = cycle(proxies)
    except Exception as e:
        logger.error(f"Error setting up proxies: {e}")
        proxy_pool = cycle([None])

    # Create list of (ticker, proxy_pool) tuples for processing
    tasks = [(ticker, proxy_pool) for ticker in tickers]
    weekly_tickers = []

    # Process tickers concurrently
    completed = 0
    total = len(tasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.WEEKLIES_MAX_WORKERS) as executor:
        futures = [executor.submit(has_weekly_options, task) for task in tasks]

        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if completed % 10 == 0:  # Log progress every 10 tickers
                logger.info(f"Progress: {completed}/{total} tickers processed")

            result = future.result()
            if result:
                weekly_tickers.append(result)

    # Save results to CSV
    with open('ticker_list.csv', 'w') as f:
        for ticker in weekly_tickers:
            f.write(f"{ticker}\n")

    logger.info(f"Found {len(weekly_tickers)} tickers with weekly options")


if __name__ == "__main__":
    main()
