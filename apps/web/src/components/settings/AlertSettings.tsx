"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Select } from "@/components/ui/select";
import type { AlertSettings as Settings, AlertSeverity } from "@/lib/types";
import { saveAlerts } from "./alert-actions";

const LEVELS: AlertSeverity[] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

// GitHub Issue alerts for one team. Mount with `initial` from getAlerts(teamId); only team admins can edit.
export function AlertSettings({ teamId, initial }: { teamId: number; initial: Settings }) {
  const [settings, setSettings] = useState(initial);
  const [pending, start] = useTransition();

  const save = (patch: { enabled?: boolean; min_severity?: AlertSeverity }) =>
    start(async () => {
      const r = await saveAlerts(teamId, patch);
      if (!r.ok) return void toast.error(r.error);
      setSettings(r.value);
      toast.success("Alert settings saved");
    });

  const locked = pending || !settings.can_edit;
  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={settings.enabled}
          disabled={locked}
          onChange={(e) => save({ enabled: e.target.checked })}
        />
        Open a GitHub issue when a vulnerability reaches production
      </label>
      <label className="flex items-center gap-2 text-fg2">
        Minimum severity
        <Select
          value={settings.min_severity}
          disabled={locked || !settings.enabled}
          onChange={(e) => save({ min_severity: e.target.value as AlertSeverity })}
        >
          {LEVELS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
      </label>
      {!settings.can_edit && <p className="text-fg2">Only team admins can change alerts.</p>}
    </div>
  );
}
