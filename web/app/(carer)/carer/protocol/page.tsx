import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Escalation protocol" };

const STEPS = [
  ["Stay with the person", "Don't leave them alone. Keep them comfortable and calm."],
  ["Call 112 if breathing is hard or there is chest pain", "CareKin is not an emergency service."],
  ["Tell the family or guardian", "Use the numbers on the care plan, or the family's emergency contacts."],
  ["Take them to a verified SCD facility", "The family's crisis screen lists the nearest ones. Ask them to share it with you."],
  ["Write it down", "Add a care note in your shift with what you saw and who you called."],
  ["Report it to CareKin", "If anything felt unsafe, report an incident so a person on our team follows up."],
];

export default function ProtocolPage() {
  return (
    <div className="space-y-5">
      <Link href="/carer" className="text-sm font-semibold text-teal underline">
        ← Back to your shift
      </Link>
      <h1 className="text-[30px] font-bold leading-9">Escalation protocol</h1>
      <p className="rounded-xl bg-crisis-soft p-4 text-[15px]">
        <span className="font-bold text-crisis-ink">We don&apos;t give medical advice.</span> This is who to call and what to
        record. The SCD training in the Academy covers the warning signs.
      </p>
      <ol className="space-y-3">
        {STEPS.map(([t, b], i) => (
          <li key={t} className="ck-card flex gap-4 p-4">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-mint font-bold text-teal">{i + 1}</span>
            <div>
              <p className="text-[17px] font-bold">{t}</p>
              <p className="ck-meta text-[15px]">{b}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
