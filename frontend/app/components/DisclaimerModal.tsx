'use client';

import { useEffect, useState } from 'react';

const STORAGE_KEY = 'oia_disclaimer_v1';

export default function DisclaimerModal() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const dismissed = window.localStorage.getItem(STORAGE_KEY);
    if (!dismissed) setOpen(true);
  }, []);

  const dismiss = () => {
    window.localStorage.setItem(STORAGE_KEY, '1');
    setOpen(false);
  };

  if (!open) return null;

  return (
    <div className="disclaimer-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="disclaimer-title">
      <div className="disclaimer-modal">
        <div className="disclaimer-modal-tag mono caps">prototype · on real data</div>
        <h2 id="disclaimer-title" className="disclaimer-modal-title">
          What you're about to see is real.
        </h2>
        <p className="disclaimer-modal-body">
          Every run on this site is captured from a real pipeline execution against real
          Gemini, LlamaCloud, and the Firestore emulator.
        </p>
        <p className="disclaimer-modal-body">
          The <b>Run the Pipeline</b> button replays a captured run — there is no live
          mailbox listening during this demo.
        </p>
        <button type="button" className="disclaimer-modal-cta" onClick={dismiss}>
          OK — show me the demo.
        </button>
      </div>
    </div>
  );
}
