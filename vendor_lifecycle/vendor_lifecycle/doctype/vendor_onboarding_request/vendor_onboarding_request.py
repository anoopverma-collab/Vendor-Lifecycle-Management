# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.stage_sequencing import STAGE_SEQUENCE
from vendor_lifecycle.vendor_lifecycle.state_validation import (
	require_state_for_india,
	validate_indian_state_spelling,
	validate_state_master_matches_country,
)
from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	is_kyc_rejected,
	resolve_vendor_lifecycle_company_name,
	send_vendor_lifecycle_email,
	vendor_lifecycle_manager_emails,
)

DEFAULT_ONBOARDING_RECEIVED_EMAIL_TEMPLATE = "Vendor Onboarding Request Received"
DEFAULT_ONBOARDING_NEW_REQUEST_EMAIL_TEMPLATE = "Vendor Onboarding Request - New Submission"
DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Passed"

# Business Details fields required for every business type, once one is picked.
COMMON_BUSINESS_DETAIL_FIELDS = [
	"estimated_monthly_production_capacity",
	"specialization",
	"years_in_business",
	"team_size",
]

# Extra fields required only for the matching business type.
TYPE_SPECIFIC_MANDATORY_FIELDS = {
	"Manufacturer": [],
	"Trader": ["brands_distributed", "minimum_order_quantity"],
	"Service Provider": ["service_types_offered", "turnaround_time"],
	"Contractor / Job Worker": ["scope_of_work", "equipment_provided_by"],
	"Logistics / Transporter": ["fleet_size", "coverage_area"],
	"Freelancer / Consultant": ["areas_of_expertise", "availability_hours_per_week"],
	"Raw Material Supplier": ["materials_supplied", "certification_standards"],
	"Equipment / Machinery Supplier": ["equipment_types"],
	"Technology / Software Vendor": ["product_or_platform_name"],
	"Facility / Maintenance Services": ["services_covered"],
	"Other": ["other_business_type"],
}

# Extra fields required only for the matching "How did you hear about us?" answer.
REFERRAL_SPECIFIC_MANDATORY_FIELDS = {
	"Other": ["referral_source_other"],
	"Referral from Existing Vendor": ["existing_vendor_name"],
}

# Drives get_pipeline_progress()'s per-stage status for the 4 doctypes after
# KYC — (doctype, display label, field holding the pass/fail decision, the
# value that field takes when Passed, the force-override field or None).
# Sign Off has no force field: it's not in FORCE_OVERRIDABLE_DOCTYPES
# (stage_sequencing.py) — a Failed Sign-off is a terminal decision about the
# vendor overall, not a mid-pipeline gate a manager can force past.
STAGE_OUTCOME_CONFIG = (
	("Vendor Background Check", "Background Check", "overall_status", "Passed", "force_overridden"),
	("Vendor Compliance Audit", "Compliance Audit", "outcome", "Passed", "force_overridden"),
	("Vendor Sampling Evaluation", "Sampling Evaluation", "evaluation_outcome", "Approved", "force_overridden"),
	("Vendor Sign Off", "Sign Off", "sign_off_failed", 0, None),
)


class VendorOnboardingRequest(Document):
	def before_insert(self):
		# Always computed fresh — never trust a caller-supplied value, and
		# don't rely on the field's own default, which would run before this
		# and mask an internally-created request as "Web Form".
		self.source = "Web Form" if frappe.session.user == "Guest" else "Internal"

	def after_insert(self):
		self._notify_received()

	def _notify_received(self):
		try:
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=DEFAULT_ONBOARDING_RECEIVED_EMAIL_TEMPLATE,
				context={"vendor_company_name": self.company_name, "contact_person": self.contact_person},
				recipients=[self.email] if self.email else [],
			)
		except Exception:
			frappe.log_error(
				title="Vendor Onboarding Request: failed to send received email", message=frappe.get_traceback()
			)

		try:
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=DEFAULT_ONBOARDING_NEW_REQUEST_EMAIL_TEMPLATE,
				context={
					"vendor_company_name": self.company_name,
					"contact_person": self.contact_person,
					"contact_number": self.contact_number,
					"email": self.email,
					"gstin_uin": self.gstin_uin,
					"record_link": frappe.utils.get_url_to_form(self.doctype, self.name),
				},
				recipients=vendor_lifecycle_manager_emails(),
			)
		except Exception:
			frappe.log_error(
				title="Vendor Onboarding Request: failed to send new-request notification", message=frappe.get_traceback()
			)

	def on_submit(self):
		# Submitting a request *is* the approval decision here — there's no
		# separate "Accepted" step (see migrate_onboarding_request_to_
		# submittable in install.py for why this doctype has no status
		# field of its own anymore).
		try:
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE,
				context={
					"firm_name": self.company_name,
					"contact_person_name": self.contact_person,
					"company_name": resolve_vendor_lifecycle_company_name(),
					"stage_name": "Onboarding Request",
					"next_stage": "KYC",
				},
				recipients=[self.email] if self.email else [],
			)
		except Exception:
			frappe.log_error(
				title="Vendor Onboarding Request: failed to send approved email", message=frappe.get_traceback()
			)

	def validate(self):
		# mandatory_depends_on is desk-UI-only in this Frappe version — it
		# never blocks a save via API or web form, so every conditional
		# "mandatory" field in this doctype must also be checked here.
		self._throw_if_missing(REFERRAL_SPECIFIC_MANDATORY_FIELDS.get(self.referral_source, []))

		if self.business_type:
			required = COMMON_BUSINESS_DETAIL_FIELDS + TYPE_SPECIFIC_MANDATORY_FIELDS.get(self.business_type, [])
			self._throw_if_missing(required)

		validate_state_master_matches_country(self)
		require_state_for_india(self)
		validate_indian_state_spelling(self)
		self._validate_establishment_date()
		self._check_duplicate()

	def _validate_establishment_date(self):
		if self.establishment_date and frappe.utils.getdate(self.establishment_date) > frappe.utils.getdate():
			frappe.throw(frappe._("Establishment Date cannot be in the future."))

	def _check_duplicate(self):
		handling = frappe.db.get_single_value("Vendor Lifecycle Settings", "duplicate_request_handling") or "Warn"
		if handling == "No Check":
			return

		# GSTIN is only checked when filled in — plenty of legitimate
		# requests (outside India, or before GST registration) leave it
		# blank, and blank values shouldn't be treated as duplicates of
		# each other.
		checks = [("email", self.email, frappe._("email")), ("contact_number", self.contact_number, frappe._("contact number"))]
		if self.gstin_uin:
			checks.append(("gstin_uin", self.gstin_uin, frappe._("GSTIN")))

		for fieldname, value, label in checks:
			duplicate = frappe.db.get_value(
				"Vendor Onboarding Request",
				{"name": ["!=", self.name or ""], fieldname: value},
				"name",
			)
			if not duplicate:
				continue

			message = frappe._("Another Onboarding Request ({0}) already exists with the same {1}.").format(
				frappe.bold(duplicate), label
			)
			if handling == "Block":
				frappe.throw(message)
			else:
				frappe.msgprint(message, title=frappe._("Duplicate Onboarding Request"), indicator="orange")

	def _throw_if_missing(self, fieldnames):
		missing = [f for f in fieldnames if not self.get(f)]
		if missing:
			labels = [frappe.bold(self.meta.get_label(f)) for f in missing]
			frappe.throw(
				frappe._("Please fill in the following: {0}").format(", ".join(labels)),
				frappe.MandatoryError,
			)

	@frappe.whitelist()
	def start_kyc(self):
		"""Internal review step: return pre-fill values for a new Vendor KYC.

		Nothing is created or saved here — the reviewer gets a fresh, unsaved
		Vendor KYC form pre-filled with this data, and decides for themselves
		when (and whether) to save it."""
		if self.docstatus != 1:
			frappe.throw(frappe._("This request must be submitted before starting KYC."))

		if frappe.db.exists("Vendor KYC", {"onboarding_request": self.name}):
			handling = frappe.db.get_single_value("Vendor Lifecycle Settings", "duplicate_kyc_handling") or "Stop"
			if handling == "Stop":
				frappe.throw(frappe._("A Vendor KYC already exists for this request."))
			# Warn/Ignore: Settings allows more than one KYC per request — let
			# this proceed; the new Vendor KYC's own validate() surfaces the
			# duplicate warning (or stays silent, for Ignore).

		values = {
			"onboarding_request": self.name,
			"firm_name": self.company_name,
			"business_type": self.business_type,
			"contact_person_name": self.contact_person,
			"contact_person_number": self.contact_number,
			"official_email": self.email,
			"address_line_1": self.address_line_1,
			"address_line_2": self.address_line_2,
			"city": self.city,
			"state": self.state,
			# frappe.new_doc's prefill is a raw assignment, not a real field
			# change — it would never trigger state_master's own fetch_from,
			# so the matching State record is resolved here instead, purely
			# so the new KYC's State dropdown doesn't show up empty despite
			# state already being correctly filled in.
			"state_master": frappe.db.get_value("State", {"state_name": self.state, "country": self.country}, "name")
			if self.state and self.country
			else None,
			"country": self.country,
			"pincode": self.pincode,
			"tax_id": self.tax_id,
			"gstin_uin": self.gstin_uin,
			"establishment_date": self.establishment_date,
		}
		# Covers "Other" -> other_business_type too, via TYPE_SPECIFIC_MANDATORY_FIELDS.
		for fieldname in COMMON_BUSINESS_DETAIL_FIELDS + TYPE_SPECIFIC_MANDATORY_FIELDS.get(self.business_type, []):
			values[fieldname] = self.get(fieldname)

		return values

	@frappe.whitelist()
	def get_pipeline_progress(self):
		"""Where this vendor currently stands across KYC and the four stages
		after it — computed fresh from whatever records exist right now,
		since stages don't have to happen in a fixed order unless Settings
		says so (see stage_sequencing.py)."""
		if self.docstatus != 1:
			return []

		mandatory_check_by_doctype = dict(STAGE_SEQUENCE)
		settings = frappe.get_single("Vendor Lifecycle Settings")

		progress = [{"label": "KYC", "state": self._kyc_stage_status()}]

		kyc_names = frappe.get_all("Vendor KYC", filters={"onboarding_request": self.name}, pluck="name")
		for doctype, label, outcome_field, passed_value, force_field in STAGE_OUTCOME_CONFIG:
			state = (
				self._stage_status(doctype, {"kyc": ["in", kyc_names]}, outcome_field, passed_value, force_field)
				if kyc_names
				else "Not Started"
			)

			# A stage Settings has marked skippable (and that hasn't been
			# started anyway) is flagged as "Skipped" rather than "Not
			# Started" — it's still fully completable if someone does it
			# regardless, in which case it shows as Completed/In Progress
			# like any other stage; this is purely about the not-started
			# case, so the reviewer can tell "hasn't happened yet, but has
			# to" apart from "hasn't happened, and doesn't need to".
			if state == "Not Started":
				mandatory_check = mandatory_check_by_doctype.get(doctype)
				if mandatory_check and not mandatory_check(self.business_type, settings):
					state = "Skipped"

			progress.append({"label": label, "state": state})

		return progress

	def _kyc_stage_status(self):
		"""Unlike the 4 stages after it, more than one Vendor KYC can exist
		for the same request (a rejected attempt doesn't block a fresh one —
		see Vendor KYC's own duplicate-check). Verified beats In Progress
		beats Rejected beats Not Started, so a newer active/verified attempt
		always takes over the display instead of a stale rejection."""
		kycs = frappe.get_all("Vendor KYC", filters={"onboarding_request": self.name}, fields=["name", "status"])
		if not kycs:
			return "Not Started"

		# is_kyc_rejected() has to be checked per-KYC, before falling back
		# to its own status field — a workflow-rejected KYC's status field
		# is never touched by the workflow at all, so it's still sitting at
		# "In Progress" (its default), not "Rejected". Checking status
		# first would wrongly treat that as a genuinely in-progress KYC.
		effective_states = []
		for k in kycs:
			if k.status == "Verified":
				effective_states.append("Verified")
			elif is_kyc_rejected(k.name):
				effective_states.append("Rejected")
			elif k.status == "In Progress":
				effective_states.append("In Progress")

		if "Verified" in effective_states:
			return "Completed"
		if "In Progress" in effective_states:
			return "In Progress"
		if "Rejected" in effective_states:
			return "Rejected"
		return "Not Started"

	def _stage_status(self, doctype, filters, outcome_field, passed_value, force_field):
		"""A submitted stage document is "Completed" only if it genuinely
		passed — force_overridden is checked first, since force_override_stage()
		(stage_sequencing.py) never touches the underlying outcome field, so a
		forcefully-passed record still literally reads Failed/Rejected
		underneath forever; that's shown as its own distinct "Forcefully
		Passed" state rather than folded into either Completed or Failed.
		Sign Off has no force_field (it's not force-overridable — see
		FORCE_OVERRIDABLE_DOCTYPES in stage_sequencing.py), so it only ever
		resolves to Completed or Failed.

		docstatus 2 (cancelled) is excluded from the lookup entirely — a
		cancelled record with no fresh replacement isn't "in progress" or
		any prior outcome, it's simply as if nothing exists yet. Only one
		non-cancelled record can exist per KYC at a time (see
		require_no_active_document_for_kyc), so this is never ambiguous
		about which record to look at."""
		rows = frappe.get_all(
			doctype,
			filters={**filters, "docstatus": ["!=", 2]},
			fields=["docstatus", outcome_field] + ([force_field] if force_field else []),
			limit=1,
		)
		if not rows:
			return "Not Started"

		row = rows[0]
		if row.docstatus == 0:
			return "In Progress"

		# docstatus == 1 from here — Needs Review/Not Reviewed shouldn't be
		# reachable at submit time (every one of these 4 doctypes blocks its
		# own submit while its outcome is still unresolved), so this is a
		# genuine Passed/Failed decision, not a partial one.
		if force_field and row.get(force_field):
			return "Forcefully Passed"
		return "Completed" if row.get(outcome_field) == passed_value else "Failed"
