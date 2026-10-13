#!/usr/bin/env node
// Pull businesses that signed up for AgentGrow Bookings and add each one as a lead in Friday's CRM, once.
// Runs inside Friday's pod, where `pg` and CRM_DATABASE_URL already exist.
//   BOOKINGS_API_URL=https://bookings.agentgrow.io BOOKINGS_API_KEY=... node bookings-lead-sync.js [--dry-run]
// Idempotent: a business already in the CRM (metadata.bookings_business_id) is skipped.
const { Client } = require("pg");

async function main() {
  const dryRun = process.argv.includes("--dry-run");
  const base = process.env.BOOKINGS_API_URL || "https://bookings.agentgrow.io";
  const res = await fetch(`${base}/api/v1/businesses`, { headers: { Authorization: `Bearer ${process.env.BOOKINGS_API_KEY}` } });
  if (!res.ok) throw new Error(`bookings API ${res.status}`);
  const { businesses } = await res.json();

  const db = new Client({ connectionString: process.env.CRM_DATABASE_URL });
  await db.connect();
  let added = 0;
  try {
    for (const b of businesses) {
      const seen = await db.query("SELECT id FROM leads WHERE metadata->>'bookings_business_id' = $1", [String(b.id)]);
      if (seen.rows.length) continue;
      const row = [b.name, `+${b.owner_wa}`, b.name, JSON.stringify({ bookings_business_id: String(b.id), bookings_created_at: b.created_at, services: b.services })];
      if (dryRun) { console.log("would add", row); added++; continue; }
      const r = await db.query(
        `INSERT INTO leads (name, phone, company, source, status, stage, currency, metadata, notes)
         VALUES ($1, $2, $3, 'agentgrow_bookings', 'new', 'new', 'INR', $4::jsonb,
                 'Set up AgentGrow Bookings (WhatsApp booking agent). Follow up on WhatsApp.') RETURNING id`, row);
      console.log(`added lead ${r.rows[0].id} for business ${b.id} (${b.name})`);
      added++;
    }
  } finally {
    await db.end();
  }
  console.log(`${dryRun ? "dry run: " : ""}${added} new of ${businesses.length} businesses`);
}

main().catch((e) => { console.error(e.message); process.exit(1); });
