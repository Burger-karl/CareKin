"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { MapPin, Phone, ShieldCheck, TriangleAlert, Zap } from "lucide-react";
import { FacilityCard, alertLabel } from "@/components/crisis/crisis-screen";
import { Button } from "@/components/ui/button";
import { ApiError, errorMessage } from "@/lib/api/client";
import { quickPosition } from "@/lib/crisis";
import {
  cancelDeviceEvent,
  clearDeviceEvent,
  getDeviceEvent,
  getDeviceEventId,
  getDevicePending,
  isRetryable,
  logAnonymousPress,
  sendDevicePress,
  startDevicePress,
  type DeviceEvent,
} from "@/lib/device-crisis";
import { cn, telHref } from "@/lib/utils";

const HOLD_MS = 2000;
const COUNTDOWN_S = 5;
const EMERGENCY = "112";

type Phase =
  | { kind: "idle" }
  | { kind: "countdown"; left: number }
  | { kind: "sending"; offline: boolean }
  | { kind: "active"; event: DeviceEvent }
  | { kind: "closed"; message: string }
  | { kind: "help" };

const isKeyGone = (e: unknown) => e instanceof ApiError && e.status === 401;

function CallButton({ className }: { className?: string }) {
  return (
    <Button asChild size="lg" variant="crisis" className={cn("h-14 w-full text-[17px]", className)}>
      <a href={telHref(EMERGENCY)}>
        <Phone /> Call {EMERGENCY} now
      </a>
    </Button>
  );
}

/**
 * The crisis button on the landing page, for everyone.
 * A phone that was set up while signed in sends a real alert: hold for 2 seconds, then a
 * 5 second countdown to cancel, then family and caregiver are alerted with the location.
 * Any other phone gets the emergency number and the nearest facility, and the press is
 * logged for ops. Nobody is alerted from a phone we can't tie to a patient.
 */
export function LandingCrisis({ enrolled: enrolledAtLoad, firstName }: { enrolled: boolean; firstName: string | null }) {
  const [enrolled, setEnrolled] = useState(enrolledAtLoad);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [hold, setHold] = useState(0); // 0..1 while the button is held
  const [notice, setNotice] = useState<string | null>(null);
  const holdStart = useRef<number | null>(null);
  const raf = useRef<number | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---- sending, with retries until it gets through
  const send = useCallback(async () => {
    const press = getDevicePending();
    if (!press) return;
    setPhase({ kind: "sending", offline: false });
    try {
      const event = await sendDevicePress(press);
      setPhase({ kind: "active", event });
    } catch (e) {
      if (isKeyGone(e)) {
        clearDeviceEvent();
        setEnrolled(false);
        setNotice("This phone can no longer send alerts without signing in. Call 112, then sign in to alert your family.");
        setPhase({ kind: "help" });
      } else if (isRetryable(e)) {
        setPhase({ kind: "sending", offline: e instanceof ApiError && e.status === 0 });
        retryTimer.current = setTimeout(() => void send(), 5000);
      } else {
        setNotice(errorMessage(e));
        setPhase({ kind: "help" });
      }
    }
  }, []);

  // ---- pick up where we left off: a press not yet sent, or a crisis still open
  useEffect(() => {
    if (!enrolledAtLoad) return;
    if (getDevicePending()) {
      void send();
      return;
    }
    const id = getDeviceEventId();
    if (!id) return;
    getDeviceEvent(id)
      .then((event) => {
        if (event.status === "active") setPhase({ kind: "active", event });
        else clearDeviceEvent();
      })
      .catch((e) => {
        if (isKeyGone(e)) setEnrolled(false);
      });
  }, [enrolledAtLoad, send]);

  useEffect(() => {
    const online = () => {
      if (getDevicePending()) void send();
    };
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("online", online);
      if (retryTimer.current) clearTimeout(retryTimer.current);
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [send]);

  // ---- keep alert statuses fresh while the crisis is open
  const activeId = phase.kind === "active" ? phase.event.id : null;
  useEffect(() => {
    if (!activeId) return;
    const t = setInterval(() => {
      getDeviceEvent(activeId)
        .then((event) => {
          if (event.status === "active") setPhase({ kind: "active", event });
          else {
            clearDeviceEvent();
            setPhase({ kind: "closed", message: "This crisis has been closed." });
          }
        })
        .catch(() => undefined);
    }, 10_000);
    return () => clearInterval(t);
  }, [activeId]);

  // ---- countdown, then record and send
  const countingDown = phase.kind === "countdown";
  useEffect(() => {
    if (phase.kind !== "countdown") return;
    if (phase.left <= 0) {
      void startDevicePress().then(() => send());
      return;
    }
    const t = setTimeout(() => setPhase({ kind: "countdown", left: phase.left - 1 }), 1000);
    return () => clearTimeout(t);
  }, [phase, countingDown, send]);

  // ---- hold to press
  function startHold() {
    if (phase.kind !== "idle") return;
    if (!enrolled) {
      openHelp();
      return;
    }
    holdStart.current = performance.now();
    const tick = () => {
      if (holdStart.current === null) return;
      const p = Math.min(1, (performance.now() - holdStart.current) / HOLD_MS);
      setHold(p);
      if (p >= 1) {
        holdStart.current = null;
        setHold(0);
        navigator.vibrate?.(80);
        setPhase({ kind: "countdown", left: COUNTDOWN_S });
        return;
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }

  function endHold() {
    holdStart.current = null;
    if (raf.current) cancelAnimationFrame(raf.current);
    setHold(0);
  }

  function openHelp() {
    setPhase({ kind: "help" });
    void quickPosition().then((pos) =>
      logAnonymousPress(
        pos ? { latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy_m: Math.round(pos.coords.accuracy) } : undefined,
      ),
    );
  }

  async function cancelSent(event: DeviceEvent) {
    try {
      await cancelDeviceEvent(event.id);
      clearDeviceEvent();
      setPhase({ kind: "closed", message: "Alert cancelled. We've marked it as a false alarm." });
    } catch (e) {
      setNotice(errorMessage(e));
    }
  }

  // ---- views
  const ring = 2 * Math.PI * 104;

  return (
    <div className="flex flex-col items-center text-center">
      {(phase.kind === "idle" || phase.kind === "countdown") && (
        <>
          <button
            type="button"
            onPointerDown={startHold}
            onPointerUp={endHold}
            onPointerLeave={endHold}
            onPointerCancel={endHold}
            onKeyDown={(e) => {
              // Holding a key is awkward: the keyboard goes straight to the cancellable countdown.
              if ((e.key === "Enter" || e.key === " ") && phase.kind === "idle") {
                e.preventDefault();
                if (enrolled) setPhase({ kind: "countdown", left: COUNTDOWN_S });
                else openHelp();
              }
            }}
            onContextMenu={(e) => e.preventDefault()}
            disabled={phase.kind === "countdown"}
            aria-label={
              enrolled
                ? `Crisis. Hold for 2 seconds to alert ${firstName ?? "the patient"}'s family and caregiver.`
                : "Emergency help. Shows the emergency number and the nearest facility."
            }
            className="relative flex size-[232px] touch-none select-none items-center justify-center rounded-full bg-[#FCE7E8] transition-transform active:scale-[0.98]"
          >
            <span aria-hidden className="absolute inset-[14px] animate-halo rounded-full bg-[#F8D3D5]" />
            <svg aria-hidden viewBox="0 0 232 232" className="absolute inset-0 -rotate-90">
              <circle
                cx="116"
                cy="116"
                r="104"
                fill="none"
                stroke="#A82A31"
                strokeWidth="8"
                strokeLinecap="round"
                strokeDasharray={ring}
                strokeDashoffset={ring * (1 - hold)}
                className={hold > 0 ? "opacity-100" : "opacity-0"}
              />
            </svg>
            <span className="relative flex size-[172px] flex-col items-center justify-center rounded-full bg-crisis text-white shadow-[0_8px_24px_rgba(214,56,64,0.35)]">
              {phase.kind === "countdown" ? (
                <>
                  <span className="text-[56px] font-bold leading-none">{phase.left}</span>
                  <span className="mt-1 text-[14px]">Sending alert…</span>
                </>
              ) : (
                <>
                  <Zap className="mb-1.5 size-8" strokeWidth={1.75} aria-hidden />
                  <span className="text-[22px] font-bold tracking-wide">CRISIS</span>
                  <span className="mt-0.5 text-[13px]">{enrolled ? (hold > 0 ? "Keep holding…" : "Hold 2 seconds") : "Get help now"}</span>
                </>
              )}
            </span>
          </button>

          {phase.kind === "countdown" ? (
            <Button size="lg" variant="outline" className="mt-4 h-12 w-full max-w-xs bg-white" onClick={() => setPhase({ kind: "idle" })}>
              Cancel
            </Button>
          ) : (
            <p className="mt-3 max-w-xs text-[14px] leading-snug text-foreground/80">
              {enrolled ? (
                <>
                  One-tap crisis is on for <span className="font-semibold">{firstName ?? "this phone"}</span>. Alerts family and
                  caregiver with your location. No sign-in needed.
                </>
              ) : (
                <>Shows the emergency number and the nearest hospital that treats sickle cell.</>
              )}
            </p>
          )}
        </>
      )}

      {phase.kind === "sending" && (
        <div className="w-full max-w-md space-y-3" aria-live="assertive">
          <div className="ck-card border-[#F3B8BB] bg-white p-5">
            <p className="text-[19px] font-bold text-crisis-ink">
              {phase.offline ? "No connection. Your alert is saved." : "Sending your alert…"}
            </p>
            <p className="ck-meta mt-1">
              {phase.offline ? "It goes out the moment you're back online. Call 112 now if you can." : "Alerting family and caregiver with your location."}
            </p>
          </div>
          <CallButton />
        </div>
      )}

      {phase.kind === "active" && (
        <div className="w-full max-w-md space-y-3 text-left" aria-live="polite">
          <div className="ck-card border-[#F3B8BB] bg-white p-5">
            <p className="flex items-center gap-2 text-[19px] font-bold text-crisis-ink">
              <ShieldCheck className="size-5" aria-hidden /> Alert sent for {phase.event.patient_first_name}
            </p>
            {phase.event.no_contacts ? (
              <p className="mt-2 text-body text-crisis-ink">
                There are no contacts to alert. Call {phase.event.emergency_number} now.
              </p>
            ) : (
              <ul className="mt-3 space-y-1.5">
                {phase.event.alerts.map((a) => {
                  const l = alertLabel(a);
                  return (
                    <li key={a.id} className="flex items-center justify-between gap-3 text-[15px]">
                      <span className="truncate">{a.recipient_name}</span>
                      <span className={cn("shrink-0 font-semibold", l.tone === "ok" ? "text-teal" : l.tone === "bad" ? "text-crisis" : "text-amber-ink")}>
                        {l.text}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
            {phase.event.location.source !== "device" && (
              <p className="ck-meta mt-2 flex items-center gap-1.5">
                <MapPin className="size-4" aria-hidden />
                {phase.event.location.source === "home" ? "No GPS: your home address was shared." : "No location could be shared."}
              </p>
            )}
          </div>
          <CallButton />
          {phase.event.nearby?.facilities.slice(0, 2).map((f, i) => <FacilityCard key={f.id} f={f} primary={i === 0} />)}
          <div className="flex flex-col items-center gap-2 pt-1">
            <button type="button" className="text-[15px] font-semibold text-crisis-ink underline" onClick={() => void cancelSent(phase.event)}>
              I pressed it by mistake
            </button>
            <Link href="/login" className="text-[14px] text-teal underline">
              Sign in to see everything and close the crisis
            </Link>
          </div>
        </div>
      )}

      {phase.kind === "help" && (
        <div className="w-full max-w-md space-y-3" aria-live="polite">
          {notice && (
            <p className="flex items-start gap-2 rounded-xl bg-amber-soft p-3 text-left text-[14px] text-amber-ink">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> {notice}
            </p>
          )}
          <CallButton />
          <Button asChild size="lg" variant="secondary" className="h-14 w-full text-[17px]">
            <Link href="/find-care">
              <MapPin /> Find the nearest SCD facility
            </Link>
          </Button>
          <Button asChild size="lg" variant="outline" className="h-12 w-full bg-white">
            <Link href="/login">Sign in to alert your family</Link>
          </Button>
          <p className="ck-meta text-left">
            After you sign in, turn on one-tap crisis for this phone. Next time, holding this button alerts your family and
            caregiver straight away, without signing in.
          </p>
          <button type="button" className="text-[14px] text-muted-foreground underline" onClick={() => setPhase({ kind: "idle" })}>
            Back
          </button>
        </div>
      )}

      {phase.kind === "closed" && (
        <div className="w-full max-w-md space-y-3">
          <p className="ck-card bg-white p-4 text-[16px] font-semibold">{phase.message}</p>
          <button type="button" className="text-[14px] text-teal underline" onClick={() => setPhase({ kind: "idle" })}>
            Done
          </button>
        </div>
      )}
    </div>
  );
}
