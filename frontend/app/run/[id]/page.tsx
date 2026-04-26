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
    <main className="page">
      <div className="corner-tr" />
      <div className="corner-bl" />
      <div className="top-bar">
        <div className="left">
          <span>Order Intake Agent</span>
          <span className="stamp">
            <span className="pulse" />LIVE · TRACK A · v0.4
          </span>
        </div>
        <div className="center">SHEET R-{params.id} · LIVE-RUN · WIP</div>
        <div className="right" />
      </div>

      <section className="hero" style={{ paddingTop: '8vh' }}>
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
