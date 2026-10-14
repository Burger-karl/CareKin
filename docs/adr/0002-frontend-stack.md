# ADR 0002: Next.js for web and React Native (Expo) for mobile

- **Status:** Proposed (confirm at stand-up; it changes PRD section 10.1)
- **Date:** 2026-10-14
- **Owner:** Web and mobile leads
- **PRD refs:** 9.3, 10.1

## Context

The PRD (section 10.1) lists **React** for the web apps (operations console, facility
portal) and **Flutter** for the patient and caregiver mobile apps. The Engineering
Kickoff, and the scaffold already in this repo, use **Next.js 14** for web and
**React Native with Expo** for mobile. Build plan Day 1 asks for this conflict to be
settled before any frontend code is written.

What the mobile app has to do (PRD 9.3, kickoff Topic 8):
- run well on low-end Android phones;
- work offline for caregivers, and queue crisis presses until the connection returns;
- make the crisis flow fast, with as few taps as possible;
- ship fixes quickly during a 3-day pilot.

## Decision

Keep the kickoff's stack:

1. **Web: Next.js 14** (App Router, TypeScript, Tailwind, shadcn/ui). It is React, so it
   matches the PRD's web choice, with routing and server rendering built in.
2. **Mobile: React Native with Expo** (expo-router), instead of Flutter.
   - One language (TypeScript) across web and mobile: shared API types, validation
     rules and client code, and anyone on the team can pick up either app.
   - Expo's over-the-air updates let us fix the pilot app without a Play Store review.
   - Offline queueing is covered (AsyncStorage now; SQLite if the queue grows).
   - The scaffold (route groups, CrisisButton, offline service) is already written this way.
3. Both apps call the same FastAPI `/api/v1`, using its OpenAPI schema as the contract.

## Consequences

- PRD section 10.1 should be updated to say Expo, not Flutter.
- Flutter is generally faster on low-end devices, so the mobile team sets a performance
  budget on Day 2 (cold start, crisis-screen render, bundle size). Each build is checked
  on a real low-end Android phone. If the budget can't be met, this ADR is revisited.
- Web and mobile can share a generated API client (e.g. from `/openapi.json`) later.
