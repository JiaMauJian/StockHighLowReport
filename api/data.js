export default async function handler(req, res) {
  const url = process.env.TURSO_DATABASE_URL.replace('libsql://', 'https://');
  const token = process.env.TURSO_AUTH_TOKEN;

  const payload = {
    requests: [
      {
        type: 'execute',
        stmt: {
          sql: 'SELECT date, taiex_close, num_lows, num_highs, num_traded_stocks, low_ratio, high_ratio FROM high_low_60d ORDER BY date ASC',
        },
      },
      { type: 'close' },
    ],
  };

  const r = await fetch(`${url}/v2/pipeline`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!r.ok) {
    res.status(502).json({ error: 'Turso query failed', status: r.status });
    return;
  }

  const data = await r.json();
  const result = data.results[0].response.result;
  const cols = result.cols.map((c) => c.name);
  const rows = result.rows.map((row) => {
    const obj = {};
    cols.forEach((col, i) => {
      obj[col] = row[i].value ?? null;
    });
    return obj;
  });

  res.setHeader('Cache-Control', 's-maxage=3600, stale-while-revalidate=600');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.json(rows);
}
