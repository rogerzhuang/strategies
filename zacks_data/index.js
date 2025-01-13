// index.js
import express from 'express';
import schedule from 'node-schedule';
import fs from 'fs/promises';
import moment from 'moment-timezone';
import { fetchAndSaveStocks, getLatestStocksFile } from './universe-service.js';
import { processZacksRankings } from './zacks-service.js';
import { setup, getPool } from './db-setup.js';
import path from 'path';
import { mkdir} from 'fs/promises';

const app = express();
const port = process.env.PORT || 5051;
const DATA_DIR = 'data';

// Add this line to parse JSON
app.use(express.json());

// Initialize database before starting server
async function initializeApp() {
  try {
    // Ensure data directory exists
    await mkdir(DATA_DIR, { recursive: true });
    console.log('Data directory ready');

    await setup();
    console.log('Database setup completed');
    
    // Schedule job for 12:10 AM Eastern Time every Saturday
    const rule = new schedule.RecurrenceRule();
    rule.dayOfWeek = 6;  // Saturday
    rule.hour = 0;
    rule.minute = 10;
    rule.tz = 'America/New_York';

    schedule.scheduleJob(rule, async () => {
      const now = moment().tz('America/New_York');
      console.log('Starting scheduled job at:', now.format('YYYY-MM-DD HH:mm:ss z'));
      try {
        const stocksFile = await fetchAndSaveStocks();
        await processZacksRankings(stocksFile);
      } catch (error) {
        console.error('Error in scheduled job:', error);
      }
    });

    // Start server
    app.listen(port, () => {
      console.log(`Server running on port ${port}`);
    });
  } catch (error) {
    console.error('Failed to initialize application:', error);
    process.exit(1);
  }
}

// API endpoint
app.get('/tickers/:date?', async (req, res) => {
  try {
    const { date } = req.params;
    const latestFile = await getLatestStocksFile(date);
    const stocksData = JSON.parse(
      await fs.readFile(path.join(DATA_DIR, latestFile), 'utf8')
    );
    
    res.json({
      date: latestFile.split('_')[1], // Extract date from filename
      stocks: stocksData,
      filename: latestFile
    });
  } catch (error) {
    console.error('Error in /tickers endpoint:', error);
    res.status(500).json({ error: error.message });
  }
});

app.post('/portfolio/:date', async (req, res) => {
  try {
    const { date } = req.params;
    const { tickers } = req.body; // Accept tickers from request body
    const endDate = moment(date).add(1, 'days').format('YYYY-MM-DD');
    
    const pool = getPool();
    const query = `
      SELECT DISTINCT s.ticker, z.zacksrank
      FROM stocks s
      JOIN zacks_rankings z ON s.id = z.stock_id
      WHERE z.zacksrank IN (1, 5)
      AND z.updatedat >= $1
      AND z.updatedat < $2
      AND s.ticker = ANY($3)
      ORDER BY zacksrank, ticker;
    `;
    
    // Use provided tickers instead of getting from file
    const result = await pool.query(query, [date, endDate, tickers]);
    
    const portfolio = {
      long: result.rows.filter(row => row.zacksrank === 1).map(row => row.ticker),
      short: result.rows.filter(row => row.zacksrank === 5).map(row => row.ticker)
    };
    
    res.json({
      date,
      portfolio
    });
    
  } catch (error) {
    console.error('Error in /portfolio endpoint:', error);
    res.status(500).json({ error: error.message });
  }
});

// Start the application
initializeApp();
