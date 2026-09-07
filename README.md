# Vendor-Lifecycle-Onboarding-Offboarding-Process
Vendor Lifecycle Management is a generic Frappe app built by Auriga IT on ERPNext v16 to streamline vendor onboarding and offboarding. It provides a structured, auditable workflow for KYC, compliance, document management, eSign, and vendor lifecycle management, replacing manual, email-based processes.

## Why this matters

Standard ERPNext gives every vendor a bare Supplier record — a name, a group, some contact fields — with no structured way to prove a vendor was actually vetted before you started buying from them, or to enforce that vetting consistently across every vendor added to the system.

Today, the workarounds are either:
- Onboarding vendors directly into the Supplier master with no gating at all — a Purchase Order can go out to a vendor whose PAN/GSTIN was never validated, whose bank details were never checked, and who never passed a compliance audit or background check.
- Running the actual vetting process outside ERPNext entirely — email threads, spreadsheets, Google Forms — with someone manually copying the outcome into the Supplier master days or weeks later, and no record left behind of who approved what.

This app closes that gap by putting the entire onboarding pipeline — Request → KYC → Background Check → Compliance Audit → Sampling Evaluation → Sign-off → Active — inside ERPNext itself, with each stage's mandatory-ness and ordering configurable per business (not hardcoded), and a mirrored, enforced deboarding pipeline that cuts off a vendor's ability to receive new orders the moment it's no longer approved.

The operational payoff: onboarding a vendor stops depending on someone remembering to follow the checklist. PAN/GSTIN get format- and (where India Compliance is installed) government-validated automatically. A Background Check that fails its rating threshold disables the Supplier or blocks it from ever going live, instead of quietly sitting unresolved. A deboarded vendor is hard-blocked from new Purchase Orders and RFQs, with any of its open transactions still surfaced so the business can close them out cleanly instead of discovering them after the fact.

Two things worth flagging before adopting this on a live site:

- **Existing Suppliers created before this app was installed** have no Vendor KYC or stage history at all. Nothing in this app retroactively fills that gap, so any dashboard or report that assumes every Supplier has a lifecycle status will show blanks for vendors onboarded before adoption.
- **The pipeline adds real process overhead.** A team used to typing a Supplier name and moving on now goes through Onboarding Request → KYC → (optionally) Background Check → Compliance Audit → Sampling → Sign-off before a vendor is fully Active. For low-risk or low-value vendors this can be a genuine slow-down — tune which stages are mandatory in Vendor Lifecycle Settings (Background Check / Compliance Audit / Sampling Evaluation can each be turned off) rather than running the full pipeline for every vendor by default.

## Documentation gaps

- **Cancel/amend behavior**: cancelling a Vendor KYC reverts its own status to Draft. Each of the four stage doctypes (Background Check, Compliance Audit, Sampling Evaluation, Sign Off) reverts the disable it itself caused on cancel — but only if it was the one responsible; if another still-submitted Failed/Rejected record for the same vendor exists, the Supplier correctly stays disabled. None of these doctypes support amendment (`allow_amend` is off across the board) — a cancelled stage document must be replaced with a fresh one rather than an amended copy, so there's no "amended stage re-enters the pipeline" scenario to account for.
- **Version compatibility**: built and tested against ERPNext v16; not verified against v14 or v15.
- **Reporting**: the app ships four built-in reports — Vendor Onboarding Pipeline (every vendor's status across all four stages in one table), Vendor Deboarding Pipeline, Vendor Support Ticket Aging, and Vendors Due for Re-Audit — plus dashboards (cards + charts) for Onboarding, Ongoing Engagement, and Deboarding. Anything beyond these (e.g. cross-vendor trend analysis over time) would need a custom report, same as any Frappe app.
- **Multi-company behavior**: does vendor lifecycle status and enforcement apply per Company, or is it global across companies on a multi-company site?
- **Screenshots**: a walkthrough of the Onboarding Request → KYC → Sign-off flow, and of the GSTIN address-fetch step, would help evaluators see the UI impact without installing it first.
