/**
 * The engine lives behind HTTP. This app proposes and renders verdicts; it
 * holds no policy, no Razorpay key, and no opinion about what is allowed.
 */
import Constants from 'expo-constants';

/** The simulator reaches the host machine on localhost; a device needs the LAN IP. */
const HOST_FROM_DEV_SERVER = Constants.expoConfig?.hostUri?.split(':')[0];
export const API_BASE =
  process.env.EXPO_PUBLIC_API_BASE ??
  (HOST_FROM_DEV_SERVER ? `http://${HOST_FROM_DEV_SERVER}:8117` : 'http://127.0.0.1:8117');

export type Verdict = 'ALLOW' | 'CLARIFY' | 'ESCALATE' | 'DENY';

export type Reason = { code: string; detail: string };

export type Decision = {
  verdict: Verdict;
  reason_code: string;
  reasons: Reason[];
  cart_id: string;
  real_total_paise: number;
  claimed_total_paise: number;
  idempotency_key: string;
  order_id: string | null;
  key_id: string | null;
};

export type AgentStep = {
  tool: 'search_catalog' | 'create_cart' | 'request_charge';
  args: Record<string, unknown>;
  result: Record<string, unknown>;
};

/** What the agent did, and what the engine made of it. */
export type AgentTurn = {
  said: string;
  steps: AgentStep[];
  decision: Decision | null;
};

export type LedgerEntry = {
  seq: number;
  ts: string;
  verdict?: Verdict;
  reason_code?: string;
  total_paise?: number;
  event?: string;
  razorpay_payment_id?: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body?.detail ?? `${response.status} ${response.statusText}`);
  return body as T;
}

/**
 * Hand an instruction to the buyer agent. `adversarial` swaps in an agent that
 * is working against the account holder — it changes what the agent *tries*,
 * never what the engine permits, which is the only reason it is safe to ship
 * a button for it.
 */
export const runAgent = (text: string, adversarial = false) =>
  request<AgentTurn>('/api/agent', {
    method: 'POST',
    body: JSON.stringify({ text, adversarial }),
  });

export const getLedger = () =>
  request<{ chain_intact: boolean; entries: LedgerEntry[] }>('/api/ledger');

export const rupees = (paise: number) =>
  `₹${(paise / 100).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
