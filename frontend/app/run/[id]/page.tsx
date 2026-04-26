import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getRunIds, loadRun } from '@/app/lib/loadRun';

export function generateStaticParams() {
  return getRunIds().map((id) => ({ id }));
}

export default function LiveRunPage({ params }: { params: { id: string } }) {
  let data;
  try {
    data = loadRun(params.id);
  } catch {
    notFound();
  }

  // Live-run page is Phase B (deferred until its wireframe is drawn).
  // This stub redirects judges directly to the Read Sheet so the CTA
  // on the landing still produces a usable artefact today.
  return (
    <main className="page" id="top">
      <div className="corner-tr" />
      <div className="corner-bl" />
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
        </nav>
        <div className="nav-right">
          <span className="nav-log">Live · WIP</span>
          <Link className="nav-cta" href={`/runs/${params.id}`}>
            Read sheet →
          </Link>
        </div>
      </header>

      <section className="hero">
        <div className="kicker">— Live-run page is Phase B</div>
        <h1>
          The pipeline animates here<span className="slash">.</span>
          <br />
          <em>For now</em>, jump to the read sheet.
        </h1>
        <p className="dek">
          The captured run for <b>{params.id}</b> is{' '}
          <b>{data.total_wall_clock_seconds}s</b> across{' '}
          <b>{data.stage_count} stages</b>. Replay UI is the next milestone.
        </p>
        <div className="ctas">
          <Link className="cta" href={`/runs/${params.id}`}>
            Read the sheet <span className="arrow">→</span>
          </Link>
          <Link className="cta ghost" href="/">
            Back to landing <span className="arrow">↩</span>
          </Link>
        </div>
      </section>
    </main>
  );
}
