import Link from 'next/link';

const GITHUB_URL = 'https://github.com/Krrish777/Order-Intake-Agent';

export default function Landing() {
  return (
    <main className="page">
      <div className="corner-tr" />
      <div className="corner-bl" />

      {/* TOP BAR */}
      <div className="top-bar">
        <div className="left">
          <span>Order Intake Agent</span>
          <span className="stamp"><span className="pulse" />LIVE · TRACK A · v0.4</span>
        </div>
        <div className="center">SHEET R-000 · LANDING · NTS</div>
        <div className="right">
          <a
            className="gh-btn"
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="View source on GitHub"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                fill="currentColor"
                d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z"
              />
            </svg>
            <span>Source</span>
            <span className="ext">↗</span>
          </a>
        </div>
      </div>

      {/* HERO */}
      <section className="hero">
        <span className="ticks" aria-hidden="true" />
        <div className="kicker">— Google Solution Hackathon · Order Intake Agent</div>
        <h1>
          Reads order emails<span className="slash">.</span>
          <br />
          <em>Refuses</em> to write bad ones.
        </h1>
        <p className="dek">
          An AI agent for <b>B2B order intake</b>. Reads every incoming PO, validates each
          line, and writes only the clean ones to your ERP.
        </p>
        <div className="ctas">
          <Link className="cta" href="/run/A-001-patterson">
            Run the pipeline <span className="arrow">→</span>
          </Link>
          <a className="cta ghost" href="#howto">
            Watch how it works <span className="arrow">↓</span>
          </a>
        </div>
        <span className="last-run">
          <span className="pulse" />last run <span className="dot">·</span>{' '}
          <b>18 APR 2026 · 10:12 EDT</b> <span className="dot">·</span> Patterson Industrial{' '}
          <span className="dot">·</span>{' '}
          <b style={{ color: 'var(--red)' }}>ESCALATED</b> <span className="dot">·</span> 43.1
          s end-to-end
        </span>
      </section>

      {/* STATS STRIP */}
      <section className="stats-strip" aria-label="key metrics">
        <div className="stat accent">
          <span className="tag">N · 01</span>
          <div className="num">0</div>
          <div className="lbl">Bad Writes</div>
          <div className="sub">to the ERP, ever</div>
        </div>
        <div className="stat">
          <span className="tag">N · 02</span>
          <div className="num">11</div>
          <div className="lbl">Sequential Stages</div>
          <div className="sub">one Gemini judge gates send</div>
        </div>
        <div className="stat">
          <span className="tag">N · 03</span>
          <div className="num">497</div>
          <div className="lbl">Unit Tests</div>
          <div className="sub">+ 30 integration · 3 evals</div>
        </div>
        <div className="stat">
          <span className="tag">N · 04</span>
          <div className="num">3</div>
          <div className="lbl">Captured Runs</div>
          <div className="sub">real emails · real verdicts</div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section className="section" id="howto">
        <div className="section-head">
          <span className="num">§ 01</span>
          <h2>
            How it works<span className="serif">three steps · the rest is detail</span>
          </h2>
          <span className="meta">READ / VALIDATE / DECIDE</span>
        </div>
        <p className="lede">
          Every email walks the same path. The agent reads inbound POs in any format,
          validates each line against the customer&apos;s item master, and either commits the
          order to the ERP — or refuses, politely, and drafts a clarification for a human to
          send.
        </p>

        <div className="howto">
          <div className="step">
            <span className="step-tag">STEP <span className="r">·</span> 01</span>
            <div className="glyph">R</div>
            <h3>Read</h3>
            <p>
              Pull every inbound message from Gmail, classify each attachment, extract every
              line item.
            </p>
            <div className="formats">PDF · XLS · EDI · CSV · FREE-TEXT</div>
          </div>
          <div className="step">
            <span className="step-tag">STEP <span className="r">·</span> 02</span>
            <div className="glyph">V</div>
            <h3>Validate</h3>
            <p>
              Resolve each customer; match every SKU through three tiers — exact, fuzzy,
              semantic embedding.
            </p>
            <div className="formats">EXACT → FUZZY → EMBEDDING</div>
          </div>
          <div className="step refuse">
            <span className="step-tag">STEP <span className="r">·</span> 03</span>
            <div className="glyph">D</div>
            <h3>Decide</h3>
            <p>
              Above 0.95 confidence: commit to the ERP. Above 0.80: clarify with the customer.
              Below: escalate to a human.
            </p>
            <div className="formats">AUTO · CLARIFY · ESCALATE</div>
          </div>
        </div>
      </section>

      {/* THREE RUNS */}
      <section className="section" id="runs">
        <div className="section-head">
          <span className="num">§ 02</span>
          <h2>
            See it in action<span className="serif">three real emails · three real verdicts</span>
          </h2>
          <span className="meta">A-001 / A-002 / A-003</span>
        </div>
        <p className="lede">
          The agent has already processed three emails this week. Each run captured its full
          audit trail — every stage, every artefact, every Firestore write. Click any sheet to
          read the drawing.
        </p>

        <div className="cards">
          <Link className="card escalate" href="/runs/A-001-patterson">
            <span className="card-tag">SHEET <span className="r">·</span> A-001</span>
            <div className="card-body">
              <div className="customer">Patterson Industrial Supply Co.</div>
              <div className="subject">PO-28491 — Atlanta monthly consolidated</div>
              <div className="stats-row">
                <div className="stat">22 / 22<span>matched at tier 1</span></div>
                <div className="stat">19<span>price violations</span></div>
              </div>
            </div>
            <div className="verdict">
              <span className="badge"><span className="dot" />ESCALATE</span>
              <span className="wall">43.1 s · 11 stages</span>
            </div>
            <div className="read">read sheet <span className="arrow">→</span></div>
          </Link>

          <Link className="card auto" href="/runs/A-002-mm-machine">
            <span className="card-tag">SHEET <span className="r">·</span> A-002</span>
            <div className="card-body">
              <div className="customer">M&amp;M Machine &amp; Fabrication</div>
              <div className="subject">Shop reorder — hex nuts and R2 hose</div>
              <div className="stats-row">
                <div className="stat">2 / 2<span>matched at tier 1</span></div>
                <div className="stat">$127.40<span>committed to ERP</span></div>
              </div>
            </div>
            <div className="verdict">
              <span className="badge"><span className="dot" />AUTO-APPROVE</span>
              <span className="wall">62.5 s · judge pass</span>
            </div>
            <div className="read">read sheet <span className="arrow">→</span></div>
          </Link>

          <Link className="card reply" href="/runs/A-003-birch-valley">
            <span className="card-tag">SHEET <span className="r">·</span> A-003</span>
            <div className="card-body">
              <div className="customer">Birch Valley Farm Equipment</div>
              <div className="subject">Re: Need by tomorrow — Hirshey planter</div>
              <div className="stats-row">
                <div className="stat">PENDING → REVIEW<span>exception advanced</span></div>
                <div className="stat">18 ms<span>reply-check hit</span></div>
              </div>
            </div>
            <div className="verdict">
              <span className="badge"><span className="dot" />REPLY MERGED</span>
              <span className="wall">5.8 s · 11 stages</span>
            </div>
            <div className="read">read sheet <span className="arrow">→</span></div>
          </Link>
        </div>

        <div className="cards-captions">
          <div>the refusal — when prices don&apos;t reconcile.</div>
          <div>the commit — when everything reconciles.</div>
          <div>the memory — when an old question gets answered.</div>
        </div>
      </section>

      {/* PIPELINE */}
      <section className="section">
        <div className="section-head">
          <span className="num">§ 03</span>
          <h2>
            Under the hood<span className="serif">eleven stages · one wire · same path</span>
          </h2>
          <span className="meta">SEQUENTIAL · ADK</span>
        </div>
        <p className="lede">
          Behind the three steps above sits a sequential pipeline of eleven stages. Six produce
          persisted artefacts. <em>Stage ten is a Gemini judge</em> that blocks outbound emails
          with hallucinations or unauthorized commitments before a single byte leaves the
          building.
        </p>

        <div className="pipeline-diagram">
          <span className="diag-label">PIPELINE · 11 STAGES</span>
          <div className="row">
            {[
              ['01', 'ingest'],
              ['02', 'reply check'],
              ['03', 'classify'],
              ['04', 'parse'],
              ['05', 'validate'],
              ['06', 'clarify'],
              ['07', 'persist'],
              ['08', 'confirm'],
              ['09', 'finalize'],
              ['10', 'judge'],
              ['11', 'send'],
            ].map(([n, name], i) => (
              <div
                key={n}
                className={`node ${i === 9 ? 'gate' : ''} ${i === 10 ? 'send' : ''}`.trim()}
              >
                <div className="circle">{n}</div>
                <div className="name">{name}</div>
              </div>
            ))}
          </div>
          <div className="legend">
            <span><span className="swatch" />standard stage</span>
            <span><span className="swatch gate" />quality gate</span>
            <span><span className="swatch send" />egress · gmail</span>
          </div>
        </div>

        <div className="deeper">
          <Link href="/runs/A-002-mm-machine">read the architecture in detail →</Link>
        </div>
      </section>

      {/* TRUST */}
      <section className="trust">
        <div className="stack">
          <div className="row">
            <span className="label">Built on</span>
            <span className="item">Google ADK</span>
            <span className="item">Gemini 3 Flash</span>
            <span className="item">Firestore</span>
            <span className="item">LlamaCloud</span>
            <span className="item">Pub/Sub</span>
          </div>
          <div className="row">
            <span className="label">Verified by</span>
            <span className="item">497 unit tests</span>
            <span className="item">30 integration tests</span>
            <span className="item">3-case eval set</span>
          </div>
          <div className="row">
            <span className="label">Quality gate</span>
            <span className="item">every outbound email passes a Gemini judge</span>
          </div>
        </div>
        <blockquote className="quote">
          &ldquo;An agent that <em>knows when to stop</em>, and how to ask, is the
          point.&rdquo;
        </blockquote>
      </section>

      {/* COLOPHON */}
      <footer className="colophon">
        <div>
          <div className="head">Submitted to</div>
          <div className="v">Google Solution Hackathon</div>
        </div>
        <div>
          <div className="head">Build</div>
          <div className="v">Order Intake Agent · v0.4</div>
        </div>
        <div>
          <div className="head">Date</div>
          <div className="v">25 Apr 2026</div>
        </div>
        <div>
          <div className="head">Set in</div>
          <div className="v serif">Jost · Azeret Mono · Instrument Serif</div>
        </div>
      </footer>
    </main>
  );
}
