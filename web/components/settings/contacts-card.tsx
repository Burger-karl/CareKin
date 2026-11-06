"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Phone, Plus, Trash2, UsersRound } from "lucide-react";
import { toast } from "sonner";
import { Avatar, toneFor } from "@/components/brand";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { keys, useContacts, useFamilyLinks } from "@/lib/api/queries";
import type { EmergencyContact, FamilyLink } from "@/lib/types";
import { cn, humanize, initials, telHref, toE164 } from "@/lib/utils";

const CHANNEL: Record<EmergencyContact["channel"], string> = { sms: "SMS", whatsapp: "WhatsApp", push: "App notification" };

function useRefresh(patientId: string) {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: keys.contacts(patientId) }),
      qc.invalidateQueries({ queryKey: keys.familyLinks(patientId) }),
    ]);
}

// ------------------------------------------------------------------ access toggles
function AccessControls({ link, patientId }: { link: FamilyLink; patientId: string }) {
  const refresh = useRefresh(patientId);
  const update = useMutation({
    mutationFn: (body: Partial<Pick<FamilyLink, "share_scope" | "can_fund">>) =>
      api(`patients/${patientId}/family-links/${link.id}`, { method: "PATCH", body }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`patients/${patientId}/family-links/${link.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("App access removed.");
      void refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const scope = link.share_scope;
  const rows: [string, boolean, (v: boolean) => void][] = [
    ["Health reports & passport", !!scope.health_data, (v) => update.mutate({ share_scope: { ...scope, health_data: v } })],
    ["Location during a crisis", !!scope.location, (v) => update.mutate({ share_scope: { ...scope, location: v } })],
    ["Can pay for care", link.can_fund, (v) => update.mutate({ can_fund: v })],
  ];
  return (
    <div className="space-y-2.5">
      <ul className="space-y-2">
        {rows.map(([label, value, set]) => (
          <li key={label} className="flex items-center justify-between gap-3">
            <span id={`${link.id}-${label}`} className="text-sm text-slate-700">
              {label}
            </span>
            <Switch checked={value} onCheckedChange={set} aria-labelledby={`${link.id}-${label}`} disabled={update.isPending} />
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => remove.mutate()}
        disabled={remove.isPending}
        className="text-sm font-medium text-crisis-ink hover:underline disabled:opacity-60"
      >
        {remove.isPending ? "Removing…" : "Remove app access"}
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ add / edit dialog
function ContactDialog({
  patientId,
  contact,
  nextPriority,
  open,
  onOpenChange,
}: {
  patientId: string;
  contact: EmergencyContact | null;
  nextPriority: number;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const refresh = useRefresh(patientId);
  const [form, setForm] = useState({ name: "", phone: "", relationship: "", channel: "sms" as EmergencyContact["channel"], priority: 1 });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [lastOpen, setLastOpen] = useState(false);
  // Reset the form each time the dialog opens.
  if (open && !lastOpen) {
    setLastOpen(true);
    setErrors({});
    setForm(
      contact
        ? { name: contact.name, phone: contact.phone, relationship: humanize(contact.relationship), channel: contact.channel, priority: contact.priority }
        : { name: "", phone: "", relationship: "", channel: "sms", priority: nextPriority },
    );
  } else if (!open && lastOpen) {
    setLastOpen(false);
  }

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name.trim(),
        phone: toE164(form.phone),
        relationship: form.relationship.trim().toLowerCase() || "contact",
        channel: form.channel,
        priority: form.priority,
      };
      return contact
        ? api(`patients/${patientId}/emergency-contacts/${contact.id}`, { method: "PATCH", body })
        : api(`patients/${patientId}/emergency-contacts`, { body });
    },
    onSuccess: () => {
      toast.success(contact ? "Contact updated." : "Contact added.");
      void refresh();
      onOpenChange(false);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "validation_error") setErrors(e.fieldErrors);
      else toast.error(errorMessage(e));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!form.name.trim()) next.name = "Add their name.";
    if (!/^\+\d{8,15}$/.test(toE164(form.phone))) next.phone = "Enter a phone number, e.g. 0803 123 4567.";
    setErrors(next);
    if (Object.keys(next).length === 0) save.mutate();
  }

  const set = (k: "name" | "phone" | "relationship") => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{contact ? "Edit contact" : "Add emergency contact"}</DialogTitle>
          <DialogDescription>Alerted with your location when the crisis button is pressed.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Field label="Name" htmlFor="ec-name" error={errors.name}>
            <Input id="ec-name" value={form.name} onChange={set("name")} aria-invalid={!!errors.name} />
          </Field>
          <Field label="Phone" htmlFor="ec-phone" error={errors.phone} hint="The number their CareKin account uses, if they have one.">
            <Input id="ec-phone" type="tel" inputMode="tel" value={form.phone} onChange={set("phone")} placeholder="0803 123 4567" aria-invalid={!!errors.phone} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Relationship" htmlFor="ec-rel" error={errors.relationship}>
              <Input id="ec-rel" value={form.relationship} onChange={set("relationship")} placeholder="Mum" />
            </Field>
            <Field label="Alert by" htmlFor="ec-channel">
              <Select id="ec-channel" value={form.channel} onChange={(e) => setForm((f) => ({ ...f, channel: e.target.value as EmergencyContact["channel"] }))}>
                <option value="sms">SMS</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="push">App notification</option>
              </Select>
            </Field>
          </div>
          <Field label="Alert order" htmlFor="ec-priority" hint="1 is alerted first." error={errors.priority}>
            <Input
              id="ec-priority"
              type="number"
              min={1}
              max={99}
              value={form.priority}
              onChange={(e) => setForm((f) => ({ ...f, priority: Math.max(1, Math.min(99, Number(e.target.value) || 1)) }))}
            />
          </Field>
          <Button type="submit" className="w-full" loading={save.isPending}>
            {contact ? "Save contact" : "Add contact"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ one contact
function ContactTile({
  contact,
  link,
  patientId,
  onEdit,
  onRemove,
}: {
  contact: EmergencyContact;
  link?: FamilyLink;
  patientId: string;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const refresh = useRefresh(patientId);
  const [error, setError] = useState<string | null>(null);
  const grant = useMutation({
    mutationFn: () =>
      api(`patients/${patientId}/family-links`, {
        body: { emergency_contact_id: contact.id, share_scope: { health_data: false, location: false }, can_fund: false },
      }),
    onSuccess: () => {
      setError(null);
      toast.success(`${contact.name} can now follow care in the app. Choose what they see.`);
      void refresh();
    },
    onError: (e) => setError(errorMessage(e)),
  });

  return (
    <li className="flex flex-col rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <Avatar label={initials(contact.name)} tone={toneFor(contact.name)} className="size-10 text-sm" />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 font-[600] text-slate-800">
            {contact.name}
            <span className="text-xs font-medium text-slate-400">#{contact.priority}</span>
          </p>
          <p className="text-sm">{humanize(contact.relationship)}</p>
          <a href={telHref(contact.phone)} className="mt-0.5 inline-flex items-center gap-1 text-sm text-teal hover:underline">
            <Phone className="size-3.5" aria-hidden /> {contact.phone}
          </a>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <Badge variant="neutral" className="text-xs">
            {CHANNEL[contact.channel]}
          </Badge>
          <div className="flex">
            <Button variant="ghost" size="icon" className="size-8" aria-label={`Edit ${contact.name}`} onClick={onEdit}>
              <Pencil className="!size-4" />
            </Button>
            <Button variant="ghost" size="icon" className="size-8 text-crisis-ink hover:bg-crisis-soft" aria-label={`Remove ${contact.name}`} onClick={onRemove}>
              <Trash2 className="!size-4" />
            </Button>
          </div>
        </div>
      </div>

      <div className="mt-4 flex-1 border-t border-slate-100 pt-3">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase text-slate-500">
          <KeyRound className="size-3.5" aria-hidden /> App access
          {link && <Badge variant="success" className="ml-auto px-2 py-0.5 text-[11px] normal-case">On</Badge>}
        </p>
        {link ? (
          <AccessControls link={link} patientId={patientId} />
        ) : (
          <>
            <p className="text-sm">Gets crisis alerts only. With app access they can follow care from their own CareKin account.</p>
            {error && (
              <p role="alert" className="mt-2 text-sm font-medium text-crisis-ink">
                {error}
              </p>
            )}
            <Button variant="secondary" size="sm" className="mt-3" loading={grant.isPending} onClick={() => grant.mutate()}>
              Give app access
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ card
/** Emergency contacts, and the app access each of them has. */
export function ContactsCard({ patientId, className }: { patientId: string; className?: string }) {
  const contacts = useContacts(patientId);
  const links = useFamilyLinks(patientId);
  const refresh = useRefresh(patientId);
  const [editing, setEditing] = useState<EmergencyContact | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [removing, setRemoving] = useState<EmergencyContact | null>(null);

  const list = contacts.data ?? [];
  const byContact = new Map((links.data ?? []).filter((l) => l.emergency_contact_id).map((l) => [l.emergency_contact_id!, l]));
  const others = (links.data ?? []).filter((l) => !l.emergency_contact_id || !list.some((c) => c.id === l.emergency_contact_id));

  const remove = useMutation({
    mutationFn: (c: EmergencyContact) => api(`patients/${patientId}/emergency-contacts/${c.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Contact removed.");
      setRemoving(null);
      void refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  function openDialog(c: EmergencyContact | null) {
    setEditing(c);
    setDialogOpen(true);
  }

  return (
    <Card id="contacts" className={cn("scroll-mt-24", className)}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-full bg-mint text-teal" aria-hidden>
              <UsersRound className="size-4" />
            </span>
            Emergency contacts &amp; family access
          </CardTitle>
          <p className="ck-meta mt-1">Alerted in a crisis, in this order. Give a contact app access to let them follow care from their own CareKin account.</p>
        </div>
        <Button size="sm" onClick={() => openDialog(null)}>
          <Plus /> Add contact
        </Button>
      </div>

      {contacts.isPending ? (
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      ) : list.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 px-5 py-8 text-center">
          <p className="font-medium text-slate-700">No emergency contacts yet</p>
          <p className="mt-1 text-sm">Add someone to alert when the crisis button is pressed.</p>
        </div>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {list.map((c) => (
            <ContactTile key={c.id} contact={c} link={byContact.get(c.id)} patientId={patientId} onEdit={() => openDialog(c)} onRemove={() => setRemoving(c)} />
          ))}
        </ul>
      )}

      {others.length > 0 && (
        <div className="mt-6">
          <p className="mb-1 text-sm font-[600] text-slate-800">Other people with app access</p>
          <p className="ck-meta mb-3">Added before access was linked to contacts. Add them as a contact to get their crisis alerts too.</p>
          <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {others.map((l) => (
              <li key={l.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="mb-3 flex items-center gap-3">
                  <Avatar label={l.member_name ? initials(l.member_name) : "FM"} tone="blue" className="size-10 text-sm" />
                  <div className="min-w-0">
                    <p className="truncate font-[600] text-slate-800">{l.member_name ?? "Family member"}</p>
                    <p className="truncate text-sm">{l.member_phone ?? `ID ending ${l.user_id.slice(-6)}`}</p>
                  </div>
                </div>
                <AccessControls link={l} patientId={patientId} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <ContactDialog
        patientId={patientId}
        contact={editing}
        nextPriority={Math.min(99, Math.max(0, ...list.map((c) => c.priority)) + 1)}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />

      <Dialog open={!!removing} onOpenChange={(v) => !v && setRemoving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {removing?.name}?</DialogTitle>
            <DialogDescription>
              They won&apos;t be alerted in a crisis any more
              {removing && byContact.has(removing.id) ? ", and their app access is removed too." : "."}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => setRemoving(null)}>
              Keep
            </Button>
            <Button variant="crisis" loading={remove.isPending} onClick={() => removing && remove.mutate(removing)}>
              Remove
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
