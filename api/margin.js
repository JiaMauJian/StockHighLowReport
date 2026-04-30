export default async function handler(req, res) {
  const token = process.env.FINMIND_TOKEN;
  const start = new Date();
  start.setFullYear(start.getFullYear() - 10);
  const startDate = start.toISOString().slice(0, 10);

  const url = new URL('https://api.finmindtrade.com/api/v4/data');
  url.searchParams.set('dataset', 'TaiwanExchangeMarginMaintenance');
  url.searchParams.set('start_date', startDate);
  url.searchParams.set('token', token);

  const r = await fetch(url.toString());
  if (!r.ok) {
    res.status(502).json({ error: 'FinMind margin API failed', status: r.status });
    return;
  }

  const json = await r.json();
  const data = (json.data || []).map((row) => ({
    date:   row.date,
    margin: row.TotalExchangeMarginMaintenance,
  }));

  res.setHeader('Cache-Control', 's-maxage=3600, stale-while-revalidate=600');
  res.json(data);
}
