/**
 * CareKin's own contact details, defined once. This is for questions and support,
 * never for emergencies: the crisis flow and 112 links stay separate.
 */
export const CONTACT = {
  email: "info@carekin.com.ng",
  phone: "+2348083201909",
  address: "714 Ekulu West, GRA, Enugu, Nigeria",
} as const;

export const CONTACT_MAILTO = `mailto:${CONTACT.email}`;
export const CONTACT_TEL = `tel:${CONTACT.phone}`;
