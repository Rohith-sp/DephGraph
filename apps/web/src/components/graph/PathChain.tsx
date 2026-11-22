// The product's signature: a dependency path as a mono breadcrumb, `service › package version › package version`.
export function PathChain({ steps }: { steps: { name: string; version?: string }[] }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs">
      {steps.map((s, i) => (
        <li key={i} className="flex items-center gap-2">
          {i > 0 && (
            <span aria-hidden className="text-fg3">
              ›
            </span>
          )}
          <span className={i === steps.length - 1 ? "text-fg" : "text-fg2"}>
            {s.name}
            {s.version && <span className="text-fg3"> {s.version}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
