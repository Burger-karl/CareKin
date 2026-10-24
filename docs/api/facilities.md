# Facility finder API contract (FF-1, FF-2, FF-3)

This contract is for the web and mobile tracks.
- Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
- Live, always-current schemas: `http://localhost:8000/docs`.

The finder helps a family choose a facility before they go. It lists only
listings that CareKin ops verified. Each listing shows when it was last checked
(`last_verified_at`, FF-3).

## `GET /facilities` (public, no login)

| Query | Meaning |
| --- | --- |
| `service` | Repeat for several: `?service=haematologist&service=blood_bank`. A facility must offer **all** of them, verified by ops and available now. Values: `haematologist`, `emergency_24h`, `transfusion`, `blood_bank`, `genotype_testing`. |
| `hmo` | Only facilities that accept this HMO, e.g. `Hygeia HMO`. Case and extra spaces don't matter; the whole name must match. |
| `q` | Text in the name or address (2 to 100 characters). |
| `latitude`, `longitude` | Send both, or neither. With them, results are nearest first and carry `distance_km`, and only facilities with an ops-confirmed location are included. |
| `radius_km` | Up to 500. Needs `latitude` and `longitude`. Without it, every match is returned, nearest first. |
| `limit`, `offset` | Paging. `limit` is 1 to 100 (default 20). |

- Every filter given must match.
- Without a location, results are sorted by name.
- The total number of matches is in the `X-Total-Count` header. Use it for "Showing 20
  of 34".
- An unknown query parameter (a typo such as `servce`) returns `422`, so a filter is
  never silently ignored.

**Response:** `200`, a list of results.

```jsonc
[
  {
    "facility": {
      "id": "uuid",
      "name": "UNTH Ituku-Ozalla",
      "address": "Ituku-Ozalla, Enugu",
      "phone_number": "+234...",
      "accepted_hmos": ["Hygeia HMO", "NHIA"],
      "latitude": 6.4413, "longitude": 7.4988,
      "verification_status": "verified",
      "location_verified": true,
      "last_verified_at": "2026-10-20T09:12:00Z",  // show on every listing (FF-3)
      "created_at": "...", "updated_at": "..."
    },
    "distance_km": 1.11,  // null when no location was sent
    "services": [         // only verified and available services
      {"id": "uuid", "service_type": "blood bank", "availability": "available",
       "verification_status": "verified", "...": "..."}
    ]
  }
]
```

## HMOs on a listing

`POST /facilities` and `PATCH /facilities/{id}` take `accepted_hmos`: a list of HMO
names.
- Up to 50 names, each 2 to 100 characters.
- Names are trimmed and their spaces collapsed. Repeats are dropped, ignoring case; the
  first spelling is kept.
- On `PATCH`, the list replaces the old one. Send `[]` to clear it; `null` is refused.
- `GET /facilities/{id}` and `GET /facilities/nearby` now include `accepted_hmos`.

## Service names (for the facility portal)

Service types are free text. When a facility adds a service, use the first name in each
row so the filters always find it. The other names are also recognised, as are
`_` or `-` in place of spaces.

| Filter | Recommended name | Also recognised |
| --- | --- | --- |
| `haematologist` | `haematologist` | haematology, hematologist, hematology |
| `emergency_24h` | `emergency 24h` | 24h emergency, 24 hour emergency, 24 hours emergency, emergency 24 hours, 24/7 emergency |
| `transfusion` | `transfusion` | blood transfusion |
| `blood_bank` | `blood bank` | |
| `genotype_testing` | `genotype testing` | genotype test, genotyping, Hb electrophoresis, haemoglobin electrophoresis, hemoglobin electrophoresis |

A plain "emergency" doesn't count as 24-hour emergency.

## Screens

- **FacilityFinder:**
  - filter chips for the five services and an HMO picker;
  - "near me" (sends the location);
  - result cards with the distance, services, HMOs, the last-verified date, and one-tap
    call.
- Show a clear empty state ("No verified facility matches all your filters") with a
  button to remove the last filter.
