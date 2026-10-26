"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, MapPin, Navigation, Phone, ShieldCheck, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/components/session/session";
import { ResolveDialog } from "@/components/crisis/resolve-dialog";
import { CrisisButton } from "@/components/crisis/crisis-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, api, errorMessage } from "@/lib/api/client";
import { useContacts } from "@/lib/api/queries";
import {
  clearActive,
  contactsForSnapshot,
  getActiveEventId,
  getPending,
  getSnapshot,
  isRetryable,
  saveSnapshot,
  sendPress,
  serviceLabel,
} from "@/lib/crisis";
import { cn, formatDate, telHref } from "@/lib/utils";
import type { AlertStatus, CrisisAlert, CrisisEvent, CrisisFacility } from "@/lib/types";

const DEFAULT_EMERGENCY = "112";

function elapsed(since: string, now: number) {
  const s = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
  if (s < 60) return `${s} seconds`;
  const m = Math.floor(s / 60);
  return `${m} min ${s % 60} s`;
}

function alertLabel(a: CrisisAlert): { text: string; tone: "ok" | "wait" | "bad" } {
  const via = a.channel === "sms" ? " · SMS" : a.channel === "whatsapp" ? " · WhatsApp" : "";
  const map: Record<AlertStatus, { text: string; tone: "ok" | "wait" | "bad" }> = {
    queued: { text: "Sending…", tone: "wait" },
    sent: { text: `Sent${via}`, tone: "ok" },
    delivered: { text: `Delivered${via}`, tone: "ok" },
    failed: { text: "Not delivered", tone: "bad" },
    skipped: { text: "Skipped", tone: "wait" },
  };
  return map[a.status];
}

function FacilityCard({ f, primary }: { f: CrisisFacility; primary?: boolean }) {
  const km = (f.distance_m / 1000).toFixed(1);
  const tags = f.crisis_services.map((s) => serviceLabel[s] ?? s);
  const call = f.call_number ?? f.phone;
  return (
    <div className={cn("ck-card p-5", !primary && "p-4")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className={cn("font-bold", primary ? "text-[22px] leading-7" : "text-[17px]")}>{f.name}</h3>
          <p className="ck-meta mt-1 text-[15px]">{km} km away</p>
        </div>
      </div>
      <ul className="mt-3 flex flex-wrap gap-2">
        {tags.map((t) => (
          <li key={t}>
            <Badge>{t}</Badge>
          </li>
        ))}
      </ul>
      {f.last_verified_at && (
        <p className="mt-3 flex items-center gap-1.5 text-meta text-teal">
          <ShieldCheck className="size-4" aria-hidden /> Verified {formatDate(f.last_verified_at)}
        </p>
      )}
      <div className="mt-4 grid grid-cols-2 gap-3">
        {call ? (
          <Button asChild size="lg">
            <a href={telHref(call)}>
              <Phone /> Call
            </a>
          </Button>
        ) : (
          <Button size="lg" disabled>
            <Phone /> Call
          </Button>
        )}
        <Button asChild size="lg" variant="secondary">
          <a href={f.directions_url} target="_blank" rel="noopener noreferrer">
            <Navigation /> Directions
          </a>
        </Button>
      </div>
    </div>
  );
}

function SafetyNote({ number }: { number: string }) {
  return (
    <div className="rounded-xl bg-crisis-soft p-5">
      <p className="font-bold text-crisis-ink">CareKin is not an emergency service.</p>
      <p className="mt-1 text-body text-foreground/90">
        We don&apos;t give medical advice. If breathing is hard or there is chest pain, call{" "}
        <a href={telHref(number)} className="font-bold underline">
          {number}
        </a>{" "}
        now.
      </p>
    </div>
  );
}

/** Nothing running: offer the button and what's saved on this phone. */
function Launcher({ patientId }: { patientId: string }) {
  const snapshot = getSnapshot();
  const number = snapshot?.emergencyNumber ?? DEFAULT_EMERGENCY;
  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col gap-6 px-4 py-6">
      <Link href="/dashboard" className="text-sm font-semibold text-teal underline">
        ← Back to home
      </Link>
      <h1 className="text-title font-bold">Crisis mode</h1>
      <CrisisButton patientId={patientId} className="py-4" />
      {snapshot && snapshot.patientId === patientId && snapshot.facilities.length > 0 && (
        <section aria-labelledby="saved-heading" className="space-y-3">
          <h2 id="saved-heading" className="text-[17px] font-bold">
            Nearest facilities saved on this phone
          </h2>
          {snapshot.facilities.slice(0, 2).map((f) => (
            <FacilityCard key={f.id} f={f} />
          ))}
        </section>
      )}
      <div className="mt-auto">
        <SafetyNote number={number} />
      </div>
    </div>
  );
}

export function CrisisScreen() {
  const router = useRouter();
  const qc = useQueryClient();
  const { patient } = useSession();
  const patientId = patient?.id;
  const contacts = useContacts(patientId);

  const [eventId, setEventId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [online, setOnline] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const [resolveOpen, setResolveOpen] = useState(false);
  const firstEvent = useRef<CrisisEvent | null>(null);

  // Work out where we are: a crisis already open, a press waiting to be sent, or nothing.
  useEffect(() => {
    const active = getActiveEventId();
    const p = getPending();
    if (p && p.patientId === patientId) setPending(true);
    else if (active) setEventId(active);
    setReady(true);
    setOnline(navigator.onLine);
  }, [patientId]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  // Send the stored press. If there's no connection or the server is down, try again.
  const attempting = useRef(false);
  const attempt = useCallback(async () => {
    const p = getPending();
    if (!p || attempting.current) return;
    attempting.current = true;
    try {
      const event = await sendPress(p);
      firstEvent.current = event;
      qc.setQueryData(["crisis-event", event.id], event);
      setEventId(event.id);
      setPending(false);
    } catch (e) {
      if (!isRetryable(e)) {
        clearActive();
        setPending(false);
        toast.error(errorMessage(e));
        if (e instanceof ApiError && e.status === 403) router.replace("/dashboard");
      }
    } finally {
      attempting.current = false;
    }
  }, [qc, router]);

  useEffect(() => {
    if (!pending) return;
    void attempt();
    const t = setInterval(() => void attempt(), 5000);
    return () => clearInterval(t);
  }, [pending, attempt, online]);

  const event = useQuery({
    queryKey: ["crisis-event", eventId],
    queryFn: () => api<CrisisEvent>(`crisis/events/${eventId}`),
    enabled: !!eventId,
    refetchInterval: (q) => (q.state.data?.status === "active" || !q.state.data ? 4000 : false),
    initialData: firstEvent.current ?? undefined,
    retry: false,
  });

  const facilities = useQuery({
    queryKey: ["crisis-facilities", eventId],
    queryFn: () => api<NonNullable<CrisisEvent["nearby"]>>(`crisis/events/${eventId}/facilities`),
    enabled: !!eventId && !firstEvent.current?.nearby,
    staleTime: 60_000,
  });

  const data = event.data;
  const nearby = data?.nearby ?? facilities.data;
  const snapshot = getSnapshot();
  const usingSaved = !nearby && !!snapshot && snapshot.patientId === patientId;
  const list: CrisisFacility[] = nearby?.facilities ?? (usingSaved ? snapshot.facilities : []);
  const number = data?.emergency_number ?? snapshot?.emergencyNumber ?? DEFAULT_EMERGENCY;

  // Keep contacts on the phone for the offline view.
  useEffect(() => {
    if (!patientId || !contacts.data) return;
    const prev = getSnapshot();
    saveSnapshot({
      patientId,
      savedAt: new Date().toISOString(),
      facilities: prev?.patientId === patientId ? prev.facilities : [],
      contacts: contactsForSnapshot(contacts.data),
      emergencyNumber: prev?.emergencyNumber ?? DEFAULT_EMERGENCY,
    });
  }, [patientId, contacts.data]);

  const resend = useMutation({
    mutationFn: () => api("crisis/alert", { body: { crisis_event_id: eventId } }),
    onSuccess: () => {
      toast.success("Trying again.");
      void event.refetch();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  // The crisis ended elsewhere (resolved by a guardian, or cancelled): leave crisis mode.
  useEffect(() => {
    if (data && data.status !== "active") clearActive();
  }, [data]);

  if (!patient || !ready) return null;
  if (!pending && !eventId) return <Launcher patientId={patient.id} />;

  const closed = data && data.status !== "active";
  const failed = data?.alerts.filter((a) => a.status === "failed").length ?? 0;
  const started = data?.triggered_at ?? getPending()?.triggeredAt ?? new Date(now).toISOString();
  const loc = data?.location.source;

  return (
    <div className="min-h-dvh bg-background pb-8">
      <header className="rounded-b-[32px] bg-crisis px-4 pb-6 pt-5 text-white">
        <div className="mx-auto max-w-md">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[15px] font-semibold" aria-live="off">
              Crisis mode, {elapsed(started, now)}
            </p>
            <Button variant="white" size="sm" className="h-11 px-5 text-[15px]" onClick={() => setResolveOpen(true)} disabled={!data}>
              I&apos;m safe now
            </Button>
          </div>
          <div className="mt-5 flex items-center gap-3">
            <span className="flex size-12 shrink-0 items-center justify-center rounded-full bg-white/20" aria-hidden>
              <span className="size-4 rounded-full bg-white" />
            </span>
            <h1 className="text-[28px] font-bold leading-8">
              {pending
                ? "Sending your alert…"
                : loc === "device"
                  ? "Your live location is being shared"
                  : loc === "home"
                    ? "Your home location is being shared"
                    : "Alert sent without a location"}
            </h1>
          </div>

          <div className="mt-5 rounded-xl bg-white p-4 text-foreground" aria-live="polite">
            {pending ? (
              <p className="text-body">
                {online
                  ? "Connecting to CareKin…"
                  : "No connection. Your alert is saved on this phone and will send as soon as you're back online."}
              </p>
            ) : data?.no_contacts ? (
              <div className="space-y-2">
                <p className="font-bold text-crisis-ink">Nobody could be alerted.</p>
                <p className="text-body">
                  You have no emergency contacts yet. Call{" "}
                  <a className="font-bold underline" href={telHref(number)}>
                    {number}
                  </a>{" "}
                  now, then add contacts so help can reach you next time.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-line">
                {data?.alerts.map((a) => {
                  const l = alertLabel(a);
                  return (
                    <li key={a.id} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                      <span className="text-[16px]">
                        {a.recipient_name}
                        {a.recipient_type === "guardian" && (
                          <span className="text-muted-foreground"> · guardian</span>
                        )}
                      </span>
                      <span
                        className={cn(
                          "text-[15px] font-bold",
                          l.tone === "ok" && "text-teal",
                          l.tone === "wait" && "text-muted-foreground",
                          l.tone === "bad" && "text-crisis-ink",
                        )}
                      >
                        {l.text}
                      </span>
                    </li>
                  );
                }) ?? <li className="text-body">Alerting your contacts…</li>}
              </ul>
            )}
            {failed > 0 && (
              <Button
                variant="crisis-outline"
                size="sm"
                className="mt-3 w-full"
                onClick={() => resend.mutate()}
                loading={resend.isPending}
              >
                Resend to {failed} {failed === 1 ? "contact" : "contacts"}
              </Button>
            )}
          </div>
          {data?.location.stale && (
            <p className="mt-3 flex items-center gap-1.5 text-sm text-white/90">
              <MapPin className="size-4" aria-hidden /> Your last GPS fix is a few minutes old.
            </p>
          )}
        </div>
      </header>

      <main className="mx-auto mt-6 max-w-md space-y-4 px-4">
        {!online && (
          <p className="flex items-center gap-2 rounded-lg bg-amber-soft px-3 py-2 text-meta font-semibold text-amber-ink">
            <WifiOff className="size-4" aria-hidden /> Offline. Showing what&apos;s saved on this phone.
          </p>
        )}
        {closed && (
          <p className="rounded-lg bg-mint px-3 py-2 text-meta font-semibold text-teal">
            This crisis is {data.status === "cancelled" ? "cancelled" : "closed"}.{" "}
            <Link href="/dashboard" className="underline">
              Back to home
            </Link>
          </p>
        )}

        <h2 className="text-[19px] font-bold">Nearest facility that treats SCD</h2>

        {list.length > 0 ? (
          <>
            <FacilityCard f={list[0]} primary />
            {list.slice(1, 4).map((f) => (
              <a
                key={f.id}
                href={f.directions_url}
                target="_blank"
                rel="noopener noreferrer"
                className="ck-card flex items-center justify-between gap-3 p-4 hover:border-[#9DC9C3]"
              >
                <span>
                  <span className="block text-[17px] font-bold">{f.name}</span>
                  <span className="ck-meta text-[15px]">
                    {(f.distance_m / 1000).toFixed(1)} km
                    {f.crisis_services[0] ? ` · ${serviceLabel[f.crisis_services[0]] ?? f.crisis_services[0]}` : ""}
                  </span>
                </span>
                <ChevronRight className="size-5 text-muted-foreground" aria-hidden />
              </a>
            ))}
            {nearby?.widened_search && (
              <p className="ck-meta">Nothing verified was close, so we searched a wider area.</p>
            )}
          </>
        ) : (
          <div className="ck-card space-y-3 p-5">
            <p className="text-body">
              {pending || facilities.isLoading
                ? "Finding the nearest facility…"
                : "We couldn't find a verified SCD facility near you. Go to the nearest hospital or call the emergency number."}
            </p>
            <Button asChild size="lg" variant="crisis" className="w-full">
              <a href={telHref(number)}>
                <Phone /> Call {number}
              </a>
            </Button>
          </div>
        )}

        {!online && snapshot?.contacts.length ? (
          <section className="ck-card p-4" aria-label="Emergency contacts saved on this phone">
            <h3 className="mb-2 font-bold">Your contacts</h3>
            <ul className="divide-y divide-line">
              {snapshot.contacts.map((c) => (
                <li key={c.phone} className="flex items-center justify-between py-2">
                  <span>
                    {c.name} <span className="text-muted-foreground">· {c.relationship}</span>
                  </span>
                  <a href={telHref(c.phone)} className="font-semibold text-teal underline">
                    Call
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <div className="pt-6">
          <SafetyNote number={number} />
        </div>
        {(usingSaved || list.length > 0) && snapshot && (
          <p className="text-center text-meta text-muted-foreground">
            Works offline · facilities &amp; contacts saved on this phone
          </p>
        )}
      </main>

      {data && (
        <ResolveDialog
          open={resolveOpen}
          onOpenChange={setResolveOpen}
          event={data}
          facilities={list}
          onDone={() => {
            clearActive();
            qc.invalidateQueries({ queryKey: ["passport"] });
            router.replace("/dashboard");
          }}
        />
      )}
    </div>
  );
}
