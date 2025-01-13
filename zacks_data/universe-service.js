import axios from 'axios';
import fs from 'fs/promises';
import path from 'path';
import moment from 'moment-timezone';

const DATA_DIR = 'data';

export async function fetchAndSaveStocks() {
  try {
    const response = await axios.get('http://universe:5050/stock?n=1200');
    const stocks = response.data.stocks;
    
    // Generate timestamp for filename in US Eastern Time
    const timestamp = moment().tz('America/New_York').format('YYYYMMDD_HHmmss');
    const filepath = path.join(DATA_DIR, `stocks_${timestamp}.json`);
    
    // Save full JSON response
    await fs.writeFile(filepath, JSON.stringify(stocks, null, 2));
    console.log(`Updated ${filepath} with ${stocks.length} stocks`);
    
    return filepath;
  } catch (error) {
    console.error('Error fetching/saving stocks:', error);
    throw error;
  }
}

export async function getLatestStocksFile(date = null) {
  try {
    const files = await fs.readdir(DATA_DIR);
    let stockFiles = files.filter(f => f.startsWith('stocks_') && f.endsWith('.json'));
    
    if (date) {
      // Filter files for specific date (YYYYMMDD)
      stockFiles = stockFiles.filter(f => f.includes(date));
    }
    
    if (stockFiles.length === 0) {
      throw new Error('Service not ready - waiting for first data sync');
    }
    
    // Sort in descending order and get the latest file
    return stockFiles.sort().reverse()[0];
  } catch (error) {
    console.error('Error getting latest stocks file:', error);
    throw error;
  }
} 