# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

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

VENDOR_LIFECYCLE_STATUS_SAMPLING_APPROVED = "Sampling Approved"
VENDOR_LIFECYCLE_STATUS_SAMPLING_IN_PROGRESS = "Sampling In Progress"
DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Passed"
DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Failed"


class VendorSamplingEvaluation(Document):
	def before_insert(self):
		require_no_active_document_for_kyc(self)

	def validate(self):
		self._require_sample_identifier()
		self._warn_on_partial_or_failed_assessments()
		self._warn_on_unreceived_samples_with_final_outcome()
		sync_vendor_field(self)
		sync_onboarding_request_field(self)
		enforce_sequential_creation(self)
		mark_vendor_status_in_progress(self, VENDOR_LIFECYCLE_STATUS_SAMPLING_IN_PROGRESS)

	def _require_sample_identifier(self):
		for row in self.samples:
			if not row.item_code and not row.sample_description:
				frappe.throw(
					frappe._("Row #{0}: fill in either Item or What's Being Evaluated.").format(row.idx)
				)

	def _warn_on_partial_or_failed_assessments(self):
		# A warning, not a block — Evaluation Outcome is still the
		# reviewer's own separate judgment call; a row that doesn't fully
		# meet expectations is surfaced here so it isn't missed, rather
		# than auto-failing anything. Same pattern as Vendor Background
		# Check's own _warn_on_flagged_or_failed_compliance_checks.
		flagged = [
			frappe._("Row #{0} ({1}) — {2}").format(row.idx, row.criteria, row.assessment)
			for row in self.evaluation_results
			if row.assessment in ("Partially Meets", "Does Not Meet")
		]
		if flagged:
			frappe.msgprint(
				frappe._("The following Evaluation Results need attention: {0}").format(", ".join(flagged)),
				title=frappe._("Evaluation Results Need Review"),
				indicator="orange",
			)

	def _warn_on_unreceived_samples_with_final_outcome(self):
		# A warning, not a block — you can't have properly evaluated a
		# sample you haven't actually received yet, but the reviewer may
		# have a real reason to record a Rejected outcome anyway (e.g. the
		# vendor failed to ship it at all). Same pattern as
		# _warn_on_partial_or_failed_assessments above.
		if self.evaluation_outcome not in ("Approved", "Rejected"):
			return
		unreceived = [
			frappe._("Row #{0} — {1}").format(row.idx, row.sample_status)
			for row in self.samples
			if row.sample_status in ("Not Received", "In-Transit")
		]
		if unreceived:
			frappe.msgprint(
				frappe._(
					"Evaluation Outcome is {0}, but the following samples haven't been received yet: {1}"
				).format(self.evaluation_outcome, ", ".join(unreceived)),
				title=frappe._("Sample Not Yet Received"),
				indicator="orange",
			)

	def has_unreceived_samples(self):
		return any(row.sample_status in ("Not Received", "In-Transit") for row in self.samples)

	@frappe.whitelist()
	def load_evaluation_from_template(self):
		"""Populate evaluation_results from evaluation_template, replacing any existing rows."""
		if not self.evaluation_template:
			frappe.throw(frappe._("Set an Evaluation Template first."))

		template = frappe.get_doc("Sampling Evaluation Template", self.evaluation_template)
		self.evaluation_results = []
		for row in template.criteria:
			self.append("evaluation_results", {
				"criteria": row.criteria,
				"category": row.category,
			})

	def before_submit(self):
		self._require_evaluation_outcome_reviewed()
		self._require_samples_and_evaluation_rows()
		self._require_evaluation_template_still_enabled()
		self._require_evaluation_rows_assessed()
		self._require_sample_status_set()

	def _require_evaluation_outcome_reviewed(self):
		if self.evaluation_outcome == "Not Reviewed":
			frappe.throw(
				frappe._("Evaluation Outcome must be set to Approved or Rejected before submitting."),
				frappe.MandatoryError,
			)

	def _require_samples_and_evaluation_rows(self):
		if not self.samples:
			frappe.throw(frappe._("Add at least one row to Products / Samples before submitting."), frappe.MandatoryError)
		if not self.evaluation_results:
			frappe.throw(frappe._("Add at least one row to Evaluation Results before submitting."), frappe.MandatoryError)

	def _require_evaluation_template_still_enabled(self):
		# The Link field's own filter only keeps a disabled template out of
		# the picker going forward — it doesn't stop this document from
		# still pointing at one it picked before that template was
		# disabled, so this is checked again here (same pattern as Vendor
		# Compliance Audit's own template checks).
		if self.evaluation_template and frappe.db.get_value("Sampling Evaluation Template", self.evaluation_template, "disabled"):
			frappe.throw(
				frappe._("Evaluation Template {0} is disabled — pick a different one before submitting.").format(
					frappe.bold(self.evaluation_template)
				)
			)

	def _require_evaluation_rows_assessed(self):
		for row in self.evaluation_results:
			if not row.assessment:
				frappe.throw(
					frappe._("Row #{0}: set Assessment before submitting.").format(row.idx),
					frappe.MandatoryError,
				)

	def _require_sample_status_set(self):
		for row in self.samples:
			if not row.sample_status:
				frappe.throw(
					frappe._("Row #{0}: set Sample Status before submitting.").format(row.idx),
					frappe.MandatoryError,
				)

	@frappe.whitelist()
	def check_submit_readiness(self):
		# Same pattern as Vendor Background Check / Vendor Compliance
		# Audit's own check_submit_readiness — dry-runs before_submit()'s
		# preconditions so the client can surface a real blocking problem
		# before showing the Approved/Rejected confirmation.
		structural_error = None
		try:
			self._require_evaluation_outcome_reviewed()
			self._require_samples_and_evaluation_rows()
			self._require_evaluation_template_still_enabled()
			self._require_evaluation_rows_assessed()
			self._require_sample_status_set()
		except frappe.ValidationError as e:
			structural_error = str(e)
			frappe.clear_last_message()

		return {
			"structural_error": structural_error,
			"evaluation_outcome": self.evaluation_outcome,
			"has_unreceived_samples": self.has_unreceived_samples(),
		}

	def on_submit(self):
		if self.evaluation_outcome == "Rejected":
			self._handle_rejected_result()
			self._notify_outcome(DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE)
			return
		if self.vendor and self.evaluation_outcome == "Approved":
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_status", VENDOR_LIFECYCLE_STATUS_SAMPLING_APPROVED)
		self._notify_outcome(DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE)

	def _notify_outcome(self, template_name):
		try:
			contact = get_kyc_vendor_contact(self.kyc) if self.kyc else {}
			context = {
				"firm_name": contact.get("firm_name"),
				"contact_person_name": contact.get("contact_person_name"),
				"company_name": resolve_vendor_lifecycle_company_name(),
				"stage_name": "Sampling Evaluation",
			}
			if template_name == DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE:
				context["next_stage"] = "Sign Off"
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=template_name,
				context=context,
				recipients=[contact["official_email"]] if contact.get("official_email") else [],
			)
		except Exception:
			frappe.log_error(
				title="Vendor Sampling Evaluation: failed to send outcome email", message=frappe.get_traceback()
			)

	def _handle_rejected_result(self):
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 1)

	@frappe.whitelist()
	def force_override(self, reason):
		"""Lets a System Manager / Vendor Lifecycle Manager push past a
		Rejected result so later stages become creatable again, without
		pretending the result was actually Approved. See
		stage_sequencing.force_override_stage for the shared permission
		check, mandatory-reason enforcement, and Supplier re-enable logic."""
		if self.evaluation_outcome != "Rejected":
			frappe.throw(frappe._("Only a Rejected result can be force-overridden."))
		force_override_stage(self, reason)

	def on_cancel(self):
		self._revert_disable_if_this_was_the_rejected_one()

	def _revert_disable_if_this_was_the_rejected_one(self):
		if self.evaluation_outcome != "Rejected" or not self.vendor:
			return
		other_rejected_exists = frappe.db.exists(
			"Vendor Sampling Evaluation",
			{"vendor": self.vendor, "docstatus": 1, "evaluation_outcome": "Rejected", "name": ["!=", self.name]},
		)
		if not other_rejected_exists:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 0)
