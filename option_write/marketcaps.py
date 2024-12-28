import time
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
import requests
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor, as_completed
import config


def get_market_cap_value(market_cap_str):
    """Convert market cap string to float value in billions"""
    try:
        # Print raw input for debugging
        print(f"Converting market cap: '{market_cap_str}'")

        # Remove '$' and split by space
        parts = market_cap_str.replace('$', '').strip().split(' ')
        print(f"Parts after split: {parts}")

        if len(parts) != 2:
            print(f"Unexpected format - parts: {parts}")
            return 0

        value = float(parts[0])
        unit = parts[1]  # Should be 'B' or 'T'

        # Convert to billions based on unit
        if unit == 'T':
            value *= 1000  # Convert trillions to billions
        elif unit == 'B':
            value = value  # Already in billions
        elif unit == 'M':
            value /= 1000  # Convert millions to billions
        else:
            print(f"Unknown unit: {unit}")
            return 0
        print(f"Converted to {value} billion")
        return value
    except Exception as e:
        print(f"Error converting market cap '{market_cap_str}': {e}")
        return 0


def get_proxies():
    """Fetch proxies from webshare"""
    proxy_url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{config.WEBSHARE_API_KEY}/-/any/sourceip/direct/-/"
    response = requests.get(proxy_url)
    if response.status_code == 200:
        proxies = [
            f"http://{line.strip()}" for line in response.text.split('\n') if line.strip()]
        return proxies
    else:
        raise ValueError(
            f"Failed to fetch proxies. Status code: {response.status_code}")


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
    companies_data = []
    page = 1
    min_market_cap_found = False
    retry_count = 0

    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-webgl')
    options.add_argument('--disable-webgl2')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    # Add these new options to avoid detection
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument(
        f'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')

    driver = webdriver.Chrome(options=options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    try:
        while not min_market_cap_found:
            print(f"\nProcessing page {page}...")
            url = f'https://companiesmarketcap.com/page/{page}/'

            try:
                # Reset page state on each attempt
                driver.delete_all_cookies()
                driver.get(url)
                time.sleep(random.uniform(1, 3))

                wait = WebDriverWait(driver, config.TIMEOUT)
                table = wait.until(EC.presence_of_element_located(
                    (By.CLASS_NAME, 'marketcap-table')))

                # Verify table has content
                rows = wait.until(EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, '.marketcap-table tbody tr')))

                if not rows:
                    raise Exception("Table is empty")

                driver.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight/2);")
                time.sleep(random.uniform(0.5, 1))

                # Process page content
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                table = soup.find('table', class_='marketcap-table')

                if not table or not table.find('tbody'):
                    raise Exception("Invalid table structure")

                rows = table.find('tbody').find_all('tr')
                print(f"Found {len(rows)} rows")

                if len(rows) < 2:  # Assuming we should have at least a few rows
                    raise Exception("Too few rows found")

                # Process rows...
                for row in rows:
                    # Skip advertisement rows
                    if 'ad-tr' in row.get('class', []):
                        continue

                    cells = row.find_all('td')
                    if len(cells) < 8:
                        continue

                    try:
                        rank = cells[1].text.strip()
                        name_div = cells[2].find('div', class_='name-div')
                        name = name_div.find(
                            'div', class_='company-name').text.strip()
                        ticker = name_div.find(
                            'div', class_='company-code').text.strip()
                        market_cap = cells[3].text.strip()
                        price = cells[4].text.strip()
                        change = cells[5].text.strip()
                        country = cells[7].find(
                            'span', class_='responsive-hidden').text.strip()

                        print(f"\nProcessing {name} ({ticker})")
                        market_cap_value = get_market_cap_value(market_cap)

                        # Check if market cap is less than $10B
                        if market_cap_value < config.MIN_MARKET_CAP_BILLIONS:
                            print(
                                f"Reached market cap < ${config.MIN_MARKET_CAP_BILLIONS}B at rank {rank}")
                            min_market_cap_found = True
                            break

                        companies_data.append({
                            'Rank': rank,
                            'Name': name,
                            'Ticker': ticker,
                            'Market Cap': market_cap,
                            'Price': price,
                            'Change': change,
                            'Country': country
                        })
                        print(
                            f"Added to dataset (market cap: {market_cap_value}B)")

                    except Exception as e:
                        print(f"Error processing row: {e}")
                        continue

                print(f"Completed page {page}")
                page += 1
                retry_count = 0  # Reset retry count on successful page
                time.sleep(random.uniform(1, 2))

            except Exception as e:
                retry_count += 1
                print(
                    f"Error on page {page} (attempt {retry_count}/{config.MAX_RETRIES}): {str(e)}")

                if retry_count >= config.MAX_RETRIES:
                    print(
                        f"Failed to process page {page} after {config.MAX_RETRIES} attempts")
                    break

                # Exponential backoff for retries
                sleep_time = min(random.uniform(2, 4) *
                                 (2 ** (retry_count - 1)), 30)
                print(f"Waiting {sleep_time:.1f} seconds before retry...")
                time.sleep(sleep_time)

                # Try refreshing the driver state
                try:
                    driver.quit()
                    driver = webdriver.Chrome(options=options)
                    driver.execute_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                except Exception as driver_error:
                    print(f"Error refreshing driver: {str(driver_error)}")
                continue

    finally:
        try:
            driver.quit()
        except:
            pass

    return companies_data


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
            executor.submit(is_us_listed, company['Ticker'], proxy_pool): company
            for company in companies
        }

        # Process completed futures
        for future in as_completed(future_to_company):
            company = future_to_company[future]
            try:
                is_us = future.result()
                if is_us:
                    us_companies.append(company)
                    print(f"Added {company['Ticker']} (US-listed)")
                else:
                    print(f"Skipped {company['Ticker']} (not US-listed)")
            except Exception as e:
                print(f"Error checking {company['Ticker']}: {e}")

    return us_companies


def main():
    # Scrape all companies above $10B market cap
    companies = scrape_companies()
    print(f"\nFound {len(companies)} companies above $10B market cap")

    # Filter for US-listed companies
    us_companies = filter_us_companies(companies)

    # Create DataFrame and save to CSV with proper quoting
    df = pd.DataFrame(us_companies)
    # Use csv.QUOTE_ALL (1) to quote all fields
    df.to_csv('marketcaps.csv', index=False, quoting=1)
    print(f"\nSaved {len(us_companies)} US-listed companies to marketcaps.csv")


if __name__ == "__main__":
    main()
