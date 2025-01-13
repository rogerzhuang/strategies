import api from "zacks-api";
import { getPool } from './db-setup.js';
import fs from 'fs/promises';

async function getOrCreateStockId(pool, ticker, name) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    let res = await client.query("SELECT id FROM stocks WHERE ticker = $1", [ticker]);
    if (res.rows.length > 0) {
      await client.query("COMMIT");
      return res.rows[0].id;
    } else {
      res = await client.query(
        "INSERT INTO stocks (ticker, name) VALUES ($1, $2) ON CONFLICT (ticker) DO UPDATE SET name = $2 RETURNING id",
        [ticker, name]
      );
      await client.query("COMMIT");
      return res.rows[0].id;
    }
  } catch (error) {
    await client.query("ROLLBACK");
    console.error(`Error in getOrCreateStockId for ticker ${ticker}:`, error);
    throw error;
  } finally {
    client.release();
  }
}

async function saveZacksRanking(pool, stockId, data, stats) {
  // Validate data before database operations
  if (!data.zacksRankText || data.zacksRank == null || data.updatedAt == null || isNaN(data.zacksRank)) {
    console.warn(`Invalid data for stock ID ${stockId}, skipping save.`);
    stats.noRankingStocks++;
    return;
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await client.query(
      `INSERT INTO zacks_rankings (stock_id, zacksRankText, zacksRank, updatedAt) 
       VALUES ($1, $2, $3, $4) 
       ON CONFLICT (stock_id, updatedAt) DO UPDATE 
       SET zacksRankText = EXCLUDED.zacksRankText, zacksRank = EXCLUDED.zacksRank
       RETURNING (xmax = 0) AS inserted;`,
      [stockId, data.zacksRankText, data.zacksRank, data.updatedAt]
    );
    await client.query("COMMIT");
    
    const isInserted = result.rows[0].inserted;
    if (isInserted) {
      console.log(`Inserted new ranking for stock ID ${stockId} on ${data.updatedAt}`);
      stats.totalInserts++;
    } else {
      console.log(`Updated existing ranking for stock ID ${stockId} on ${data.updatedAt}`);
      stats.totalUpdates++;
    }
    stats.successfulSaves++;
  } catch (error) {
    await client.query("ROLLBACK");
    console.error(`Error saving ranking for stock ID ${stockId}:`, error);
    stats.failedSaves++;
    stats.noRankingStocks++;
    throw error;
  } finally {
    client.release();
  }
}

export async function processZacksRankings(stocksFile) {
  const pool = getPool();
  const stats = {
    totalProcessed: 0,
    successfulSaves: 0,
    totalInserts: 0,
    totalUpdates: 0,
    failedRetrievals: 0,
    failedSaves: 0,
    failedProcessing: 0,
    noRankingStocks: 0
  };

  async function getDataWithRetry(ticker, maxRetries = 3, delay = 1000) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const data = await api.getData(ticker);
        
        if (data === null) {
          console.log(`No Zacks ranking available for ${ticker}`);
          stats.noRankingStocks++;
          return null;
        }

        if (typeof data !== "object") {
          throw new Error("Invalid data received from API");
        }

        if (data.zacksRank == null || data.zacksRankText == null) {
          console.warn(`Incomplete data received for ${ticker}, skipping.`);
          stats.noRankingStocks++;
          return null;
        }

        // Ensure valid date
        data.updatedAt = new Date(data.updatedAt || new Date());
        if (isNaN(data.updatedAt.getTime())) {
          data.updatedAt = new Date();
        }

        return data;
      } catch (error) {
        console.error(`Attempt ${attempt} failed for ${ticker}:`, error);
        if (attempt < maxRetries) {
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }
        stats.failedRetrievals++;
        stats.noRankingStocks++;
        return null;
      }
    }
    return null;
  }

  try {
    const stocksData = JSON.parse(
      await fs.readFile(stocksFile, 'utf8')
    );
    
    const pendingPromises = [];
    
    for (const stock of stocksData) {
      const promise = (async () => {
        try {
          stats.totalProcessed++;
          const data = await getDataWithRetry(stock.symbol);
          if (data) {
            const stockId = await getOrCreateStockId(pool, stock.symbol, stock.name);
            await saveZacksRanking(pool, stockId, data, stats);
          }
        } catch (error) {
          console.error(`Error processing ${stock.symbol}:`, error);
          stats.failedProcessing++;
          stats.noRankingStocks++;
        }
      })();
      pendingPromises.push(promise);
    }

    await Promise.all(pendingPromises);
    
    console.log("Processing completed:");
    console.log(`Total processed: ${stats.totalProcessed}`);
    console.log(`Successful saves: ${stats.successfulSaves}`);
    console.log(`Total inserts: ${stats.totalInserts}`);
    console.log(`Total updates: ${stats.totalUpdates}`);
    console.log(`Failed retrievals: ${stats.failedRetrievals}`);
    console.log(`Failed saves: ${stats.failedSaves}`);
    console.log(`Failed processing: ${stats.failedProcessing}`);
    console.log(`No rankings: ${stats.noRankingStocks}`);

  } finally {
    await pool.end();
    console.log("Database connections closed.");
  }
} 