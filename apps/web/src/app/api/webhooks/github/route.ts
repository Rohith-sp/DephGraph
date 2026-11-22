import { createHmac, timingSafeEqual } from "node:crypto";
import { after } from "next/server";

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
  const base = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const delivery = req.headers.get("x-github-delivery");
  // GitHub drops a delivery that is not answered within 10 s, but the review takes up to ~30 s and the
  // Python function cannot work after its own response. So acknowledge now and let the FastAPI call
  // finish inside after() (Vercel keeps this function alive for it, up to maxDuration).
  after(async () => {
    try {
      const res = await fetch(`${base}/pr-gate/webhook`, { method: "POST", headers, body });
      if (!res.ok) console.error("pr-gate webhook failed", { delivery, status: res.status, body: await res.text() });
    } catch (e) {
      console.error("pr-gate webhook unreachable", { delivery, error: String(e) });
    }
  });
  return Response.json({ accepted: true, delivery_id: delivery }, { status: 202 });
}
