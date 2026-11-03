import { Plus } from "lucide-react";

export const FAQS: { q: string; a: string; group: "Getting started" | "Crisis help" | "Caregivers & payment" | "Privacy" }[] = [
  {
    group: "Getting started",
    q: "Who is CareKin for?",
    a: "People living with sickle cell, parents and guardians of children with sickle cell, family members abroad who fund and follow care, and caregivers who want to work with them.",
  },
  {
    group: "Getting started",
    q: "Do I need a referral or a hospital letter?",
    a: "No. Create an account with your phone number, confirm it with the code we text you, and set up your profile and emergency contacts. It takes a few minutes.",
  },
  {
    group: "Getting started",
    q: "Does it cost anything to join?",
    a: "No. Creating an account, the crisis button, the health passport and the facility finder are free. You only pay for caregiver bookings you make.",
  },
  {
    group: "Crisis help",
    q: "What happens when I press the crisis button?",
    a: "Your emergency contacts, guardians and any caregiver on shift get an alert with your location, and you see the nearest verified facility that treats sickle cell, with directions and a call button.",
  },
  {
    group: "Crisis help",
    q: "Do I have to sign in during a crisis?",
    a: "No, if you set up your phone beforehand. Turn on one-tap crisis once while signed in. After that, holding the button on the CareKin home page alerts your family without signing in or waiting for a code.",
  },
  {
    group: "Crisis help",
    q: "Is CareKin an emergency service?",
    a: "No. CareKin alerts the people you trust and points you to care. It does not give medical advice. If breathing is hard or there is chest pain, call 112 straight away.",
  },
  {
    group: "Caregivers & payment",
    q: "How are caregivers checked?",
    a: "Every caregiver's NIN is verified, they give two guarantors, and they go through a background check before CareKin approves them. Nurses must also have a verified licence.",
  },
  {
    group: "Caregivers & payment",
    q: "How does payment work?",
    a: "You pay into escrow when a caregiver accepts your booking. The money is held by a licensed escrow partner and released to the caregiver only after the care is done.",
  },
  {
    group: "Caregivers & payment",
    q: "Can family abroad pay for care?",
    a: "Yes. They can pay with a card from any country. Prices are in naira, and the app shows an estimate in pounds, dollars or euros.",
  },
  {
    group: "Privacy",
    q: "Who can see my health information?",
    a: "Only you, and the people and hospitals you choose to share it with. Your health passport is encrypted, every share has an end date, and you can stop sharing at any time.",
  },
  {
    group: "Privacy",
    q: "How is my consent recorded?",
    a: "Each consent, such as health data or location during a crisis, is recorded under the Nigeria Data Protection Act. You can change any of them in Settings.",
  },
];

/** Question and answer list that opens one item at a time, without JavaScript. */
export function FaqList({ items }: { items: typeof FAQS }) {
  return (
    <ul className="divide-y divide-line overflow-hidden rounded-[28px] border border-line bg-white">
      {items.map(({ q, a }) => (
        <li key={q}>
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-6 py-5 text-[17px] font-semibold marker:hidden [&::-webkit-details-marker]:hidden">
              {q}
              <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-mint text-teal transition-transform duration-300 group-open:rotate-45" aria-hidden>
                <Plus className="size-5" />
              </span>
            </summary>
            <p className="px-6 pb-6 text-[16px] leading-relaxed text-ink/70">{a}</p>
          </details>
        </li>
      ))}
    </ul>
  );
}
