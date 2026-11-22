import Link from "next/link";

export default function NotFound() {
  return (
    <div className="max-w-xl space-y-4">
      <h1>Not found</h1>
      <p className="text-fg2">This page or record does not exist.</p>
      <Link href="/services" className="inline-block text-accent underline-offset-4 hover:underline">
        Back to services
      </Link>
    </div>
  );
}
