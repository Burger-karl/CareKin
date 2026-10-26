"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Avatar } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage } from "@/lib/api/client";
import { keys, useFamilyLinks } from "@/lib/api/queries";
import type { FamilyLink } from "@/lib/types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function LinkRow({ link, patientId }: { link: FamilyLink; patientId: string }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: keys.familyLinks(patientId) });
  const update = useMutation({
    mutationFn: (body: Partial<Pick<FamilyLink, "share_scope" | "can_fund">>) =>
      api(`patients/${patientId}/family-links/${link.id}`, { method: "PATCH", body }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`patients/${patientId}/family-links/${link.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Access removed.");
      void refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const scope = link.share_scope;
  const rows: [string, boolean, (v: boolean) => void][] = [
    ["Health reports & passport", !!scope.health_data, (v) => update.mutate({ share_scope: { ...scope, health_data: v } })],
    ["Location during a crisis", !!scope.location, (v) => update.mutate({ share_scope: { ...scope, location: v } })],
    ["Can fund care", link.can_fund, (v) => update.mutate({ can_fund: v })],
  ];
  return (
    <li className="space-y-3 rounded-xl border-[1.5px] border-line p-4">
      <div className="flex items-center gap-3">
        <Avatar label="FM" tone="blue" className="size-10 text-sm" />
        <div className="min-w-0 flex-1">
          <p className="font-bold">Family member</p>
          <p className="ck-meta truncate">ID ending {link.user_id.slice(-6)}</p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Remove access"
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
        >
          <Trash2 />
        </Button>
      </div>
      <ul className="space-y-2">
        {rows.map(([label, value, set]) => (
          <li key={label} className="flex items-center justify-between gap-3">
            <span id={`${link.id}-${label}`} className="text-body">
              {label}
            </span>
            <Switch checked={value} onCheckedChange={set} aria-labelledby={`${link.id}-${label}`} disabled={update.isPending} />
          </li>
        ))}
      </ul>
    </li>
  );
}

/** Add, change or remove the family members who follow this person's care. */
export function FamilyDialog({
  patientId,
  open,
  onOpenChange,
}: {
  patientId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const qc = useQueryClient();
  const links = useFamilyLinks(patientId);
  const [userId, setUserId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api(`patients/${patientId}/family-links`, {
        body: { user_id: userId.trim(), share_scope: { health_data: false, location: false }, can_fund: false },
      }),
    onSuccess: () => {
      setUserId("");
      toast.success("Family member added. Choose what they can see below.");
      void qc.invalidateQueries({ queryKey: keys.familyLinks(patientId) });
    },
    onError: (e) => setError(errorMessage(e)),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!UUID.test(userId.trim())) return setError("That doesn't look like a Care Kin ID. Ask them to copy it from their sign-up screen.");
    add.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Family access</DialogTitle>
          <DialogDescription>You decide what each person can see. Remove them any time.</DialogDescription>
        </DialogHeader>

        <ul className="mb-5 space-y-3">
          {links.data?.map((l) => <LinkRow key={l.id} link={l} patientId={patientId} />)}
          {links.data?.length === 0 && <li className="text-body text-muted-foreground">No family members yet.</li>}
        </ul>

        <form onSubmit={submit} className="space-y-3" noValidate>
          <Field label="Add by Care Kin ID" htmlFor="family-id" error={error} hint="They can copy their ID when they sign up as family.">
            <Input
              id="family-id"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="e.g. 3f2b…"
              aria-invalid={!!error}
              autoComplete="off"
            />
          </Field>
          <Button type="submit" className="w-full" loading={add.isPending}>
            Add family member
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
