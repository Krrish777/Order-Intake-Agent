import Link from 'next/link';
import type { RunData } from '@/app/lib/runShape';
import { deriveCustomerName, deriveVerdict } from '@/app/lib/runShape';

const GITHUB_URL = 'https://github.com/Krrish777/Order-Intake-Agent';

const VERDICT_COLOR: Record<string, string> = {
  ESCALATE: 'var(--red)',
  'AUTO-APPROVE': 'var(--green)',
  'REPLY MERGED': 'var(--ink)',
};

const VERDICT_KICKER: Record<string, string> = {
  ESCALATE: 'A — REFUSAL · ON PRICE GROUNDS',
  'AUTO-APPROVE': 'A — COMMIT · JUDGE PASSED',
  'REPLY MERGED': 'A — MEMORY · CLARIFICATION ANSWERED',
};

export default function ReadSheet({ id, data }: { id: string; data: RunData }) {
  const verdict = deriveVerdict(data);
  const customer = deriveCustomerName(data);
  const corrShort = data.correlation_id.slice(0, 8);
  // sheetId is the canonical run id (A-001/A-002/A-003), not the firestore session_id
  const sheetId = id.split('-').slice(0, 2).join('-');
  const capturedAt = formatCaptured(data.captured_at);
  const subject = data.inbound_email?.headers.Subject ?? '—';

  return (
    <main className="page read-sheet">
      <div className="corner-tr" />
      <div className="corner-bl" />

      {/* TOP BAR */}
      <header className="top-bar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">
            Order Intake<span className="brand-accent">Agent</span>
          </span>
        </Link>
        <nav className="nav-center" aria-label="primary">
          <Link href="/">Landing</Link>
          <Link href="/runs/A-001-patterson">A-001</Link>
          <Link href="/runs/A-002-mm-machine">A-002</Link>
          <Link href="/runs/A-003-birch-valley">A-003</Link>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            Source <span className="ext">↗</span>
          </a>
        </nav>
        <div className="nav-right">
          <span className="nav-log">
            Sheet <b style={{ color: 'var(--ink)' }}>{sheetId.toUpperCase()}</b>
          </span>
          <Link className="nav-cta" href="/">
            ← Back to landing
          </Link>
        </div>
      </header>

      {/* TITLE BLOCK */}
      <header className="title-block">
        <div>
          <div className="head">SHEET</div>
          <div className="v">{sheetId.toUpperCase()}</div>
          <div className="v sub">{verdict.toLowerCase()}</div>
        </div>
        <div>
          <div className="head">CUSTOMER</div>
          <div className="v">{customer}</div>
          <div className="v sub">{subject}</div>
        </div>
        <div>
          <div className="head">CAPTURED</div>
          <div className="v">{capturedAt}</div>
          <div className="v sub">
            {data.total_wall_clock_seconds !== null
              ? `${data.total_wall_clock_seconds.toFixed(1)} s end-to-end`
              : '—'}
          </div>
        </div>
        <div className="verdict-cell">
          <div className="head">VERDICT</div>
          <div className="badge" style={{ color: VERDICT_COLOR[verdict] }}>
            <span className="dot" style={{ background: VERDICT_COLOR[verdict] }} />
            {verdict}
          </div>
          <div className="wall">{data.stage_count} stages · {data.raw_audit_event_count} audit events</div>
        </div>
      </header>

      {/* CAPTURED STRIP */}
      <div className="captured-strip">
        <span className="badge">CAPTURED FROM A REAL RUN</span>
        <span className="meta">
          correlation_id <code>{corrShort}…</code>
          <span className="dot">·</span>
          <b>{data.raw_audit_event_count}</b> audit events
          <span className="dot">·</span>
          captured <b>{capturedAt}</b>
        </span>
        <a href={`https://github.com/Krrish777/Order-Intake-Agent/blob/master/design/wireframes-v2/data/${id}.json`} target="_blank" rel="noopener noreferrer">
          view raw JSON ↗
        </a>
      </div>

      {/* SHEET INTRO */}
      <section className="sheet-intro">
        <div className="kicker">{VERDICT_KICKER[verdict] ?? '—'}</div>
        <p className="narration">{narrationFor(verdict, customer, data)}</p>
      </section>

      {/* §I CORRESPONDENCE */}
      <section className="section">
        <div className="section-head">
          <span className="roman">§ I</span>
          <h2>
            Correspondence<span className="serif">what arrived in the inbox</span>
          </h2>
          <span className="meta">INBOUND · TEXT</span>
        </div>
        <div className="layout">
          <div className="letter">
            <div className="tab">Inbound</div>
            {data.inbound_email ? (
              <>
                <dl className="hdr">
                  <dt>From</dt>
                  <dd>{data.inbound_email.headers.From}</dd>
                  <dt>To</dt>
                  <dd>{data.inbound_email.headers.To}</dd>
                  <dt>Subject</dt>
                  <dd className="subject">{data.inbound_email.headers.Subject}</dd>
                  <dt>Date</dt>
                  <dd>{data.inbound_email.headers.Date}</dd>
                </dl>
                <div className="body">
                  {data.inbound_email.body.split(/\n\n+/).map((para, i) => (
                    <p key={i}>{para.split('\n').map((l, li, arr) => (
                      <span key={li}>
                        {l}
                        {li < arr.length - 1 && <br />}
                      </span>
                    ))}</p>
                  ))}
                </div>
              </>
            ) : (
              <div className="body">
                <p>No inbound email captured for this run.</p>
              </div>
            )}
          </div>
          <aside className="margin">
            <div className="note">
              <span className="label">format</span>
              <div className="body">
                Plain-text body, parsed direct from the .eml fixture — exactly what the agent
                saw.
              </div>
            </div>
            <div className="note fact">
              <span className="label">message-id</span>
              <div className="body" style={{ wordBreak: 'break-all' }}>
                {data.source_message_id ?? '—'}
              </div>
            </div>
            <div className="note fact">
              <span className="label">source</span>
              <div className="body">
                <b>session_id</b> {data.session_id ?? '—'}
                <br />
                <b>agent_version</b> {data.agent_version ?? '—'}
              </div>
            </div>
          </aside>
        </div>
      </section>

      {/* §V LATENCY */}
      <section className="section no-border">
        <div className="section-head">
          <span className="roman">§ V</span>
          <h2>
            Numbers<span className="serif">how the {data.stage_count} stages spent the wall clock</span>
          </h2>
          <span className="meta">LATENCY · MS</span>
        </div>
        <table className="latency">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>STAGE</th>
              <th>OUTCOME</th>
              <th className="num">MS</th>
            </tr>
          </thead>
          <tbody>
            {data.stages.map((s, i) => (
              <tr key={`${s.stage}-${i}`}>
                <td className="num">{String(i + 1).padStart(2, '0')}</td>
                <td className="stage">{s.stage.replace(/_stage$/, '').replace(/_/g, ' ')}</td>
                <td className="note">{s.outcome ?? '—'}</td>
                <td className="num">
                  {s.duration_ms !== null ? s.duration_ms.toFixed(0) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>TOTAL WALL CLOCK</td>
              <td className="num">
                {data.total_wall_clock_seconds !== null
                  ? `${(data.total_wall_clock_seconds * 1000).toFixed(0)}`
                  : '—'}
              </td>
            </tr>
          </tfoot>
        </table>
      </section>

      <footer className="colophon">
        <div>
          <div className="head">Verdict</div>
          <div className="v" style={{ color: VERDICT_COLOR[verdict] }}>
            {verdict}
          </div>
        </div>
        <div>
          <div className="head">Run</div>
          <div className="v">
            <code>{data.correlation_id.slice(0, 12)}…</code>
          </div>
        </div>
        <div>
          <div className="head">Captured</div>
          <div className="v">{capturedAt}</div>
        </div>
        <div>
          <div className="head">Back</div>
          <div className="v">
            <Link href="/">↩ landing</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}

function formatCaptured(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toISOString().replace('T', ' ').replace(/:\d{2}\.\d+Z$/, ' UTC');
  } catch {
    return iso;
  }
}

function narrationFor(verdict: string, customer: string, data: RunData): string {
  const orderLines =
    data.orders[0]?.lines?.length ??
    data.exceptions[0]?.parsed_doc?.sub_documents?.[0]?.line_items?.length ??
    null;
  if (verdict === 'ESCALATE') {
    return (
      `${customer} sent a purchase order. The agent matched every line, then refused to write — ` +
      `prices on the customer's sheet did not reconcile with the master price list, and the agent does not ` +
      `commit to numbers it cannot defend.`
    );
  }
  if (verdict === 'AUTO-APPROVE') {
    return (
      `${customer} reordered ${orderLines ?? 'a handful of'} familiar SKUs. Every line matched at tier 1, ` +
      `the Gemini judge cleared the confirmation email, and the order was written to Firestore.`
    );
  }
  return (
    `${customer} replied to an open clarification. The reply-shortcircuit stage matched the open exception in ` +
    `milliseconds and advanced its state — no second classification, no second extraction.`
  );
}
