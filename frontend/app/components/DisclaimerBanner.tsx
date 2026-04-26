export default function DisclaimerBanner() {
  return (
    <div className="prototype-banner" role="note" aria-label="prototype notice">
      <span className="dot" aria-hidden="true">●</span>
      <span className="caps">PROTOTYPE</span>
      <span className="sep">·</span>
      <span>
        data captured from real pipeline runs against{' '}
        <span className="mono">demo-order-intake-local</span>
      </span>
      <span className="sep">·</span>
      <span>view raw JSON for any run</span>
    </div>
  );
}
