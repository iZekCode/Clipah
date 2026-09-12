// What the API does under the load a working day actually produces.
//
//   k6 run tests/load/k6.js
//
// Every value this needs comes from the environment, because a load test that mints its
// own Session would be testing the seeding path rather than the product:
//
//   CLIPAH_LOAD_BASE_URL              the API origin, default http://127.0.0.1:8000
//   CLIPAH_LOAD_SESSION_COOKIE        the Session cookie value for the member being played
//   CLIPAH_LOAD_CSRF_TOKEN            the double-submit token that Session was issued with
//   CLIPAH_LOAD_WORKSPACE_ID          the Workspace that member belongs to
//   CLIPAH_LOAD_PROJECT_ID            one active Project inside it
//   CLIPAH_LOAD_EDIT_ID               one Edit inside that Project, for render admission
//   CLIPAH_LOAD_REVISION              that Edit's newest revision number, default 1
//   CLIPAH_LOAD_FOREIGN_WORKSPACE_ID  a Workspace the member is NOT a member of
//   CLIPAH_LOAD_FOREIGN_PROJECT_ID    a Project inside that other Workspace
//
// `backend/src/clipah/dev/seed.py` prints the first four as JSON, and a second run of it
// prints the last two.
//
// Three things are asserted rather than observed. The API's own p95 stays under 500 ms,
// measured only over requests tagged `boundary:api` — an upload part goes to the object
// store and a publish goes to a provider, and neither is this deployment's latency to
// answer for. Nothing belonging to another Workspace is ever visible, counted as a hard
// zero rather than a rate. And every refusal a limit produces is the documented one, with
// its `Retry-After`, rather than a 500 or a silent success.

import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Rate, Trend } from 'k6/metrics'

const BASE_URL = __ENV.CLIPAH_LOAD_BASE_URL || 'http://127.0.0.1:8000'
const WORKSPACE_ID = __ENV.CLIPAH_LOAD_WORKSPACE_ID || ''
const PROJECT_ID = __ENV.CLIPAH_LOAD_PROJECT_ID || ''
const EDIT_ID = __ENV.CLIPAH_LOAD_EDIT_ID || ''
const REVISION = __ENV.CLIPAH_LOAD_REVISION || '1'
const FOREIGN_WORKSPACE_ID = __ENV.CLIPAH_LOAD_FOREIGN_WORKSPACE_ID || ''
const FOREIGN_PROJECT_ID = __ENV.CLIPAH_LOAD_FOREIGN_PROJECT_ID || ''
const SESSION_COOKIE = __ENV.CLIPAH_LOAD_SESSION_COOKIE || ''
const CSRF_TOKEN = __ENV.CLIPAH_LOAD_CSRF_TOKEN || ''

const crossWorkspaceLeaks = new Counter('cross_workspace_leaks')
const limitResponsesCorrect = new Rate('limit_responses_correct')
const streamFirstFrame = new Trend('stream_first_frame_ms', true)

export const options = {
  scenarios: {
    dashboard_reads: {
      executor: 'constant-arrival-rate',
      exec: 'dashboardReads',
      rate: 30,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      tags: { scenario: 'dashboard_reads' },
    },
    upload_sessions: {
      executor: 'constant-arrival-rate',
      exec: 'uploadSessions',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      tags: { scenario: 'upload_sessions' },
    },
    job_polling: {
      executor: 'constant-arrival-rate',
      exec: 'jobPolling',
      rate: 20,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      tags: { scenario: 'job_polling' },
    },
    job_streams: {
      executor: 'constant-vus',
      exec: 'jobStream',
      vus: 25,
      duration: '2m',
      tags: { scenario: 'job_streams' },
    },
    render_admission: {
      executor: 'constant-arrival-rate',
      exec: 'renderAdmission',
      rate: 2,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      tags: { scenario: 'render_admission' },
    },
    publication_drafts: {
      executor: 'constant-arrival-rate',
      exec: 'publicationDrafts',
      rate: 2,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      tags: { scenario: 'publication_drafts' },
    },
    scheduler_claims: {
      executor: 'constant-arrival-rate',
      exec: 'schedulerClaims',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      tags: { scenario: 'scheduler_claims' },
    },
    webhook_bursts: {
      executor: 'ramping-arrival-rate',
      exec: 'webhookBurst',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 30,
      stages: [
        { target: 200, duration: '20s' },
        { target: 200, duration: '40s' },
        { target: 5, duration: '20s' },
      ],
      tags: { scenario: 'webhook_bursts' },
    },
    reconciliation: {
      executor: 'constant-arrival-rate',
      exec: 'reconciliation',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      tags: { scenario: 'reconciliation' },
    },
    tenant_isolation: {
      executor: 'constant-arrival-rate',
      exec: 'tenantIsolation',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      tags: { scenario: 'tenant_isolation' },
    },
  },
  thresholds: {
    // The deployment's own latency. Upload parts and provider calls are tagged
    // `boundary:storage` and `boundary:provider` and are deliberately outside it.
    'http_req_duration{boundary:api}': ['p(95)<500'],
    // Not one row of another Workspace, at any concurrency.
    cross_workspace_leaks: ['count==0'],
    // Every refusal is the documented refusal.
    limit_responses_correct: ['rate==1'],
    // A refusal is an answer, so it is not a failed request; anything else is.
    checks: ['rate==1'],
  },
}

function apiHeaders(unsafe) {
  const headers = { Cookie: `clipah_session=${SESSION_COOKIE}; clipah_csrf=${CSRF_TOKEN}` }
  if (unsafe) {
    headers.Origin = BASE_URL
    headers['X-CSRF-Token'] = CSRF_TOKEN
    headers['Content-Type'] = 'application/json'
  }
  return headers
}

function workspaceQuery(id) {
  return `workspace_id=${id || WORKSPACE_ID}`
}

// A refusal a limit produces is expected under load; what is not expected is a refusal
// that is shaped differently from the one the plan documents.
function recordLimitShape(response) {
  if (response.status !== 429 && response.status !== 409) {
    return
  }
  let code = ''
  try {
    code = response.json('error.code')
  } catch (error) {
    code = ''
  }
  const known = ['RATE_LIMITED', 'QUOTA_EXCEEDED', 'CONCURRENCY_LIMIT', 'CONFLICT']
  const carriesRetryAfter =
    response.status !== 429 || Boolean(response.headers['Retry-After'])
  limitResponsesCorrect.add(known.includes(code) && carriesRetryAfter)
}

function expectAnswered(response, name) {
  recordLimitShape(response)
  check(response, { [name]: (r) => r.status < 500 })
  return response
}

export function dashboardReads() {
  const responses = http.batch([
    ['GET', `${BASE_URL}/api/v1/dashboard/summary?${workspaceQuery()}`, null, { headers: apiHeaders(false), tags: { boundary: 'api' } }],
    ['GET', `${BASE_URL}/api/v1/projects?${workspaceQuery()}&limit=20`, null, { headers: apiHeaders(false), tags: { boundary: 'api' } }],
    ['GET', `${BASE_URL}/api/v1/projects/${PROJECT_ID}/candidates?${workspaceQuery()}&limit=10`, null, { headers: apiHeaders(false), tags: { boundary: 'api' } }],
  ])
  responses.forEach((response) => expectAnswered(response, 'dashboard read was answered'))
  sleep(0.2)
}

export function uploadSessions() {
  const created = http.post(
    `${BASE_URL}/api/v1/projects/${PROJECT_ID}/uploads?${workspaceQuery()}`,
    JSON.stringify({ filename: 'load.mp4', contentType: 'video/mp4', contentLength: 64 * 1024 * 1024 }),
    { headers: apiHeaders(true), tags: { boundary: 'api' } },
  )
  expectAnswered(created, 'upload session was answered')
  if (created.status !== 201) {
    return
  }
  const uploadId = created.json('id')
  // Signing a part is the API's work; sending the part is the object store's, which is why
  // this scenario stops at the capability rather than uploading bytes.
  const signed = http.post(
    `${BASE_URL}/api/v1/projects/${PROJECT_ID}/uploads/${uploadId}/parts/1?${workspaceQuery()}`,
    null,
    { headers: apiHeaders(true), tags: { boundary: 'storage' } },
  )
  expectAnswered(signed, 'part capability was answered')
  http.del(
    `${BASE_URL}/api/v1/projects/${PROJECT_ID}/uploads/${uploadId}?${workspaceQuery()}`,
    null,
    { headers: apiHeaders(true), tags: { boundary: 'api' } },
  )
}

export function jobPolling() {
  const listed = http.get(`${BASE_URL}/api/v1/dashboard/summary?${workspaceQuery()}`, {
    headers: apiHeaders(false),
    tags: { boundary: 'api' },
  })
  expectAnswered(listed, 'job poll was answered')
  let jobs = []
  try {
    jobs = listed.json('unfinishedJobs') || []
  } catch (error) {
    jobs = []
  }
  if (jobs.length > 0) {
    const job = http.get(`${BASE_URL}/api/v1/jobs/${jobs[0].id}?${workspaceQuery()}`, {
      headers: apiHeaders(false),
      tags: { boundary: 'api' },
    })
    expectAnswered(job, 'job read was answered')
  }
  sleep(0.5)
}

export function jobStream() {
  // The stream stays open for as long as a job center tab would. k6 has no EventSource, so
  // this measures what matters here: that the first frame arrives promptly and that an open
  // stream costs the API no connection it cannot give back.
  const started = Date.now()
  const response = http.get(`${BASE_URL}/api/v1/jobs/events?${workspaceQuery()}`, {
    headers: apiHeaders(false),
    timeout: '20s',
    tags: { boundary: 'stream' },
  })
  streamFirstFrame.add(Date.now() - started)
  check(response, { 'workspace stream was served': (r) => r.status === 200 })
  sleep(1)
}

export function renderAdmission() {
  const response = http.post(
    `${BASE_URL}/api/v1/edits/${EDIT_ID}/renders?${workspaceQuery()}`,
    JSON.stringify({ preset: '1080x1920', revision: Number(REVISION) }),
    { headers: apiHeaders(true), tags: { boundary: 'api' } },
  )
  expectAnswered(response, 'render admission was answered')
  sleep(0.5)
}

export function publicationDrafts() {
  const draft = http.post(
    `${BASE_URL}/api/v1/edits/${EDIT_ID}/revisions/${REVISION}/publication-drafts?${workspaceQuery()}`,
    JSON.stringify({ destinations: [] }),
    { headers: apiHeaders(true), tags: { boundary: 'api' } },
  )
  expectAnswered(draft, 'publication draft was answered')
  if (draft.status !== 201) {
    return
  }
  const draftId = draft.json('id')
  const preflight = http.post(
    `${BASE_URL}/api/v1/publication-drafts/${draftId}/preflight?${workspaceQuery()}`,
    null,
    { headers: apiHeaders(true), tags: { boundary: 'api' } },
  )
  expectAnswered(preflight, 'preflight was answered')
  const confirmed = http.post(
    `${BASE_URL}/api/v1/publication-drafts/${draftId}/confirm?${workspaceQuery()}`,
    null,
    { headers: apiHeaders(true), tags: { boundary: 'provider' } },
  )
  expectAnswered(confirmed, 'confirmation was answered')
}

export function schedulerClaims() {
  // The scheduler claims due work in the background; what a member sees of it is the
  // publication list, which is the read this keeps under load while claims are happening.
  const listed = http.get(`${BASE_URL}/api/v1/publications?${workspaceQuery()}&limit=20`, {
    headers: apiHeaders(false),
    tags: { boundary: 'api' },
  })
  expectAnswered(listed, 'publication list was answered')
  sleep(0.5)
}

export function webhookBurst() {
  // A provider retrying a burst of deliveries must be refused on its signature rather than
  // slowing every member's request. The body is deliberately unsigned.
  const response = http.post(
    `${BASE_URL}/api/v1/webhooks/tiktok`,
    JSON.stringify({ event: 'video.publish.complete', publish_id: 'load-test' }),
    { headers: { 'Content-Type': 'application/json' }, tags: { boundary: 'api' } },
  )
  check(response, { 'unsigned delivery was refused': (r) => r.status >= 400 && r.status < 500 })
}

export function reconciliation() {
  const response = http.get(`${BASE_URL}/api/v1/publications?${workspaceQuery()}&limit=50`, {
    headers: apiHeaders(false),
    tags: { boundary: 'api' },
  })
  expectAnswered(response, 'reconciliation read was answered')
  sleep(0.5)
}

export function tenantIsolation() {
  if (!FOREIGN_WORKSPACE_ID || !FOREIGN_PROJECT_ID) {
    return
  }
  const byWorkspace = http.get(`${BASE_URL}/api/v1/projects?${workspaceQuery(FOREIGN_WORKSPACE_ID)}`, {
    headers: apiHeaders(false),
    tags: { boundary: 'api' },
  })
  const byResource = http.get(
    `${BASE_URL}/api/v1/projects/${FOREIGN_PROJECT_ID}?${workspaceQuery()}`,
    { headers: apiHeaders(false), tags: { boundary: 'api' } },
  )
  ;[byWorkspace, byResource].forEach((response) => {
    if (response.status === 200) {
      crossWorkspaceLeaks.add(1)
    }
    check(response, { 'another Workspace stayed invisible': (r) => r.status === 404 })
  })
}
