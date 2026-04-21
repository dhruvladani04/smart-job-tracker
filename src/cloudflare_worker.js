/**
 * Cloudflare Worker API for Job Tracker Dashboard
 * Replaces FastAPI backend with a Workers + D1 based API
 *
 * Endpoints:
 *   GET  /api/stats
 *   GET  /api/jobs
 *   GET  /api/jobs/:id
 *   POST /api/jobs/:id/status
 *   POST /api/jobs/:id/feedback
 *   POST /api/jobs/bulk
 *   DELETE /api/jobs/:id
 *   GET  /api/searches
 *   GET  /api/companies
 *   POST /api/sync  (upsert jobs from JSON)
 */

const VALID_STATUSES = ['new', 'reviewed', 'applied', 'skipped', 'rejected', 'interview', 'offer', 'archived'];
const VALID_VERDICTS = ['apply', 'skip', 'save', 'unsure'];

// In-memory rate limiting (per-worker-instance, resets on restart)
// For production, use Cloudflare KV for distributed rate limiting
const rateLimitMap = new Map();

function isRateLimited(ip) {
  const now = Date.now();
  const window = 60 * 1000; // 1 minute window
  const limit = 100; // requests per window

  const record = rateLimitMap.get(ip) || { count: 0, windowStart: now };

  if (now - record.windowStart > window) {
    record.count = 0;
    record.windowStart = now;
  }

  record.count++;
  rateLimitMap.set(ip, record);

  return record.count > limit;
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}

function errorResponse(message, status = 400) {
  return jsonResponse({ error: message }, status);
}

async function getDb(env) {
  return env.JOB_TRACKER_DB;
}

// ─── Stats ────────────────────────────────────────────────────────────────────

async function getStats(env) {
  const db = await getDb(env);

  const total = (await db.prepare('SELECT COUNT(*) as count FROM jobs').first())?.count || 0;
  const newCount = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE status = 'new'").first())?.count || 0;
  const appliedCount = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE status = 'applied'").first())?.count || 0;
  const interviewCount = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE status = 'interview'").first())?.count || 0;
  const offerCount = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE status = 'offer'").first())?.count || 0;
  const rejectedCount = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE status = 'rejected'").first())?.count || 0;

  const scoreRows = await db.prepare('SELECT fit_score FROM jobs WHERE fit_score IS NOT NULL').all();
  const scoreValues = scoreRows.results.map(r => r.fit_score);
  const p1Count = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE apply_priority = 'P1'").first())?.count || 0;
  const p2Count = (await db.prepare("SELECT COUNT(*) as count FROM jobs WHERE apply_priority = 'P2'").first())?.count || 0;

  let meanScore = 0;
  let medianScore = 0;
  if (scoreValues.length > 0) {
    meanScore = Math.round(scoreValues.reduce((a, b) => a + b, 0) / scoreValues.length * 10) / 10;
    const sorted = [...scoreValues].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    medianScore = sorted.length % 2 !== 0 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
  }

  return {
    total_jobs: total,
    new_jobs: newCount,
    applied_jobs: appliedCount,
    interview_jobs: interviewCount,
    offer_jobs: offerCount,
    rejected_jobs: rejectedCount,
    p1_jobs: p1Count,
    p2_jobs: p2Count,
    mean_score: meanScore,
    median_score: medianScore,
  };
}

// ─── Jobs List ────────────────────────────────────────────────────────────────

async function listJobs(env, url) {
  const db = await getDb(env);

  const status = url.searchParams.get('status');
  const priority = url.searchParams.get('priority');
  const verdict = url.searchParams.get('verdict');
  const minScore = url.searchParams.get('min_score');
  const search = url.searchParams.get('search');
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '50'), 200);
  const offset = parseInt(url.searchParams.get('offset') || '0');

  let query = 'SELECT id, title, company, location, fit_score, apply_priority, status, human_verdict, url, first_seen_at FROM jobs';
  const conditions = [];
  const bindings = [];

  if (status) {
    conditions.push('status = ?');
    bindings.push(status);
  }
  if (priority) {
    conditions.push('apply_priority = ?');
    bindings.push(priority);
  }
  if (verdict) {
    conditions.push('human_verdict = ?');
    bindings.push(verdict);
  }
  if (minScore !== null) {
    conditions.push('fit_score >= ?');
    bindings.push(parseInt(minScore));
  }
  if (search) {
    conditions.push('(title LIKE ? OR company LIKE ? OR location LIKE ?)');
    const term = `%${search}%`;
    bindings.push(term, term, term);
  }

  if (conditions.length > 0) {
    query += ' WHERE ' + conditions.join(' AND ');
  }

  query += ' ORDER BY fit_score DESC NULLS LAST, updated_at DESC LIMIT ? OFFSET ?';
  bindings.push(limit, offset);

  const stmt = db.prepare(query);
  const rows = bindings.length > 0
    ? (await stmt.bind(...bindings).all()).results
    : (await db.prepare(query + ' LIMIT ? OFFSET ?').bind(limit, offset).all()).results;

  return rows.map(row => ({
    id: row.id,
    title: row.title,
    company: row.company,
    location: row.location || '',
    fit_score: row.fit_score,
    apply_priority: row.apply_priority,
    status: row.status || 'new',
    human_verdict: row.human_verdict,
    url: row.url,
    first_seen_at: row.first_seen_at,
  }));
}

// ─── Job Detail ───────────────────────────────────────────────────────────────

async function getJob(env, jobId) {
  const db = await getDb(env);
  const row = (await db.prepare('SELECT * FROM jobs WHERE id = ?').bind(jobId).first());

  if (!row) {
    return null;
  }

  return {
    id: row.id,
    title: row.title,
    company: row.company,
    location: row.location || '',
    remote: !!row.remote,
    posted_date: row.posted_date || '',
    salary: row.salary || '',
    url: row.url || '',
    jd_raw: row.jd_raw || '',
    fit_score: row.fit_score,
    interview_chance: row.interview_chance,
    apply_priority: row.apply_priority,
    should_apply: row.should_apply === 1 || row.should_apply === true,
    why_match: row.why_match || '',
    biggest_gap: row.biggest_gap || '',
    resume_tweaks: row.resume_tweaks || '',
    why_company_angle: row.why_company_angle || '',
    status: row.status || 'new',
    human_verdict: row.human_verdict,
    human_score: row.human_score,
    human_feedback: row.human_feedback,
    notes: row.notes,
    seen_count: row.seen_count || 1,
    first_seen_at: row.first_seen_at,
    last_seen_at: row.last_seen_at,
  };
}

// ─── Update Status ────────────────────────────────────────────────────────────

async function updateJobStatus(env, jobId, body) {
  const db = await getDb(env);

  if (!body.status || !VALID_STATUSES.includes(body.status)) {
    throw new Error(`Invalid status. Must be one of: ${VALID_STATUSES.join(', ')}`);
  }

  const existing = (await db.prepare('SELECT id FROM jobs WHERE id = ?').bind(jobId).first());
  if (!existing) {
    throw new Error('Job not found');
  }

  const now = new Date().toISOString();
  let query = 'UPDATE jobs SET status = ?, updated_at = ?';
  const bindings = [body.status, now];

  if (body.status === 'applied') {
    query += ', applied_at = COALESCE(applied_at, ?)';
    bindings.push(now);
  }

  if (body.notes) {
    query += ', notes = ?';
    bindings.push(body.notes);
  }

  query += ' WHERE id = ?';
  bindings.push(jobId);

  await db.prepare(query).bind(...bindings).run();

  return { success: true, job_id: jobId, new_status: body.status };
}

// ─── Add Feedback ─────────────────────────────────────────────────────────────

async function addFeedback(env, jobId, body) {
  const db = await getDb(env);

  const existing = (await db.prepare('SELECT id FROM jobs WHERE id = ?').bind(jobId).first());
  if (!existing) {
    throw new Error('Job not found');
  }

  const now = new Date().toISOString();
  const updates = ['feedback_updated_at = ?'];
  const bindings = [now];

  if (body.verdict) {
    if (!VALID_VERDICTS.includes(body.verdict)) {
      throw new Error(`Invalid verdict. Must be one of: ${VALID_VERDICTS.join(', ')}`);
    }
    updates.push('human_verdict = ?');
    bindings.push(body.verdict);
  }

  if (body.score !== undefined && body.score !== null) {
    if (body.score < 0 || body.score > 100) {
      throw new Error('Score must be between 0 and 100');
    }
    updates.push('human_score = ?');
    bindings.push(body.score);
  }

  if (body.feedback) {
    updates.push('human_feedback = ?');
    bindings.push(body.feedback);
  }

  if (body.notes) {
    updates.push('notes = ?');
    bindings.push(body.notes);
  }

  bindings.push(jobId);
  await db.prepare(`UPDATE jobs SET ${updates.join(', ')} WHERE id = ?`).bind(...bindings).run();

  return { success: true, job_id: jobId };
}

// ─── Bulk Action ──────────────────────────────────────────────────────────────

async function bulkAction(env, body) {
  const db = await getDb(env);

  if (!body.job_ids || body.job_ids.length === 0) {
    throw new Error('No job IDs provided');
  }

  const placeholders = body.job_ids.map(() => '?').join(',');
  let affected = 0;

  if (body.action === 'update_status') {
    if (!body.value || !VALID_STATUSES.includes(body.value)) {
      throw new Error(`Invalid status value: ${body.value}`);
    }
    const now = new Date().toISOString();
    const extra = body.value === 'applied' ? ', applied_at = COALESCE(applied_at, ?)' : '';
    const extraBind = body.value === 'applied' ? [now] : [];
    await db.prepare(
      `UPDATE jobs SET status = ?, updated_at = ?${extra} WHERE id IN (${placeholders})`
    ).bind(body.value, now, ...extraBind, ...body.job_ids).run();
    affected = body.job_ids.length;

  } else if (body.action === 'add_verdict') {
    if (!body.value || !VALID_VERDICTS.includes(body.value)) {
      throw new Error(`Invalid verdict value: ${body.value}`);
    }
    await db.prepare(
      `UPDATE jobs SET human_verdict = ?, feedback_updated_at = ? WHERE id IN (${placeholders})`
    ).bind(body.value, new Date().toISOString(), ...body.job_ids).run();
    affected = body.job_ids.length;

  } else if (body.action === 'delete') {
    await db.prepare(`DELETE FROM jobs WHERE id IN (${placeholders})`).bind(...body.job_ids).run();
    affected = body.job_ids.length;
  } else {
    throw new Error(`Unknown action: ${body.action}`);
  }

  return { success: true, affected_jobs: affected };
}

// ─── Delete Job ────────────────────────────────────────────────────────────────

async function deleteJob(env, jobId) {
  const db = await getDb(env);
  await db.prepare('DELETE FROM jobs WHERE id = ?').bind(jobId).run();
  return { success: true, job_id: jobId };
}

// ─── Searches ──────────────────────────────────────────────────────────────────

async function listSearches(env) {
  const db = await getDb(env);
  const rows = (await db.prepare('SELECT * FROM search_queries ORDER BY last_run DESC').all()).results;
  return rows.map(row => ({
    id: row.id,
    query_name: row.query_name,
    keywords: row.keywords,
    location: row.location,
    last_run: row.last_run,
    jobs_found: row.jobs_found,
  }));
}

// ─── Companies ─────────────────────────────────────────────────────────────────

async function listCompanies(env) {
  const db = await getDb(env);
  const rows = (await db.prepare('SELECT * FROM companies ORDER BY total_jobs DESC').all()).results;
  return rows.map(row => ({
    id: row.id,
    name: row.name,
    industry: row.industry,
    stage: row.stage,
    total_jobs: row.total_jobs,
    applications: row.applications,
    interviews: row.interviews,
    target_company: !!row.target_company,
  }));
}

// ─── Sync Jobs (from JSON export) ─────────────────────────────────────────────

async function syncJobs(env, body) {
  const db = await getDb(env);

  if (!body.jobs || !Array.isArray(body.jobs)) {
    throw new Error('Invalid payload: jobs array required');
  }

  let synced = 0;
  const now = new Date().toISOString();

  for (const job of body.jobs) {
    // Try to find existing job by dedupe_key or URL
    let existing = null;
    if (job.dedupe_key) {
      existing = (await db.prepare('SELECT id FROM jobs WHERE dedupe_key = ?').bind(job.dedupe_key).first());
    }
    if (!existing && job.url) {
      existing = (await db.prepare('SELECT id FROM jobs WHERE url = ?').bind(job.url).first());
    }
    if (!existing && job.title && job.company) {
      const title = job.title.toLowerCase().trim();
      const company = job.company.toLowerCase().trim();
      const location = (job.location || '').toLowerCase().trim();
      existing = (await db.prepare(
        "SELECT id FROM jobs WHERE LOWER(title) = ? AND LOWER(company) = ? AND LOWER(COALESCE(location, '')) = ?"
      ).bind(title, company, location).first());
    }

    if (existing) {
      // Update existing job
      const updates = ['last_seen_at = ?', 'seen_count = seen_count + 1'];
      const bindings = [now];

      if (job.fit_score !== undefined) { updates.push('fit_score = ?'); bindings.push(job.fit_score); }
      if (job.status) { updates.push('status = ?'); bindings.push(job.status); }
      if (job.apply_priority) { updates.push('apply_priority = ?'); bindings.push(job.apply_priority); }
      if (job.human_verdict) { updates.push('human_verdict = ?'); bindings.push(job.human_verdict); }
      if (job.human_score !== undefined) { updates.push('human_score = ?'); bindings.push(job.human_score); }
      if (job.human_feedback) { updates.push('human_feedback = ?'); bindings.push(job.human_feedback); }
      if (job.notes) { updates.push('notes = ?'); bindings.push(job.notes); }
      if (job.interview_chance) { updates.push('interview_chance = ?'); bindings.push(job.interview_chance); }
      if (job.should_apply !== undefined) { updates.push('should_apply = ?'); bindings.push(job.should_apply ? 1 : 0); }
      if (job.why_match) { updates.push('why_match = ?'); bindings.push(job.why_match); }
      if (job.biggest_gap) { updates.push('biggest_gap = ?'); bindings.push(job.biggest_gap); }
      if (job.resume_tweaks) { updates.push('resume_tweaks = ?'); bindings.push(job.resume_tweaks); }
      if (job.why_company_angle) { updates.push('why_company_angle = ?'); bindings.push(job.why_company_angle); }
      if (job.jd_raw) { updates.push('jd_raw = ?'); bindings.push(job.jd_raw); }
      if (job.jd_summary) { updates.push('jd_summary = ?'); bindings.push(job.jd_summary); }
      if (job.posted_date) { updates.push('posted_date = ?'); bindings.push(job.posted_date); }
      if (job.salary) { updates.push('salary = ?'); bindings.push(job.salary); }

      updates.push('updated_at = ?');
      bindings.push(now);
      bindings.push(existing.id);

      await db.prepare(`UPDATE jobs SET ${updates.join(', ')} WHERE id = ?`).bind(...bindings).run();
    } else {
      // Insert new job
      await db.prepare(`
        INSERT INTO jobs (
          title, company, location, remote, posted_date, salary, url,
          jd_raw, jd_summary, fit_score, interview_chance, apply_priority,
          ai_model, why_match, biggest_gap, resume_tweaks, why_company_angle,
          should_apply, status, notes, human_verdict, human_score, human_feedback,
          feedback_updated_at, applied_at, seen_count, source, apify_run_id,
          dedupe_key, last_search_query, scraped_at, first_seen_at, last_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).bind(
        job.title, job.company, job.location || null,
        job.remote ? 1 : 0, job.posted_date || null, job.salary || null, job.url || null,
        job.jd_raw || null, job.jd_summary || null, job.fit_score || null,
        job.interview_chance || null, job.apply_priority || null,
        job.ai_model || null, job.why_match || null, job.biggest_gap || null,
        job.resume_tweaks || null, job.why_company_angle || null,
        job.should_apply !== false ? 1 : 0, job.status || 'new', job.notes || null,
        job.human_verdict || null, job.human_score || null, job.human_feedback || null,
        job.feedback_updated_at || null, job.applied_at || null,
        now, 1, job.source || null, job.apify_run_id || null,
        job.dedupe_key || null, job.last_search_query || null,
        now, now, now
      ).run();
    }
    synced++;
  }

  return { success: true, synced_jobs: synced, total: body.jobs.length };
}

// ─── Router ───────────────────────────────────────────────────────────────────

async function handleRequest(env, request) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/?/, '');
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

  // Rate limiting (skip for sync endpoint)
  if (!path.startsWith('sync') && isRateLimited(ip)) {
    return errorResponse('Rate limit exceeded', 429);
  }

  const method = request.method;

  // Handle CORS preflight
  if (method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }

  try {
    // Route matching
    if (path === 'stats' && method === 'GET') {
      return jsonResponse(await getStats(env));
    }

    if (path === 'jobs' && method === 'GET') {
      return jsonResponse(await listJobs(env, url));
    }

    if (path === 'jobs' && method === 'POST') {
      // Bulk action at /api/jobs
      const body = await request.json();
      return jsonResponse(await bulkAction(env, body));
    }

    if (path.match(/^jobs\/(\d+)\/status$/) && method === 'POST') {
      const jobId = parseInt(path.match(/^jobs\/(\d+)\/status$/)[1]);
      const body = await request.json();
      return jsonResponse(await updateJobStatus(env, jobId, body));
    }

    if (path.match(/^jobs\/(\d+)\/feedback$/) && method === 'POST') {
      const jobId = parseInt(path.match(/^jobs\/(\d+)\/feedback$/)[1]);
      const body = await request.json();
      return jsonResponse(await addFeedback(env, jobId, body));
    }

    if (path.match(/^jobs\/(\d+)$/)) {
      const jobId = parseInt(path.match(/^jobs\/(\d+)$/)[1]);
      if (method === 'GET') {
        const job = await getJob(env, jobId);
        if (!job) return errorResponse('Job not found', 404);
        return jsonResponse(job);
      }
      if (method === 'DELETE') {
        return jsonResponse(await deleteJob(env, jobId));
      }
    }

    if (path === 'searches' && method === 'GET') {
      return jsonResponse(await listSearches(env));
    }

    if (path === 'companies' && method === 'GET') {
      return jsonResponse(await listCompanies(env));
    }

    if (path === 'sync' && method === 'POST') {
      // Sync endpoint - expects JSON payload with jobs array
      const body = await request.json();
      return jsonResponse(await syncJobs(env, body));
    }

    // Health check
    if (path === 'health' && method === 'GET') {
      return jsonResponse({ status: 'ok', timestamp: new Date().toISOString() });
    }

    return errorResponse(`Not found: ${method} /api/${path}`, 404);

  } catch (err) {
    console.error(`Error handling ${method} /api/${path}:`, err);
    return errorResponse(err.message || 'Internal server error', 500);
  }
}

// ─── Worker Entry Point ────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    // Handle CORS preflight for all routes
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        },
      });
    }

    // Only handle /api/* routes
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/api')) {
      // Serve dashboard for non-API routes
      return new Response('Job Tracker API — use /api/* endpoints', {
        status: 200,
        headers: { 'Content-Type': 'text/plain' },
      });
    }

    return handleRequest(env, request);
  },
};
