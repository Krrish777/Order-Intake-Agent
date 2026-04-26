import Link from 'next/link';

const GITHUB_URL = 'https://github.com/Krrish777/Order-Intake-Agent';

export default function Landing() {
  return (
    <main className="page" id="top">
      <div className="corner-tr" />
      <div className="corner-bl" />

      {/* TOP BAR */}
      <header className="top-bar">
        <Link className="brand" href="#top">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">
            Order Intake<span className="brand-accent">Agent</span>
          </span>
        </Link>
        <nav className="nav-center" aria-label="primary">
          <a href="#howto">How it works</a>
          <a href="#runs">Runs</a>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            Source <span className="ext">↗</span>
          </a>
        </nav>
        <div className="nav-right">
          <Link className="nav-cta" href="/runs/A-001-patterson">
            View latest run
          </Link>
        </div>
      </header>

      {/* HERO — split-screen */}
      <section className="hero">
        <div className="hero-split">
          <div className="hero-text">
            <div className="kicker">— Google Solution Hackathon · Order Intake Agent</div>
            <h1>
              Reads order emails<span className="slash">.</span>
              <br />
              <em>Refuses</em> to write bad ones.
            </h1>
            <p className="dek">
              An AI agent for <b>B2B order intake</b>. Reads every PO, validates each line, and
              writes only the clean ones to your ERP.
            </p>
            <div className="ctas">
              <Link className="cta" href="/runs/A-001-patterson">
                View the latest run <span className="arrow">→</span>
              </Link>
              <a className="cta ghost" href="#" data-pending="video">
                Watch 2-min demo <span className="arrow">↗</span>
              </a>
            </div>
          </div>
          <div className="hero-image">
            <img
              src="/intake-chaos.jpg"
              alt="EDI gets the red carpet — a robot is welcomed past velvet ropes. A second lane labelled Portal sits abandoned with cobwebs and dead branches. A third lane labelled Email is a chaotic crowd waving envelopes and papers — the real orders live here, and so does the chaos."
            />
          </div>
        </div>
      </section>

      {/* THREE RUNS — § 01, the proof */}
      <section className="section" id="runs">
        <div className="section-head">
          <span className="num">§ 01</span>
          <h2>
            See it in action<span className="serif">three real emails · three real verdicts</span>
          </h2>
          <span className="meta">A-001 / A-002 / A-003</span>
        </div>
        <p className="lede">
          The agent has already processed three emails this week. Each run captured its full
          audit trail — every stage, every artefact, every Firestore write. Click any sheet
          to read the drawing.
        </p>

        <div className="cards">
          <Link className="card escalate" href="/runs/A-001-patterson">
            <span className="card-tag">
              SHEET <span className="r">·</span> A-001
            </span>
            <div className="card-body">
              <div className="customer">Patterson Industrial Supply Co.</div>
              <div className="subject">PO-28491 — Atlanta monthly consolidated</div>
              <div className="stats-row">
                <div className="stat">
                  22 / 22<span>matched at tier 1</span>
                </div>
                <div className="stat">
                  19<span>price violations</span>
                </div>
              </div>
            </div>
            <div className="verdict">
              <span className="badge">
                <span className="dot" />
                ESCALATE
              </span>
              <span className="wall">43.1 s · 11 stages</span>
            </div>
            <div className="read">
              read sheet <span className="arrow">→</span>
            </div>
          </Link>

          <Link className="card auto" href="/runs/A-002-mm-machine">
            <span className="card-tag">
              SHEET <span className="r">·</span> A-002
            </span>
            <div className="card-body">
              <div className="customer">M&amp;M Machine &amp; Fabrication</div>
              <div className="subject">Shop reorder — hex nuts and R2 hose</div>
              <div className="stats-row">
                <div className="stat">
                  2 / 2<span>matched at tier 1</span>
                </div>
                <div className="stat">
                  $127.40<span>committed to ERP</span>
                </div>
              </div>
            </div>
            <div className="verdict">
              <span className="badge">
                <span className="dot" />
                AUTO-APPROVE
              </span>
              <span className="wall">62.5 s · judge pass</span>
            </div>
            <div className="read">
              read sheet <span className="arrow">→</span>
            </div>
          </Link>

          <Link className="card reply" href="/runs/A-003-birch-valley">
            <span className="card-tag">
              SHEET <span className="r">·</span> A-003
            </span>
            <div className="card-body">
              <div className="customer">Birch Valley Farm Equipment</div>
              <div className="subject">Re: Need by tomorrow — Hirshey planter</div>
              <div className="stats-row">
                <div className="stat">
                  PENDING → REVIEW<span>exception advanced</span>
                </div>
                <div className="stat">
                  18 ms<span>reply-check hit</span>
                </div>
              </div>
            </div>
            <div className="verdict">
              <span className="badge">
                <span className="dot" />
                REPLY MERGED
              </span>
              <span className="wall">5.8 s · 11 stages</span>
            </div>
            <div className="read">
              read sheet <span className="arrow">→</span>
            </div>
          </Link>
        </div>

        <div className="cards-captions">
          <div>the refusal — when prices don&apos;t reconcile.</div>
          <div>the commit — when everything reconciles.</div>
          <div>the memory — when an old question gets answered.</div>
        </div>
      </section>

      {/* HOW IT WORKS — § 02, merged: pipeline with R/V/D brackets */}
      <section className="section tight" id="howto">
        <div className="section-head">
          <span className="num">§ 02</span>
          <h2>
            How it works<span className="serif">eleven stages · one wire · same path</span>
          </h2>
          <span className="meta">SEQUENTIAL · ADK</span>
        </div>
        <p className="lede">
          Behind the surface sits a sequential pipeline of eleven stages.{' '}
          <em>Stage ten is a Gemini judge</em> that blocks any outbound email with
          hallucinations or unauthorized commitments before a single byte leaves the building.
        </p>

        <div className="pipeline-diagram">
          <span className="diag-label">PIPELINE · 11 STAGES</span>
          <div className="pipeline-brackets" aria-hidden="true">
            <div className="bracket">
              <span className="bracket-rule" />
              <span className="bracket-label">READ</span>
            </div>
            <div className="bracket">
              <span className="bracket-rule" />
              <span className="bracket-label">VALIDATE</span>
            </div>
            <div className="bracket">
              <span className="bracket-rule" />
              <span className="bracket-label">DECIDE</span>
            </div>
          </div>
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
            <span>
              <span className="swatch" />
              standard stage
            </span>
            <span>
              <span className="swatch gate" />
              quality gate
            </span>
            <span>
              <span className="swatch send" />
              egress · gmail
            </span>
          </div>
        </div>

        <div className="deeper">
          <Link href="/runs/A-002-mm-machine">read the architecture in detail →</Link>
        </div>
      </section>

      {/* CTA BANNER */}
      <section className="cta-banner">
        <div className="inner">
          <div>
            <div className="stamp">— closing brief</div>
            <h2>
              Stop writing bad orders.
              <br />
              <em>Start refusing</em> them.
            </h2>
          </div>
          <div className="actions">
            <Link className="btn" href="/runs/A-001-patterson">
              View the latest run <span className="arrow">→</span>
            </Link>
          </div>
        </div>
      </section>

      {/* FOOTER — one line */}
      <footer className="footer-line">
        <span>Order Intake Agent · v0.6</span>
        <span className="sep">·</span>
        <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
          Source ↗
        </a>
        <span className="sep">·</span>
        <span>Google Solution Hackathon</span>
        <span className="sep">·</span>
        <span>Built on Google ADK · Gemini · Firestore · Pub/Sub</span>
      </footer>
    </main>
  );
}
