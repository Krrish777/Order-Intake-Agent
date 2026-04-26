// Shape of a captured run JSON (subset — only fields the UI reads).
// Source of truth: design/wireframes-v2/data/A-00*.json produced by
// scripts/capture_run.py.

export type Verdict = 'ESCALATE' | 'AUTO-APPROVE' | 'REPLY MERGED';

export interface RunStage {
  stage: string;
  action: string;
  outcome: string | null;
  entered_ts: string | null;
  exited_ts: string | null;
  duration_ms: number | null;
  payload: Record<string, unknown>;
}

export interface LifecycleEvent {
  stage: string;
  action: string;
  outcome: string | null;
  ts: string | null;
  payload: Record<string, unknown>;
}

export interface InboundEmail {
  headers: {
    From: string;
    To: string;
    Subject: string;
    Date: string;
    'Message-ID': string;
  };
  body: string;
}

export interface RunData {
  correlation_id: string;
  source_message_id: string | null;
  session_id: string | null;
  agent_version: string | null;
  captured_at: string;
  total_wall_clock_seconds: number | null;
  stage_count: number;
  stages: RunStage[];
  lifecycle_events: LifecycleEvent[];
  orders: Array<Record<string, any>>;
  exceptions: Array<Record<string, any>>;
  inbound_email: InboundEmail | null;
  raw_audit_event_count: number;
}

export function deriveVerdict(data: RunData): Verdict {
  // routing_decided lifecycle event holds the canonical outcome when classification ran
  const routed = data.lifecycle_events.find((e) => e.action === 'routing_decided');
  if (routed?.outcome === 'escalate') return 'ESCALATE';
  if (routed?.outcome === 'auto_approve') return 'AUTO-APPROVE';
  if (routed?.outcome === 'reply_merged' || routed?.outcome === 'reply') return 'REPLY MERGED';

  // REPLY shortcircuit: the run finalised before classification fired, so there is no
  // routing_decided event. Detected by zero orders + zero exceptions + a reply_shortcircuit
  // stage that ran successfully.
  const ran = (stage: string) => data.stages.some((s) => s.stage === stage);
  if (
    data.orders.length === 0 &&
    data.exceptions.length === 0 &&
    ran('reply_shortcircuit_stage')
  ) {
    return 'REPLY MERGED';
  }

  // Fallback by persisted artefacts.
  if (data.orders.length > 0 && data.exceptions.length === 0) return 'AUTO-APPROVE';
  return 'ESCALATE';
}

export function deriveCustomerName(data: RunData): string {
  const fromOrder = data.orders[0]?.customer?.name;
  if (fromOrder) return String(fromOrder);
  const fromException = data.exceptions[0]?.customer?.name;
  if (fromException) return String(fromException);
  // REPLY runs shortcircuit before customer resolution — fall back to the
  // sender's display name from the inbound .eml.
  const from = data.inbound_email?.headers.From ?? '';
  const displayName = from.match(/^([^<]+)</)?.[1].trim();
  if (displayName) return displayName;
  const bareEmail = from.match(/<([^>]+)>/)?.[1] ?? from.trim();
  return bareEmail || 'Unknown customer';
}
