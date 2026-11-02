import { createHmac, timingSafeEqual } from "node:crypto";

export const runtime = "nodejs";

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
  try {
    const res = await fetch(`${base}/pr-gate/webhook`, { method: "POST", headers, body });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return new Response("upstream unreachable", { status: 502 });
  }
}
