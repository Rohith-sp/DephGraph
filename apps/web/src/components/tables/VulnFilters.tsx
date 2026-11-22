"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ECOSYSTEMS, SEVERITIES } from "@/lib/severity";


export function VulnFilters() {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    router.replace(`${path}?${next}`);
  };

  return (
    <form role="search" className="flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()}>
      <label className="flex flex-col gap-1.5 text-fg2">
        Severity
        <Select value={params.get("severity") ?? ""} onChange={(e) => set("severity", e.target.value)}>
          <option value="">All</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s[0] + s.slice(1).toLowerCase()}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex flex-col gap-1.5 text-fg2">
        Ecosystem
        <Select value={params.get("ecosystem") ?? ""} onChange={(e) => set("ecosystem", e.target.value)}>
          <option value="">All</option>
          {ECOSYSTEMS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex min-w-0 flex-1 flex-col gap-1.5 text-fg2 sm:max-w-xs">
        Search
        <Input
          type="search"
          defaultValue={params.get("q") ?? ""}
          placeholder="Title or ID"
          onKeyDown={(e) => e.key === "Enter" && set("q", e.currentTarget.value.trim())}
          onBlur={(e) => e.currentTarget.value.trim() !== (params.get("q") ?? "") && set("q", e.currentTarget.value.trim())}
        />
      </label>
    </form>
  );
}
