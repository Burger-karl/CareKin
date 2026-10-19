# Health passport API contract (Day 9)

This contract is for the web and mobile tracks.
- Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
- Live, always-current schemas: `http://localhost:8000/docs`.

The passport is the patient's own record of crises, triggers, hospital visits,
transfusions and medicines (HP-1).
- The patient controls who sees it (HP-3).
- Every view is logged (HP-4).
- It exports as a one-page PDF for a doctor (HP-2).
- CareKin never gives medical advice. Show `disclaimer` wherever the passport is shown.

## Who can do what

| | Add entry | Read / export | Share / list shares / revoke |
| --- | --- | --- | --- |
| Patient (own record) | yes | yes | yes |
| Guardian | yes | yes | yes |
| Family member, `health_data` shared | no | yes | no |
| Family member, not shared | no | 403 `health_data_not_shared` | no |
| Facility admin, active share to their facility | no | only the shared types (and profile, if included) | no |
| Facility admin, share revoked / expired | no | 403 `share_revoked` / `share_expired` | no |
| Caregiver, ops, anyone else | 404 | 404 | 404 |

Adding an entry needs the writer's `health_data` consent. Without it the API returns
`403 consent_required`; send the user to `/auth/consent`.

## Endpoints

| Method | Path | Body / query | Success |
| --- | --- | --- | --- |
| POST | `/passport/{patient_id}/entries` | `{entry_type, occurred_at, summary, details?, facility_id?}` | 201 entry |
| GET | `/passport/{patient_id}` | `?entry_type=&since=&limit=&offset=` | 200 `{patient, profile, entries, total_entries, access, disclaimer}` |
| GET | `/passport/{patient_id}/export` | | 200 `application/pdf` (one page) |
| POST | `/passport/{patient_id}/share` | `{facility_id, entry_types?, include_profile?, expires_in_hours?}` | 201 share |
| GET | `/passport/{patient_id}/shares` | | 200 every share, with `status` |
| DELETE | `/passport/{patient_id}/share/{share_id}` | | 200 share (`status: revoked`) |
| GET | `/passport/shares/received` | facility admins | 200 active shares to your facilities |

**Entry fields**
- `entry_type`: `crisis`, `trigger`, `visit`, `transfusion` or `medication`.
- `occurred_at`: needs a timezone. It can't be in the future, or before the patient was
  born.
- `summary`: one line, up to 500 characters.
- `details`: optional, up to 5,000 characters.

Both text fields are encrypted at rest.

**Shares**
- A share can only go to a CareKin-verified facility. Otherwise you get `422 facility_not_verified`.
- `expires_in_hours` runs from 1 to 720 (30 days). The default is 72.
- `entry_types` defaults to all types.
- `include_profile` (default `true`) also shares the genotype, blood group, allergies,
  triggers and medicines list.
- Only one share per facility can be active. A second one gets `409 share_exists`;
  revoke it first to change the scope.
- Each share records its own consent (`consent_type: passport_share`). Revoking the share
  revokes that consent too.

**Export**
- Always exactly one page, newest entries first.
- The `X-Entries-Shown` and `X-Entries-Total` headers say how many entries fit. Tell the
  user "N earlier entries are in the app" when they differ.
- The response is never cached (`Cache-Control: no-store`), and the file name never
  contains the patient's name.

## Screens

- **PassportViewer:** entries with type filters, and the disclaimer.
- **ExportButton:** downloads the PDF.
- **Share management:**
  - pick a verified facility, the types to share, and how long;
  - list the shares with their status;
  - a revoke button on each share.
- **Facility portal:** the "Shared with us" list comes from `GET /passport/shares/received`.
