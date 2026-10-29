// Response shapes of the CareKin API (see docs/api/*.md and backend/app/domains/*/schemas.py).

export type UserRole = "patient" | "guardian" | "family_abroad" | "caregiver" | "facility_admin" | "ops";
export type ConsentType = "terms" | "health_data" | "location" | "llm_processing";

export interface User {
  id: string;
  phone: string;
  email: string | null;
  full_name: string;
  role: UserRole;
  preferred_language: "en" | "ig" | "pcm";
  phone_verified: boolean;
  is_active: boolean;
  created_at: string;
}

export interface Me extends User {
  consents: {
    terms: boolean;
    health_data: boolean;
    location: boolean;
    llm_processing: boolean;
    policy_version: string;
    missing_required: ConsentType[];
  };
}

export interface GeoPoint {
  latitude: number;
  longitude: number;
}

export interface Patient {
  id: string;
  user_id: string | null;
  full_name: string;
  date_of_birth: string;
  age: number;
  is_minor: boolean;
  sex: string | null;
  genotype: string | null;
  blood_group: string | null;
  home_location: GeoPoint | null;
  created_at: string;
  access: "self" | "guardian" | "family";
}

export interface EmergencyContact {
  id: string;
  patient_id: string;
  name: string;
  phone: string;
  relationship: string;
  channel: "sms" | "whatsapp" | "push";
  priority: number;
}

export interface CareProfile {
  id: string;
  patient_id: string;
  known_triggers: string | null;
  medications_overview: string | null;
  allergies: string | null;
  care_instructions: string | null;
  primary_facility_id: string | null;
  updated_at: string;
}

export interface FamilyLink {
  id: string;
  patient_id: string;
  user_id: string;
  share_scope: { health_data?: boolean; location?: boolean };
  can_fund: boolean;
  created_at: string;
}

// ---- facilities
export interface Facility {
  id: string;
  name: string;
  address: string;
  phone_number: string;
  accepted_hmos: string[];
  latitude: number | null;
  longitude: number | null;
  verification_status: string;
  location_verified: boolean;
  last_verified_at: string | null;
}

export interface FacilityService {
  id: string;
  service_type: string;
  availability: string;
  verification_status: string;
}

export interface FacilityResult {
  facility: Facility;
  distance_km: number | null;
  services: FacilityService[];
}

// ---- crisis
export type AlertStatus = "queued" | "sent" | "delivered" | "failed" | "skipped";

export interface CrisisAlert {
  id: string;
  recipient_type: string;
  recipient_name: string;
  phone: string;
  channel: "sms" | "whatsapp" | "push";
  status: AlertStatus;
  attempts: number;
  sent_at: string | null;
}

export interface CrisisFacility {
  id: string;
  name: string;
  facility_type: string | null;
  address: string;
  latitude: number;
  longitude: number;
  distance_m: number;
  call_number: string | null;
  phone: string | null;
  emergency_phone: string | null;
  crisis_services: string[];
  verification_status: string;
  last_verified_at: string | null;
  directions_url: string;
}

export interface CrisisFacilities {
  crisis_event_id: string;
  location_source: "device" | "home" | "none";
  radius_km: number | null;
  widened_search: boolean;
  facilities_available: boolean;
  facilities: CrisisFacility[];
  disclaimer: string;
  emergency_number: string;
}

export interface CrisisEvent {
  id: string;
  patient_id: string;
  client_event_id: string;
  status: "active" | "resolved" | "cancelled";
  triggered_at: string;
  resolved_at: string | null;
  outcome_facility_id: string | null;
  location: { source: "device" | "home" | "none"; latitude: number | null; longitude: number | null; stale: boolean };
  alerts: CrisisAlert[];
  no_contacts: boolean;
  disclaimer: string;
  emergency_number: string;
  nearby?: CrisisFacilities;
}

// ---- passport
export type PassportEntryType = "crisis" | "trigger" | "visit" | "transfusion" | "medication";

export interface PassportEntry {
  id: string;
  patient_id: string;
  entry_type: PassportEntryType;
  occurred_at: string;
  summary: string;
  details: string | null;
  facility_id: string | null;
  facility_name: string | null;
  crisis_event_id: string | null;
  created_at: string;
}

export interface Passport {
  patient: {
    id: string;
    full_name: string;
    date_of_birth: string;
    sex: string | null;
    genotype: string | null;
    blood_group: string | null;
  };
  profile: {
    known_triggers: string | null;
    medications_overview: string | null;
    allergies: string | null;
    care_instructions: string | null;
  } | null;
  entries: PassportEntry[];
  total_entries: number;
  access: { via: string; can_manage: boolean };
  disclaimer: string;
}

export interface PassportShare {
  id: string;
  patient_id: string;
  facility_id: string;
  facility_name: string | null;
  entry_types: PassportEntryType[];
  include_profile: boolean;
  expires_at: string;
  revoked_at: string | null;
  created_at: string;
  status: "active" | "expired" | "revoked";
}

// ---- caregivers
export type CaregiverTier = "companion" | "trained_carer" | "licensed_nurse";
export type BookingType = "hourly" | "daily" | "overnight" | "live_in";

export interface CaregiverMatch {
  caregiver_id: string;
  full_name: string;
  tier: CaregiverTier;
  languages: string[];
  years_experience: number | null;
  hourly_rate_minor: number;
  avg_rating: string | number | null;
  rating_count: number;
  distance_km: number;
  languages_matched: string[];
  estimated_total_minor: number;
  score: number;
}

export interface UnavailableCaregiver {
  caregiver_id: string;
  full_name: string;
  tier: CaregiverTier;
  reason: "busy" | "outside_hours";
}

export interface MatchResult {
  patient_id: string;
  guardian_approval_required: boolean;
  min_tier_applied: CaregiverTier | null;
  results: CaregiverMatch[];
  /** Absent on a backend that predates the field. */
  unavailable?: UnavailableCaregiver[];
}

export interface CaregiverPublic {
  id: string;
  full_name: string;
  tier: CaregiverTier;
  bio: string | null;
  languages: string[];
  years_experience: number | null;
  hourly_rate_minor: number | null;
  service_radius_km: number | null;
  avg_rating: string | number | null;
  rating_count: number;
  certifications: { cert_type: string; title: string; issuer: string | null; expires_at: string | null }[];
}

export interface CaregiverSelf extends CaregiverPublic {
  user_id: string;
  base_latitude?: number | null;
  base_longitude?: number | null;
  status: string;
  approved_at: string | null;
}

// ---- bookings
export type BookingStatus =
  | "requested"
  | "pending_guardian"
  | "accepted"
  | "declined"
  | "paid"
  | "in_progress"
  | "completed"
  | "cancelled"
  | "disputed";

export interface Booking {
  id: string;
  patient_id: string;
  caregiver_id: string;
  booked_by: string | null;
  booking_type: BookingType;
  status: BookingStatus;
  starts_at: string;
  ends_at: string;
  service_location: GeoPoint | null;
  service_address: string | null;
  guardian_present: boolean;
  rate_minor: number;
  total_minor: number;
  currency: string;
  created_at: string;
  access: "patient_side" | "caregiver" | "ops";
}

export type ShiftStatus = "scheduled" | "in_progress" | "completed" | "missed" | "cancelled";

export interface Shift {
  id: string;
  booking_id: string;
  scheduled_start: string;
  scheduled_end: string;
  actual_start: string | null;
  actual_end: string | null;
  status: ShiftStatus;
}

export interface CheckIn {
  id: string;
  shift_id: string;
  kind: "check_in" | "check_out";
  location: GeoPoint;
  distance_m: number;
  within_geofence: boolean;
  client_timestamp: string;
  received_at: string;
}

export interface CareNote {
  id: string;
  shift_id: string;
  author_id: string | null;
  body: string;
  created_at: string;
}

// ---- payments
export type EscrowStatus = "pending" | "held" | "on_hold_dispute" | "released" | "refunded";

export interface Payment {
  id: string;
  booking_id: string;
  gateway: "paystack" | "stripe";
  reference: string;
  amount_minor: number;
  currency: string;
  status: string;
  paid_at: string | null;
  needs_review: boolean;
  created_at: string;
}

export interface Escrow {
  id: string;
  booking_id: string;
  status: EscrowStatus;
  amount_minor: number;
  currency: string;
  held_at: string | null;
  released_at: string | null;
}

export interface BookingMoney {
  booking_id: string;
  booking_status: string;
  escrow: Escrow | null;
  payments: Payment[];
}

export interface Checkout {
  payment: Payment;
  authorization_url: string | null;
  escrow_status: EscrowStatus;
  reused: boolean;
}

export interface PaymentVerify {
  outcome: "pending" | "failed" | "funded" | "needs_review" | "already_processed" | "funding_retry";
  payment: Payment;
  escrow_status: EscrowStatus | null;
}

// ---- incidents
export type IncidentCategory = "safety" | "neglect" | "conduct" | "payment" | "other";
export type Severity = "low" | "medium" | "high" | "critical";
export type IncidentStatus = "open" | "investigating" | "resolved" | "dismissed";

export interface Incident {
  id: string;
  booking_id: string | null;
  reported_by: string | null;
  category: IncidentCategory;
  severity: Severity;
  is_dispute: boolean;
  status: IncidentStatus;
  description: string;
  response_due_at: string;
  first_response_at: string | null;
  assigned_to: string | null;
  resolved_at: string | null;
  resolution: string | null;
  created_at: string;
}

// ---- ops
export interface OpsDashboard {
  generated_at: string;
  period_days: number;
  caregivers: { by_status: Record<string, number>; awaiting_decision: number };
  facilities: { by_status: Record<string, number>; verification_queue: number; stale: number };
  bookings: { by_status: Record<string, number>; created_in_period: number; completed_in_period: number };
  incidents: { open: number; overdue: number; open_disputes: number };
  crisis: { active: number; in_period: number };
}

export interface PendingCaregiver {
  id: string;
  full_name: string;
  tier: CaregiverTier;
  status: string;
  waiting_days: number;
  guarantor_count: number;
  checks: { nin: string | null; background: string | null };
  certifications: { total: number; pending: number; verified: number; rejected: number };
  ready_to_approve: boolean;
  blockers: string[];
}

export interface PendingFacility {
  id: string;
  name: string;
  address: string;
  phone_number: string;
  verification_status: string;
  has_location: boolean;
  location_verified: boolean;
  last_verified_at: string | null;
  services: { id: string; service_type: string; availability: string; verification_status: string }[];
  reasons: string[];
}

export interface AuditEntry {
  id: number;
  created_at: string;
  actor_name: string | null;
  actor_role: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  ip_address: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  next_before_id: number | null;
}

export interface Review {
  id: string;
  booking_id: string;
  shift_id: string;
  reviewer_id: string;
  reviewee_id: string;
  direction: "family_to_caregiver" | "caregiver_to_family";
  rating: number;
  comment: string | null;
  created_at: string;
}

export interface GuardianApproval {
  id: string;
  booking_id: string;
  guardian_id: string;
  decision: "approved" | "rejected";
  reason: string | null;
  decided_at: string;
}
