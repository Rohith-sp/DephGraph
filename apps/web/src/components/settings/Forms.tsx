"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import {
  addService,
  createTeam,
  createToken,
  inviteMember,
  removeInvite,
  removeMember,
  revokeToken,
  setMemberRole,
} from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { Result } from "@/lib/run";

// Runs an action, toasts the outcome, refreshes server data on success.
function useAction() {
  const router = useRouter();
  const [pending, start] = useTransition();
  const act = (fn: () => Promise<Result<unknown>>, done: string | ((v: unknown) => string), then?: (v: unknown) => void) =>
    start(async () => {
      const r = await fn();
      if (!r.ok) return void toast.error(r.error);
      toast.success(typeof done === "function" ? done(r.value) : done);
      then?.(r.value);
      router.refresh();
    });
  return { pending, act };
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

export function CreateTeam() {
  const { pending, act } = useAction();
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const name = String(new FormData(form).get("name"));
        act(() => createTeam(name, slugify(name)), "Team created", () => form.reset());
      }}
    >
      <label className="flex flex-col gap-1.5 text-fg2">
        Team name
        <Input name="name" required maxLength={120} className="w-64" />
      </label>
      <Button type="submit" disabled={pending}>
        Create team
      </Button>
    </form>
  );
}

export function InviteForm({ teamId }: { teamId: number }) {
  const { pending, act } = useAction();
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const f = new FormData(form);
        act(() => inviteMember(teamId, String(f.get("email")), String(f.get("role"))), "Invite saved", () =>
          form.reset(),
        );
      }}
    >
      <label className="flex flex-col gap-1.5 text-fg2">
        Invite by email
        <Input name="email" type="email" required className="w-64" />
      </label>
      <label className="flex flex-col gap-1.5 text-fg2">
        Role
        <Select name="role" defaultValue="member">
          <option value="member">member</option>
          <option value="admin">admin</option>
        </Select>
      </label>
      <Button type="submit" disabled={pending}>
        Invite
      </Button>
    </form>
  );
}

export function RemoveInvite({ teamId, email }: { teamId: number; email: string }) {
  const { pending, act } = useAction();
  return (
    <Button variant="ghost" size="sm" disabled={pending} onClick={() => act(() => removeInvite(teamId, email), "Invite removed")}>
      Cancel
    </Button>
  );
}

export function MemberControls({ teamId, userId, role }: { teamId: number; userId: string; role: string }) {
  const { pending, act } = useAction();
  return (
    <div className="flex gap-2">
      <Select
        aria-label="Role"
        value={role}
        disabled={pending}
        onChange={(e) => act(() => setMemberRole(teamId, userId, e.target.value), "Role updated")}
      >
        <option value="member">member</option>
        <option value="admin">admin</option>
      </Select>
      <Button variant="ghost" size="sm" disabled={pending} onClick={() => act(() => removeMember(teamId, userId), "Member removed")}>
        Remove
      </Button>
    </div>
  );
}

function importMessage(status: string | null | undefined, repo: string) {
  switch (status) {
    case "imported":
    case "unchanged":
      return "Service added, dependencies imported";
    case "not_installed":
      return `Service added. Install the DephGraph GitHub App on ${repo} to import dependencies`;
    case "failed":
      return "Service added. The import failed and will retry on the next push";
    default:
      return "Service added";
  }
}

export function AddServiceForm({ teams }: { teams: { team_id: number; name: string }[] }) {
  const { pending, act } = useAction();
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const f = new FormData(form);
        const repo = String(f.get("repo"));
        act(
          () => addService(Number(f.get("team")), repo, String(f.get("name")), String(f.get("root"))),
          (v) => importMessage((v as { import_status?: string | null }).import_status, repo),
          () => form.reset(),
        );
      }}
    >
      <label className="flex flex-col gap-1.5 text-fg2">
        Team
        <Select name="team" className="w-48">
          {teams.map((t) => (
            <option key={t.team_id} value={t.team_id}>
              {t.name}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex flex-col gap-1.5 text-fg2">
        GitHub repository URL
        <Input name="repo" required placeholder="https://github.com/owner/repo" className="w-80" />
      </label>
      <label className="flex flex-col gap-1.5 text-fg2">
        Directory (monorepo)
        <Input name="root" placeholder="optional, e.g. apps/api" className="w-48" />
      </label>
      <label className="flex flex-col gap-1.5 text-fg2">
        Name
        <Input name="name" placeholder="optional" className="w-40" />
      </label>
      <Button type="submit" disabled={pending}>
        Add service
      </Button>
    </form>
  );
}

export function TokenForm({ serviceId }: { serviceId: number }) {
  const { pending, act } = useAction();
  const [secret, setSecret] = useState("");
  return (
    <div className="space-y-3">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const form = e.currentTarget;
          const name = String(new FormData(form).get("name"));
          act(
            () => createToken(serviceId, name),
            "Token created",
            (v) => {
              setSecret((v as { token: string }).token);
              form.reset();
            },
          );
        }}
      >
        <label className="flex flex-col gap-1.5 text-fg2">
          Token name
          <Input name="name" required maxLength={80} placeholder="e.g. production collector" className="w-64" />
        </label>
        <Button type="submit" disabled={pending}>
          Create token
        </Button>
      </form>
      {secret && (
        <p role="status" className="break-all rounded-lg border border-line bg-raised p-3 font-mono text-xs">
          Copy it now, it is shown once: {secret}
        </p>
      )}
    </div>
  );
}

export function RevokeToken({ serviceId, tokenId }: { serviceId: number; tokenId: number }) {
  const { pending, act } = useAction();
  return (
    <Button variant="ghost" size="sm" disabled={pending} onClick={() => act(() => revokeToken(serviceId, tokenId), "Token revoked")}>
      Revoke
    </Button>
  );
}
