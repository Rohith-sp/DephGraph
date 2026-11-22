import { createHmac, timingSafeEqual } from "node:crypto";
import { after } from "next/server";
import { report } from "@/lib/sentry";
import { apiBaseUrl } from "@/lib/runtime";

export const runtime = "nodejs";
export const maxDuration = 300; // Hobby maximum; covers the whole after() review

function validSignature(body: string, header: string | null, secret: string): boolean {
  if (!header) return false;
  const expected = Buffer.from(`sha256=${createHmac("sha256", secret).update(body).digest("hex")}`);
  const given = Buffer.from(header);
  return given.length === expected.length && timingSafeEqual(given, expected);
}

export async function POST(req: Request) {
  const secret = process.env.GITHUB_WEBHOOK_SECRET;
  if (!secret) return new Response("webhook not configured", { status: 500 });

  const body = await req.text(); // raw bytes: HMAC must cover exactly what GitHub signed
  const signature = req.headers.get("x-hub-signature-256");
  if (!validSignature(body, signature, secret)) return new Response("invalid signature", { status: 401 });

  const headers = new Headers({ "content-type": req.headers.get("content-type") ?? "application/json" });
  for (const h of ["x-github-event", "x-hub-signature-256", "x-github-delivery"]) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const base = apiBaseUrl();
  const delivery = req.headers.get("x-github-delivery");
  // GitHub drops a delivery that is not answered within 10 s, but the review takes up to ~30 s and the
  // Python function cannot work after its own response. So acknowledge now and let the FastAPI call
  // finish inside after() (Vercel keeps this function alive for it, up to maxDuration).
  after(() => forward(`${base}/pr-gate/webhook`, headers, body, delivery));
  return Response.json({ accepted: true, delivery_id: delivery }, { status: 202 });
}

// FastAPI stores a delivery before reviewing it, so once it has answered nothing is lost (a failed
// review is retried by its cron sweeper). Only a delivery that never reaches it is at risk, hence
// the retries on network errors and 5xx; GitHub's "Redeliver" is the last resort and is idempotent.
async function forward(url: string, headers: Headers, body: string, delivery: string | null) {
  let failure = "";
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt) await new Promise((r) => setTimeout(r, 2000 * 4 ** (attempt - 1))); // 2 s, then 8 s
    try {
      const res = await fetch(url, { method: "POST", headers, body });
      if (res.ok) return;
      failure = `status ${res.status}: ${await res.text()}`;
      if (res.status < 500) break; // rejected, not unavailable: retrying cannot help
    } catch (e) {
      failure = String(e);
    }
  }
  console.error("pr-gate webhook failed", { delivery, failure });
  await report(new Error("pr-gate webhook forward failed"), { delivery, failure });
}
