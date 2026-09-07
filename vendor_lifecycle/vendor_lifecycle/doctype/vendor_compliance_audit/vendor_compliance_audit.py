# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import difflib

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.stage_sequencing import (
	enforce_sequential_creation,
	force_override_stage,
	require_no_active_document_for_kyc,
)
from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	get_kyc_vendor_contact,
	mark_vendor_status_in_progress,
	resolve_vendor_lifecycle_company_name,
	send_vendor_lifecycle_email,
	sync_onboarding_request_field,
	sync_vendor_field,
)

DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Passed"
DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Failed"

VENDOR_LIFECYCLE_STATUS_AUDIT_VERIFIED = "Audit Verified"
VENDOR_LIFECYCLE_STATUS_COMPLIANCE_AUDIT_IN_PROGRESS = "Compliance Audit In Progress"


class VendorComplianceAudit(Document):
	def before_insert(self):
		require_no_active_document_for_kyc(self)

	@frappe.whitelist()
	def load_checklist_from_template(self):
		"""Populate checklist_items from checklist_template, replacing any existing rows."""
		if not self.checklist_template:
			frappe.throw(frappe._("Set a Checklist Template first."))

		template = frappe.get_doc("Audit Checklist Template", self.checklist_template)
		self.checklist_items = []
		for row in template.items:
			self.append("checklist_items", {
				"item": row.item,
				"category": row.category,
			})

	@frappe.whitelist()
	def load_licenses_from_template(self):
		"""Populate licenses from license_template, replacing any existing rows."""
		if not self.license_template:
			frappe.throw(frappe._("Set a License Template first."))

		template = frappe.get_doc("Licenses and Permits Template", self.license_template)
		self.licenses = []
		for row in template.items:
			self.append("licenses", {"license_type": row.license_type})

	@frappe.whitelist()
	def load_insurance_from_template(self):
		"""Populate insurance_certificates from insurance_template, replacing any existing rows."""
		if not self.insurance_template:
			frappe.throw(frappe._("Set an Insurance Template first."))

		template = frappe.get_doc("Insurance Template", self.insurance_template)
		self.insurance_certificates = []
		for row in template.items:
			self.append("insurance_certificates", {"insurance_type": row.insurance_type})

	def validate(self):
		self._clear_unused_auditor_field()
		self._clear_facility_area_if_not_applicable()
		self._clear_valid_scope_if_global()
		self._validate_valid_scope_lists()
		self._validate_insurance_perils()
		self._validate_license_date_ranges()
		self.outcome = self.compute_result()
		self._warn_on_expired_documents()
		sync_vendor_field(self)
		sync_onboarding_request_field(self)
		enforce_sequential_creation(self)
		mark_vendor_status_in_progress(self, VENDOR_LIFECYCLE_STATUS_COMPLIANCE_AUDIT_IN_PROGRESS)

	def _clear_valid_scope_if_global(self):
		# Runs on every save, not just the client-side toggle — a stale
		# Countries/States value from before ticking Valid Globally (or one
		# set via the API directly) can never linger in the now-irrelevant
		# fields.
		for row in list(self.licenses) + list(self.insurance_certificates):
			if row.valid_globally:
				row.valid_countries = None
				row.valid_states = None

	def _validate_valid_scope_lists(self):
		# Valid In Countries/States are free text, not a real Link — a
		# genuine multi-select can't exist inside these rows at all (they're
		# already child tables of this Audit, and Frappe hardcodes every
		# child row's own table-field map to a permanently empty mapping —
		# confirmed directly against this Frappe version, not assumed). So
		# each comma-separated entry is checked here instead, against real
		# master data (Country, and this app's own State master — each
		# State record already carries its own Country), normalized to the
		# official spelling and de-duplicated, so a typo or a repeated entry
		# can't silently produce a value nothing will ever match against
		# later (a report, a filter, anything downstream). A State's own
		# Country is also auto-added to Valid In Countries when it's not
		# already listed there — so the two fields never have to be kept in
		# sync by hand.
		country_names = None
		for row in list(self.licenses) + list(self.insurance_certificates):
			if row.valid_globally:
				continue

			countries = self._dedupe_preserve_order(self._parse_tokens(row.valid_countries))
			if countries:
				if country_names is None:
					country_names = {name.lower(): name for name in frappe.get_all("Country", pluck="name")}
				countries = self._dedupe_preserve_order(
					[self._resolve_token(token, country_names, frappe._("country"), row) for token in countries]
				)

			state_tokens = self._dedupe_preserve_order(self._parse_tokens(row.valid_states))
			resolved_states = []
			if state_tokens:
				state_names, state_country_by_name = self._state_master_lookup(countries)
				if state_names:
					for token in state_tokens:
						match = self._resolve_token(token, state_names, frappe._("state"), row, master_doctype="State")
						resolved_states.append(match)
						implied_country = state_country_by_name.get(match.lower())
						if implied_country and implied_country.lower() not in [c.lower() for c in countries]:
							countries.append(implied_country)
					resolved_states = self._dedupe_preserve_order(resolved_states)
				else:
					# No States on file for this scope yet — left as free
					# text (deduped only), same as before the State master
					# had anything in it for that country.
					resolved_states = state_tokens

			row.valid_countries = ", ".join(countries) if countries else None
			row.valid_states = ", ".join(resolved_states) if resolved_states else None

	def _validate_insurance_perils(self):
		# Coverage Breakdown rows are real Link fields (Insurance Type,
		# Coverage Type), not free text — Frappe's own Link validation
		# already rejects a typo/nonexistent value, so this only needs to
		# check the things that are specific to THIS audit: a breakdown row
		# must belong to one of this audit's own Insurance Certificates
		# (not some other Insurance Type never actually added here), the
		# same Coverage Type can't appear twice under one Insurance Type,
		# and an unticked "Covered" row shouldn't carry a stale amount.
		insured_types = {row.insurance_type for row in self.insurance_certificates if row.insurance_type}
		seen = set()
		for row in self.insurance_perils:
			if row.insurance_type not in insured_types:
				frappe.throw(
					frappe._(
						"Row #{0}: {1} is not one of this audit's own Insurance Certificates — add it there first."
					).format(row.idx, frappe.bold(row.insurance_type))
				)

			key = (row.insurance_type, row.coverage_type)
			if key in seen:
				frappe.throw(
					frappe._("Row #{0}: {1} already has a Coverage Breakdown row for {2}.").format(
						row.idx, frappe.bold(row.insurance_type), frappe.bold(row.coverage_type)
					)
				)
			seen.add(key)

			if not row.covered:
				row.coverage_amount = 0

	def _state_master_lookup(self, countries):
		# Scoped to the row's own Valid In Countries when given (catches a
		# state/country mismatch, e.g. "Texas" under "India") — otherwise
		# matched against every State on file, since a row can leave
		# Countries blank and let it be filled in from the state instead.
		filters = {"disabled": 0}
		if countries:
			filters["country"] = ["in", countries]
		rows = frappe.get_all("State", filters=filters, fields=["state_name", "country"])
		name_map = {r.state_name.lower(): r.state_name for r in rows}
		country_map = {r.state_name.lower(): r.country for r in rows}
		return name_map, country_map

	def _parse_tokens(self, raw):
		return [t.strip() for t in (raw or "").split(",") if t.strip()]

	def _dedupe_preserve_order(self, values):
		seen = set()
		result = []
		for value in values:
			key = value.lower()
			if key not in seen:
				seen.add(key)
				result.append(value)
		return result

	def _resolve_token(self, token, valid_names, label, row, master_doctype=None):
		match = valid_names.get(token.lower())
		if match:
			return match

		close = difflib.get_close_matches(token, valid_names.values(), n=1, cutoff=0.6)
		suggestion = frappe._(" Did you mean {0}?").format(frappe.bold(close[0])) if close else ""

		if master_doctype:
			frappe.throw(
				frappe._("Row #{0}: {1} is not a valid {2}.{3} Add it under {4} first, or check the spelling.").format(
					row.idx, frappe.bold(token), label, suggestion, frappe.bold(master_doctype)
				)
			)
		frappe.throw(
			frappe._("Row #{0}: {1} is not a valid {2}.{3}").format(row.idx, frappe.bold(token), label, suggestion)
		)

	def _clear_facility_area_if_not_applicable(self):
		# Runs on every save, not just before_submit — a stale Facility
		# Area/Unit from before switching to "No" (or one set via the API
		# directly) can never linger in the now-irrelevant fields, even on
		# a draft that's never submitted.
		if self.facility_applicable != "Yes":
			self.facility_area = 0
			self.facility_area_unit = None

	def _clear_unused_auditor_field(self):
		# Only one of internal Auditors / external agency ever applies at a
		# time — enforced here too, not just by the client-side toggle, so
		# a stale value from before a switch (or one set via the API
		# directly) can never linger in the now-irrelevant field.
		if self.conducted_by_external_agency:
			self.auditors = []
		else:
			self.external_auditor = None
			self.external_agency_contact_person = None
			self.external_agency_contact_number = None
			self.external_agency_contact_email = None
			self.proof_of_visit = None

	def _warn_on_expired_documents(self):
		license_labels = self._expired_license_labels()
		insurance_labels = self._expired_insurance_labels()
		if not license_labels and not insurance_labels:
			return

		parts = []
		if license_labels:
			parts.append(
				frappe._("Licenses & Permits — {0}").format(", ".join(frappe.bold(label) for label in license_labels))
			)
		if insurance_labels:
			parts.append(
				frappe._("Insurance Certificates — {0}").format(
					", ".join(frappe.bold(label) for label in insurance_labels)
				)
			)
		frappe.msgprint(
			frappe._("These have already expired, which fails this audit's Result: {0}").format(
				"; ".join(parts)
			),
			title=frappe._("Expired Documents"),
			indicator="red",
		)

	def _expired_license_labels(self):
		today = frappe.utils.getdate()
		return [
			frappe._("Row #{0} ({1})").format(row.idx, row.license_type) for row in self.licenses
			if row.valid_upto and frappe.utils.getdate(row.valid_upto) < today
		]

	def _expired_insurance_labels(self):
		today = frappe.utils.getdate()
		return [
			frappe._("Row #{0} ({1})").format(row.idx, row.insurance_type) for row in self.insurance_certificates
			if row.expiry_date and frappe.utils.getdate(row.expiry_date) < today
		]

	def _has_expired_documents(self):
		return bool(self._expired_license_labels() or self._expired_insurance_labels())

	def _validate_license_date_ranges(self):
		for row in self.licenses:
			if row.valid_from and row.valid_upto and frappe.utils.getdate(row.valid_from) > frappe.utils.getdate(row.valid_upto):
				frappe.throw(
					frappe._("Row #{0} ({1}): Valid From cannot be after Valid Upto.").format(
						row.idx, row.license_type
					)
				)

	# DEFERRED IDEA — Corrective Action tracking on failed checklist items.
	# A first version of this was built and then reverted (2026-08-26): each
	# Fail row got a Button ("Manage Corrective Action") + allow_on_submit
	# fields (due date, responsible party, status, resolution notes,
	# resolved-on), shown whenever response=='Fail' regardless of docstatus,
	# with a server-side rule blocking an empty "Resolved" claim.
	#
	# Before building this again, the redesign actually wanted is different
	# from that first pass:
	#   - The button (labelled just "Corrective Action", not "Manage
	#     Corrective Action") should only appear once the Audit is
	#     submitted — not while still a draft.
	#   - It belongs in a dropdown (grouped with other row/document actions),
	#     not a bare inline button.
	#   - Every corrective-action field should be genuinely read-only on the
	#     row itself — editable only through the popup's own
	#     frappe.model.set_value calls, never by expanding the row and typing
	#     directly.
	#   - Scope is much smaller than the first pass: only a Due Date is
	#     actually required when a row is marked Fail. Responsible
	#     Party/Status/Resolution Notes/Resolved-On from the first attempt
	#     may be unnecessary.
	#   - Add a "No Corrective Action Needed" checkbox per Fail row. Ticking
	#     it asks for a date (of that decision) and removes the row from
	#     wherever "still needs corrective action" is being surfaced (the
	#     dashboard banner, any future report).
	#
	# Bigger open question, raised before any of the above gets built: is a
	# dedicated corrective-action tracker even needed at all, given
	# cancel + amend already gives a natural "go fix it and redo the audit"
	# path for a Failed Compliance Audit? Worth settling that before
	# building either version. Revisit only if asked — not scheduled now.
	def compute_result(self):
		# An expired legally-required license or insurance certificate is
		# treated as an absolute disqualifier — the same "hard veto, not
		# just another input" philosophy already established for Vendor
		# Background Check's Overall Status — a good checklist can't
		# outweigh a real, current compliance gap.
		if self._has_expired_documents():
			return "Failed"
		# Same "hard veto" treatment as expired documents — a financially
		# unsound vendor can't be offset by an otherwise-clean checklist.
		if self.financial_stability_outcome == "Unstable":
			return "Failed"
		if not self.checklist_items:
			return "Needs Review"
		if any(row.response == "Fail" for row in self.checklist_items):
			return "Failed"
		# Not yet judged — blocks Pass the same way an unanswered checklist
		# item does, but doesn't override an actual Fail found above.
		if self.financial_stability_outcome == "Not Reviewed":
			return "Needs Review"
		# A genuinely unreviewed row (response still blank) must block the
		# Result until it's actually looked at — but a row deliberately
		# marked "N/A" doesn't count against (or for) the outcome at all,
		# so it's excluded rather than treated as if it were unreviewed.
		if any(not row.response for row in self.checklist_items):
			return "Needs Review"
		applicable = [row for row in self.checklist_items if row.response != "N/A"]
		if not applicable:
			# Every row marked N/A — nothing left to actually judge.
			return "Needs Review"
		if all(row.response == "Pass" for row in applicable):
			return "Passed"
		return "Needs Review"

	def before_submit(self):
		self._require_checklist_template_still_enabled()
		self._require_license_template_still_enabled()
		self._require_insurance_template_still_enabled()
		self._require_license_and_insurance_rows_not_disabled()
		self._require_mandatory_evidence()
		self._require_checklist_ready()
		self._require_remarks_for_fail_and_na()
		self._require_financial_stability_notes_if_unstable()
		self._require_facility_area_complete()
		self._require_audit_team_complete()

	def _require_financial_stability_notes_if_unstable(self):
		if self.financial_stability_outcome == "Unstable" and not self.financial_stability_notes:
			frappe.throw(
				frappe._("Notes are mandatory under Financial Stability when the Outcome is Unstable."),
				frappe.MandatoryError,
			)

	def _require_audit_team_complete(self):
		# Whichever side of the toggle applies must actually be filled in
		# before submitting — deferred to submit time (not validate()), same
		# as every other completeness check on this doctype, so a draft can
		# still be saved before this is decided.
		if self.conducted_by_external_agency:
			missing = []
			if not self.external_auditor:
				missing.append(frappe._("External Auditor / Agency"))
			if not self.external_agency_contact_person:
				missing.append(frappe._("External Agency Contact Person"))
			if not self.external_agency_contact_number:
				missing.append(frappe._("External Agency Contact Number"))
			if not self.external_agency_contact_email:
				missing.append(frappe._("External Agency Contact Email"))
			if missing:
				frappe.throw(
					frappe._("These fields are mandatory before submitting: {0}").format(
						", ".join(frappe.bold(m) for m in missing)
					),
					frappe.MandatoryError,
				)
		elif not self.auditors:
			frappe.throw(frappe._("Auditors (Internal) must have at least one user before submitting."), frappe.MandatoryError)

	def _require_remarks_for_fail_and_na(self):
		# mandatory_depends_on is desk-UI-only in this Frappe version — it
		# never blocks a save via the API, so this is checked again here at
		# submit time (same pattern as every other completeness check on
		# this doctype). A Pass row needs no explanation; Fail or N/A do —
		# N/A in particular is easy to click through without saying why
		# something didn't apply.
		for row in self.checklist_items:
			if row.response in ("Fail", "N/A") and not row.remark:
				frappe.throw(
					frappe._("Row #{0} ({1}): Reason/Remark is mandatory when Response is Fail or N/A.").format(
						row.idx, frappe.bold(row.item)
					)
				)

	def _require_license_and_insurance_rows_not_disabled(self):
		# The Link fields' own filters only keep a disabled License/Issuing
		# Authority/Insurance Type/Insurer record out of the picker going
		# forward — a row can still be pointing at one that was enabled when
		# picked and got disabled afterward, so this is checked again here,
		# fresh, at submit time (same pattern as
		# _require_compliance_check_rows_not_disabled on Vendor Background
		# Check).
		self._require_rows_not_disabled(
			self.licenses, [("license_type", "License Type"), ("issuing_authority", "Issuing Authority")]
		)
		self._require_rows_not_disabled(
			self.insurance_certificates, [("insurance_type", "Insurance Type"), ("insurer", "Insurer")]
		)

	def _require_rows_not_disabled(self, rows, field_master_pairs):
		for row in rows:
			problems = []
			for fieldname, master_doctype in field_master_pairs:
				value = row.get(fieldname)
				if value and frappe.db.get_value(master_doctype, value, "disabled"):
					problems.append(frappe._("{0} {1} is disabled").format(master_doctype, frappe.bold(value)))
			if problems:
				frappe.throw(
					frappe._("Row #{0}: {1} — pick a different one before submitting.").format(
						row.idx, "; ".join(problems)
					)
				)

	def _require_facility_area_complete(self):
		# mandatory_depends_on is desk-UI-only in this Frappe version — it
		# never blocks a save via the API — and even a plain reqd wouldn't
		# reject an explicit 0, which Frappe treats as a real value for a
		# numeric field, not a missing one. Both have to be checked here.
		# Deferred to submit time (not validate()), same as every other
		# completeness check on this doctype, so a draft can still be saved
		# before Facility Applicable has been decided.
		if not self.facility_applicable:
			frappe.throw(frappe._("Facility Applicable must be answered (Yes or No) before submitting."), frappe.MandatoryError)
		if self.facility_applicable == "Yes":
			if not self.facility_area:
				frappe.throw(
					frappe._("Facility Area is mandatory once Facility Applicable is Yes, and cannot be zero."),
					frappe.MandatoryError,
				)
			if not self.facility_area_unit:
				frappe.throw(frappe._("Unit is mandatory once Facility Applicable is Yes."), frappe.MandatoryError)

	def _require_checklist_ready(self):
		if not self.checklist_items:
			frappe.throw(frappe._("Add at least one Checklist Item before submitting."))
		if self.outcome == "Needs Review":
			frappe.throw(
				frappe._(
					"This Compliance Audit's Result is still \"Needs Review\" — every checklist item must"
					" be answered (Pass, Fail, or N/A), and Financial Stability Outcome must be set to"
					" Stable or Unstable, before submitting."
				)
			)

	@frappe.whitelist()
	def check_submit_readiness(self):
		# Dry-runs every before_submit() precondition, calling the exact
		# same private methods before_submit() itself calls, so the two
		# can never quietly drift apart — lets the client surface a real
		# blocking problem before showing the Failed-Result confirmation,
		# so a "Yes, submit anyway" click can't be immediately followed by
		# an unrelated failure (see Vendor Background Check's own
		# check_submit_readiness for the same pattern).
		structural_error = None
		try:
			self._require_checklist_template_still_enabled()
			self._require_license_template_still_enabled()
			self._require_insurance_template_still_enabled()
			self._require_license_and_insurance_rows_not_disabled()
			self._require_mandatory_evidence()
			self._require_checklist_ready()
			self._require_remarks_for_fail_and_na()
			self._require_financial_stability_notes_if_unstable()
			self._require_facility_area_complete()
			self._require_audit_team_complete()
		except frappe.ValidationError as e:
			structural_error = str(e)
			# frappe.throw() queues its message for automatic client-side
			# display in addition to raising — without this, the client
			# would show it twice.
			frappe.clear_last_message()

		return {"structural_error": structural_error, "outcome": self.outcome}

	def _require_checklist_template_still_enabled(self):
		# The Link field's own filter only keeps a disabled template out of
		# the picker going forward — it doesn't stop this Audit from still
		# pointing at one it picked before that template was disabled, so
		# this is checked again here at submit time.
		if self.checklist_template and frappe.db.get_value("Audit Checklist Template", self.checklist_template, "disabled"):
			frappe.throw(
				frappe._("Checklist Template {0} is disabled — pick a different one before submitting.").format(
					frappe.bold(self.checklist_template)
				)
			)

	def _require_license_template_still_enabled(self):
		# Same reasoning as _require_checklist_template_still_enabled — the
		# Link field's own filter only keeps a disabled template out of the
		# picker going forward.
		if self.license_template and frappe.db.get_value("Licenses and Permits Template", self.license_template, "disabled"):
			frappe.throw(
				frappe._("Licenses and Permits Template {0} is disabled — pick a different one before submitting.").format(
					frappe.bold(self.license_template)
				)
			)

	def _require_insurance_template_still_enabled(self):
		# Same reasoning as _require_checklist_template_still_enabled — the
		# Link field's own filter only keeps a disabled template out of the
		# picker going forward.
		if self.insurance_template and frappe.db.get_value("Insurance Template", self.insurance_template, "disabled"):
			frappe.throw(
				frappe._("Insurance Template {0} is disabled — pick a different one before submitting.").format(
					frappe.bold(self.insurance_template)
				)
			)

	def _require_mandatory_evidence(self):
		# Which items need evidence is configured on the Template itself —
		# looked up fresh here rather than trusting anything copied onto
		# this document's own rows, since editing the Template's tick-box
		# after the checklist was loaded must take effect immediately, not
		# only the next time someone happens to reload it (same pattern as
		# Vendor Background Check's own mandatory-compliance-field check).
		#
		# Checklist items aren't a Link to a shared master — matched by
		# exact item text against the template's own rows instead.
		if not self.checklist_template:
			return
		evidence_required_items = set(
			frappe.get_all(
				"Audit Checklist Template Item",
				filters={"parent": self.checklist_template, "evidence_required": 1},
				pluck="item",
			)
		)
		if not evidence_required_items:
			return
		for row in self.checklist_items:
			if row.item in evidence_required_items and not row.evidence:
				frappe.throw(
					frappe._("Row #{0} ({1}): Evidence must be attached before submitting.").format(
						row.idx, frappe.bold(row.item)
					)
				)

	def on_submit(self):
		if self.outcome == "Failed":
			self._handle_failed_result()
			self._notify_outcome(DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE)
			return

		if self.vendor and self.outcome == "Passed":
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_status", VENDOR_LIFECYCLE_STATUS_AUDIT_VERIFIED)
			months = frappe.db.get_single_value("Vendor Lifecycle Settings", "compliance_audit_validity_months") or 0
			self.db_set("valid_until", frappe.utils.add_months(self.audit_date, months))
			# Deliberately manual/reporting-only for now (see the "Vendors Due
			# for Re-Audit" report) — nothing here reacts when valid_until
			# passes. A future opt-in Settings toggle (e.g. "Auto-flag
			# Suppliers with Expired Compliance Audit") could flip
			# vendor_lifecycle_status once a vendor's latest Passed audit
			# expires with no newer one — the same "hard veto" enforcement
			# style already used for a failed Background Check
			# (_handle_failed_result below) — but that's a real behavior
			# change a deployment should opt into, not something to turn on
			# silently by building it now.
		self._notify_outcome(DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE)

	def _notify_outcome(self, template_name):
		try:
			contact = get_kyc_vendor_contact(self.kyc) if self.kyc else {}
			context = {
				"firm_name": contact.get("firm_name"),
				"contact_person_name": contact.get("contact_person_name"),
				"company_name": resolve_vendor_lifecycle_company_name(),
				"stage_name": "Compliance Audit",
			}
			if template_name == DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE:
				context["next_stage"] = "Sampling Evaluation"
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=template_name,
				context=context,
				recipients=[contact["official_email"]] if contact.get("official_email") else [],
			)
		except Exception:
			frappe.log_error(
				title="Vendor Compliance Audit: failed to send outcome email", message=frappe.get_traceback()
			)

	def _handle_failed_result(self):
		# Same treatment as a Failed Vendor Background Check
		# (_handle_failed_result there): the Supplier is always created at
		# Vendor KYC submission, so it already exists by the time any
		# Compliance Audit is submitted — this just disables it.
		# get_available_stages() already hard-stops offering the next stage
		# once this Audit has Failed, so no separate stage-blocking logic is
		# needed here.
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 1)

	@frappe.whitelist()
	def force_override(self, reason):
		"""Lets a System Manager / Vendor Lifecycle Manager push past a
		Failed result so later stages become creatable again, without
		pretending the result was actually Passed. See
		stage_sequencing.force_override_stage for the shared permission
		check, mandatory-reason enforcement, and Supplier re-enable logic."""
		if self.outcome != "Failed":
			frappe.throw(frappe._("Only a Failed result can be force-overridden."))
		force_override_stage(self, reason)

	def on_cancel(self):
		self._revert_disable_if_this_was_the_failed_one()

	def _revert_disable_if_this_was_the_failed_one(self):
		# Same treatment as Vendor Background Check's own
		# _revert_disable_if_this_was_the_failed_one — cancelling this Audit
		# retracts its own consequences, unless some *other* still-submitted
		# Compliance Audit for the same vendor is also Failed (only possible
		# from data predating the one-active-Compliance-Audit-per-vendor
		# rule), in which case the Supplier must stay disabled on that
		# one's account.
		if self.outcome != "Failed" or not self.vendor:
			return
		other_failed_exists = frappe.db.exists(
			"Vendor Compliance Audit",
			{"vendor": self.vendor, "docstatus": 1, "outcome": "Failed", "name": ["!=", self.name]},
		)
		if not other_failed_exists:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 0)
