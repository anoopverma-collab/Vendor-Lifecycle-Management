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

VENDOR_LIFECYCLE_STATUS_BACKGROUND_VERIFIED = "Background Verified"
VENDOR_LIFECYCLE_STATUS_BACKGROUND_CHECK_IN_PROGRESS = "Background Check In Progress"
DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Passed"
DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Failed"


class VendorBackgroundCheck(Document):
	def before_insert(self):
		require_no_active_document_for_kyc(self)

		if self.amended_from:
			# References are permanently tied to whichever Background Check
			# they were originally submitted against (see
			# _require_background_check_in_draft on the Reference) and can
			# never be re-pointed at this new document — so the Result and
			# Reference Summary copied over from the amended-from document
			# are stale leftovers that no real Reference here will ever be
			# able to reproduce. Left as-is, _require_result_is_fresh would
			# block submission forever, since nothing on this document can
			# ever recompute back to match them. Clearing them here makes
			# the amendment start exactly like a brand-new Background
			# Check: no References yet, Result "Needs Review", until fresh
			# ones are added.
			self.reference_summary = []
			self.outcome = "Needs Review"

	@frappe.whitelist()
	def load_compliance_checks_from_template(self):
		if not self.compliance_check_template:
			frappe.throw(frappe._("Set a Compliance Check Template first."))

		template = frappe.get_doc("Compliance Check Template", self.compliance_check_template)
		self.compliance_checks = []
		for row in template.items:
			self.append("compliance_checks", {"check_type": row.check_type})

	def validate(self):
		self._clear_unused_auditor_field()
		self._warn_on_flagged_or_failed_compliance_checks()
		self._compute_overall_status()
		sync_vendor_field(self)
		sync_onboarding_request_field(self)
		enforce_sequential_creation(self)
		mark_vendor_status_in_progress(self, VENDOR_LIFECYCLE_STATUS_BACKGROUND_CHECK_IN_PROGRESS)

	def _clear_unused_auditor_field(self):
		# Only one of internal Conducted By / external agency ever applies at
		# a time — enforced here too, not just by the client-side toggle, so
		# a stale value from before a switch (or one set via the API
		# directly) can never linger in the now-irrelevant field.
		if self.conducted_by_external_agency:
			self.conducted_by = []
		else:
			self.external_agency = None
			self.external_agency_contact_person = None
			self.external_agency_contact_number = None
			self.external_agency_contact_email = None
			self.proof_of_visit = None

	def _compute_overall_status(self):
		# Combines this Background Check's two independent inputs: the
		# Result (outcome), driven entirely by References, and the
		# Compliance Checks table, which lives directly on this document
		# and is therefore always current here — no separate freshness
		# check is needed for that half, unlike outcome, which depends on
		# external Reference documents (see
		# _require_result_is_fresh). A compliance failure is treated as an
		# absolute disqualifier that good references can't outweigh, not
		# just another input averaged in alongside them.
		any_failed_compliance = any(row.status == "Failed" for row in self.compliance_checks)
		all_cleared_compliance = all(row.status == "Cleared" for row in self.compliance_checks)

		if self.outcome == "Failed" or any_failed_compliance:
			self.overall_status = "Failed"
		elif self.outcome == "Passed" and all_cleared_compliance:
			self.overall_status = "Passed"
		else:
			self.overall_status = "Needs Review"

	def before_submit(self):
		self._require_compliance_check_template_still_enabled()
		self._require_compliance_check_rows_not_disabled()
		self._require_mandatory_compliance_fields()
		self._require_all_compliance_checks_resolved()
		self._require_references_ready()
		self._require_verification_source_complete()
		self._require_result_is_fresh()

	def _require_verification_source_complete(self):
		# Whichever side of the toggle applies must actually be filled in
		# before submitting — deferred to submit time (not validate()), same
		# as every other completeness check on this doctype, so a draft can
		# still be saved before this is decided.
		if self.conducted_by_external_agency:
			missing = []
			if not self.external_agency:
				missing.append(frappe._("External Agency"))
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
		elif not self.conducted_by:
			frappe.throw(frappe._("Conducted By must have at least one user before submitting."), frappe.MandatoryError)

	def _require_references_ready(self):
		# A cancelled Reference doesn't count as "existing" here — a
		# Background Check whose only References were all cancelled is
		# treated the same as one with no References at all.
		if not frappe.db.exists("Vendor Background Check Reference", {"background_check": self.name, "docstatus": ["!=", 2]}):
			frappe.throw(frappe._("Add at least one Reference before submitting."))
		if frappe.db.exists("Vendor Background Check Reference", {"background_check": self.name, "docstatus": 0}):
			frappe.throw(
				frappe._(
					"Every Reference must be submitted before submitting this Background Check — none can be"
					" left in draft."
				)
			)
		if not frappe.db.exists("Vendor Background Check Reference", {"background_check": self.name, "docstatus": 1}):
			frappe.throw(frappe._("At least one Reference must be submitted before submitting this Background Check."))
		if self.outcome == "Needs Review":
			frappe.throw(
				frappe._(
					"This Background Check's Result is still \"Needs Review\" — every Reference must be fully"
					" scored (Passed or Failed) before submitting."
				)
			)

	@frappe.whitelist()
	def check_submit_readiness(self):
		# Dry-runs every before_submit() precondition except the freshness
		# check (reported separately below, since the client already
		# treats that with its own "Resave" prompt, distinct from a plain
		# error) — calling the exact same private methods before_submit()
		# itself calls, so the two can never quietly drift apart. Lets the
		# client surface a real blocking problem *before* showing the
		# Overall-Status-Failed confirmation, so a "Yes, submit anyway"
		# click can't be immediately followed by an unrelated failure.
		structural_error = None
		try:
			self._require_compliance_check_template_still_enabled()
			self._require_compliance_check_rows_not_disabled()
			self._require_mandatory_compliance_fields()
			self._require_all_compliance_checks_resolved()
			self._require_references_ready()
			self._require_verification_source_complete()
		except frappe.ValidationError as e:
			structural_error = str(e)
			# frappe.throw() queues its message for automatic client-side
			# display in addition to raising — catching the exception here
			# doesn't undo that, so without this the client would show the
			# message twice: once from Frappe's own auto-display, and once
			# more from the explicit frappe.msgprint() built around
			# structural_error above.
			frappe.clear_last_message()

		freshness = self.check_result_is_fresh()

		return {
			"structural_error": structural_error,
			"is_fresh": freshness["is_fresh"],
			"overall_status": self.overall_status,
		}

	def _require_result_is_fresh(self):
		# Last line of defense: everything above already confirmed the
		# structural preconditions (references exist, none are draft, a
		# threshold exists) — this is the one check that's actually
		# expensive (a full dry-run recompute), so it deliberately runs
		# last. The client's own before_submit already calls
		# check_result_is_fresh() and prompts the user to resave rather
		# than silently letting a stale value through — this is the
		# server-side backstop in case that client-side prompt was somehow
		# bypassed (a direct API call, for instance).
		fresh_result, fresh_method, fresh_references = _compute_aggregate(self.name)
		if not self._is_same_result(fresh_result, fresh_method, fresh_references):
			frappe.throw(
				frappe._(
					"This Background Check's Result is out of date — something it depends on (a Reference,"
					" or a Vendor Lifecycle Settings value) has changed since it was last saved. Save this"
					" document again before submitting."
				)
			)

	def _is_same_result(self, fresh_result, fresh_method, fresh_references):
		# Compares every value that can independently go stale — not just
		# the overall Result, but the resolved method (which decides how
		# References combine) and each individual Reference's own row in the
		# Reference Summary table. A margin-based method ("Majority of
		# References") can absorb one reference's own result flipping
		# without flipping the overall Result — so checking only the overall
		# value would miss that one row being stale in the summary table.
		if fresh_result != self.outcome:
			return False
		if fresh_method != self.result_method_resolved:
			return False
		current_summary = {
			row.reference: (round(row.average_rating or 0, 1), row.outcome) for row in self.reference_summary
		}
		fresh_summary = {
			ref.name: (round(ref.average_rating or 0, 1), ref.outcome) for ref in fresh_references
		}
		return current_summary == fresh_summary

	@frappe.whitelist()
	def check_result_is_fresh(self):
		fresh_result, fresh_method, fresh_references = _compute_aggregate(self.name)
		return {"is_fresh": self._is_same_result(fresh_result, fresh_method, fresh_references)}

	def _warn_on_flagged_or_failed_compliance_checks(self):
		# A warning, not a block — the References/Ratings are still what
		# determine Pass/Fail; a flagged or failed compliance check is
		# surfaced for the reviewer's own judgment rather than
		# auto-failing the check. One consolidated message naming every
		# such row, rather than a separate msgprint per check, since any
		# number of rows (including custom check types a deployment added
		# itself) could need attention at once.
		#
		# Skipped when this save is only an internal side effect of a
		# Reference being touched (see recompute_overall_result) — that
		# happens inside whatever request the Reference itself is being
		# saved/submitted/cancelled in, so this warning would otherwise
		# leak into that unrelated response, looking like feedback about
		# the Reference rather than this Background Check.
		if self.flags.ignore_compliance_check_warning:
			return
		flagged = [row.check_type for row in self.compliance_checks if row.status in ("Flagged", "Failed")]
		if flagged:
			frappe.msgprint(
				frappe._(
					"The following compliance checks are flagged or failed — review before proceeding"
					" with this vendor: {0}"
				).format(", ".join(frappe.bold(name) for name in flagged)),
				title=frappe._("Compliance Checks Need Review"),
				indicator="red",
			)

	def _require_compliance_check_template_still_enabled(self):
		# The Link field's own filter only keeps a disabled template out of
		# the picker going forward — it doesn't stop this Background Check
		# from still pointing at one it picked before that template was
		# disabled, so this is checked again here at submit time.
		if self.compliance_check_template and frappe.db.get_value(
			"Compliance Check Template", self.compliance_check_template, "disabled"
		):
			frappe.throw(
				frappe._("Compliance Check Template {0} is disabled — pick a different one before submitting.").format(
					frappe.bold(self.compliance_check_template)
				)
			)

	def _require_compliance_check_rows_not_disabled(self):
		# The Check Type / Source Link fields' own filters only keep a
		# disabled record out of the picker going forward — a row can still
		# be pointing at one that was enabled when picked and got disabled
		# afterward, so this is checked again here, fresh, at submit time
		# (same pattern as _require_compliance_check_template_still_enabled).
		check_types = {row.check_type for row in self.compliance_checks if row.check_type}
		sources = {row.source for row in self.compliance_checks if row.source}

		disabled_check_types = set(
			frappe.get_all(
				"Compliance Check Type", filters={"name": ["in", list(check_types)], "disabled": 1}, pluck="name"
			)
		) if check_types else set()
		disabled_sources = set(
			frappe.get_all(
				"Compliance Check Source", filters={"name": ["in", list(sources)], "disabled": 1}, pluck="name"
			)
		) if sources else set()

		for row in self.compliance_checks:
			problems = []
			if row.check_type in disabled_check_types:
				problems.append(frappe._("Check Type {0} is disabled").format(frappe.bold(row.check_type)))
			if row.source in disabled_sources:
				problems.append(frappe._("Source {0} is disabled").format(frappe.bold(row.source)))
			if problems:
				frappe.throw(
					frappe._("Row #{0}: {1} — pick a different one before submitting.").format(
						row.idx, "; ".join(problems)
					)
				)

	def _require_mandatory_compliance_fields(self):
		# Which fields are mandatory is configured per Check Type on the
		# Template itself (Attachment/Source/Reference Mandatory) — looked
		# up fresh here rather than trusting a copy made when the template
		# was first loaded, since editing the Template's tick-boxes after
		# that point must take effect immediately, not only the next time
		# someone happens to re-load it.
		if not self.compliance_check_template:
			return
		mandatory_by_check_type = {
			item.check_type: item
			for item in frappe.get_all(
				"Compliance Check Template Item",
				filters={"parent": self.compliance_check_template},
				fields=["check_type", "attachment_mandatory", "source_mandatory", "reference_mandatory"],
			)
		}
		for row in self.compliance_checks:
			template_item = mandatory_by_check_type.get(row.check_type)
			if not template_item:
				continue
			missing = []
			if template_item.attachment_mandatory and not row.attachment:
				missing.append(frappe._("Supporting Document"))
			if template_item.source_mandatory and not row.source:
				missing.append(frappe._("Source"))
			if template_item.reference_mandatory and not row.reference_value:
				missing.append(frappe._("Reference / Value"))
			if missing:
				frappe.throw(
					frappe._("Row #{0} ({1}): {2} must be filled in before submitting.").format(
						row.idx, frappe.bold(row.check_type), ", ".join(missing)
					)
				)

	def _require_all_compliance_checks_resolved(self):
		# Every check must be actively resolved one way or the other before
		# submitting — Not Started, In Progress, and Flagged are all
		# left-hanging states; a Flagged row must be pushed to Cleared or
		# Failed by the reviewer first, not carried through unresolved.
		unresolved = [row for row in self.compliance_checks if row.status not in ("Cleared", "Failed")]
		if unresolved:
			rows = ", ".join(
				frappe._("Row #{0} ({1}): {2}").format(row.idx, frappe.bold(row.check_type), row.status)
				for row in unresolved
			)
			frappe.throw(
				frappe._("Every Compliance Check must be Cleared or Failed before submitting — still unresolved: {0}").format(
					rows
				)
			)

	def on_submit(self):
		if self.overall_status == "Failed":
			self._handle_failed_result()
			self._notify_outcome(DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE)
			return

		if self.vendor and self.overall_status == "Passed":
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_status", VENDOR_LIFECYCLE_STATUS_BACKGROUND_VERIFIED)
		self._notify_outcome(DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE)

	def _notify_outcome(self, template_name):
		try:
			contact = get_kyc_vendor_contact(self.kyc) if self.kyc else {}
			context = {
				"firm_name": contact.get("firm_name"),
				"contact_person_name": contact.get("contact_person_name"),
				"company_name": resolve_vendor_lifecycle_company_name(),
				"stage_name": "Background Check",
			}
			if template_name == DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE:
				context["next_stage"] = "Compliance Audit"
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=template_name,
				context=context,
				recipients=[contact["official_email"]] if contact.get("official_email") else [],
			)
		except Exception:
			frappe.log_error(
				title="Vendor Background Check: failed to send outcome email", message=frappe.get_traceback()
			)

	def on_cancel(self):
		# Without this, Frappe's generic "linked document" check
		# (check_no_back_links_exist) blocks cancelling this Background Check
		# outright, because its own submitted References still link back to
		# it — and at least one submitted Reference is guaranteed to exist,
		# since before_submit requires one. References intentionally stay
		# locked and untouched by this cancellation (see
		# _require_background_check_in_draft on the Reference) rather than
		# being cancelled along with it, so this is safe to ignore. Same
		# pattern ERPNext itself uses (e.g. Purchase Order.on_cancel).
		self.ignore_linked_doctypes = ("Vendor Background Check Reference",)
		self._revert_disable_if_this_was_the_failed_one()

	def _revert_disable_if_this_was_the_failed_one(self):
		# Cancelling this Background Check retracts its own consequences —
		# if it was the one that disabled the Supplier, that disable should
		# lift too, unless some *other* still-submitted Background Check
		# for the same vendor is also Failed (only possible from data that
		# predates the one-active-Background-Check-per-vendor rule above;
		# structurally impossible for anything created after it), in which
		# case the Supplier must stay disabled on that one's account.
		if self.overall_status != "Failed" or not self.vendor:
			return
		other_failed_exists = frappe.db.exists(
			"Vendor Background Check",
			{"vendor": self.vendor, "docstatus": 1, "overall_status": "Failed", "name": ["!=", self.name]},
		)
		if not other_failed_exists:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 0)

	def _handle_failed_result(self):
		# The Supplier is always created at Vendor KYC submission, so it
		# already exists by the time any Background Check is submitted —
		# this just disables it. enforce_sequential_creation() already
		# blocks the next stage from starting without a "Passed" Background
		# Check, so no separate stage-blocking logic is needed here.
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 1)

	@frappe.whitelist()
	def force_override(self, reason):
		"""Lets a System Manager / Vendor Lifecycle Manager push past a
		Failed result so later stages become creatable again, without
		pretending the result was actually Passed. See
		stage_sequencing.force_override_stage for the shared permission
		check, mandatory-reason enforcement, and Supplier re-enable logic."""
		if self.overall_status != "Failed":
			frappe.throw(frappe._("Only a Failed result can be force-overridden."))
		force_override_stage(self, reason)


def recompute_overall_result(background_check_name, exclude_name=None):
	"""Combines every linked Reference's own Result into one overall
	Result on the Background Check, using whichever method Vendor
	Lifecycle Settings is configured with, and updates the read-only
	Reference Summary table in place so every linked Reference's key info
	+ id shows up on the Background Check itself. Called from the
	Reference doctype's own on_update/on_trash — References are
	independent documents (a child table row can't have its own nested
	child table, so Ratings couldn't live inside an inline References
	grid), so none of this can be computed inline during the Background
	Check's own validate() the way a normal child table would.

	Updates each existing summary row's fields in place (matched by its
	`reference` link) rather than wiping the whole table and re-appending
	fresh rows every time — a full wipe-and-rebuild gives every row a new
	name each time, so Frappe's change tracking sees "removed everything,
	added everything back" on every single Reference edit, flooding Track
	Changes even when only one Reference actually changed. Preserving row
	identity here means an unaffected row produces no diff at all, and a
	genuinely changed row shows up as one clean update instead of a
	remove+add pair.

	Loads and resaves the full document (rather than a bare
	frappe.db.set_value) so the Reference Summary table — a plain,
	top-level child table, unaffected by the no-grandchild-tables
	limitation above since Vendor Background Check itself isn't a child
	doctype — can be updated at the same time. A Reference locks itself
	once this document's Background Check is no longer a draft (see
	_require_background_check_in_draft on the Reference), so this only
	ever runs while still a draft, when a normal save is allowed.

	Cancelled References (docstatus 2) are excluded entirely — they don't
	appear in the summary table and don't count toward the Result. Draft
	References (docstatus 0) still appear in the summary table for
	visibility, but — like cancelled ones — don't count toward the Result;
	only submitted References (docstatus 1) actually factor into the
	Each Reference Must Pass / Majority of References calculation.

	`exclude_name` is set only when called from a Reference's own
	on_trash — Frappe runs on_trash *before* the row is actually removed
	from the database, so without excluding it explicitly here, the
	summary would still include the about-to-be-deleted Reference, leaving
	a dangling Link once it's actually gone."""
	result, method, references = _compute_aggregate(background_check_name, exclude_name)

	doc = frappe.get_doc("Vendor Background Check", background_check_name)
	existing_rows_by_reference = {row.reference: row for row in doc.reference_summary}
	fresh_reference_names = {ref.name for ref in references}

	# Drop rows for References that no longer belong here (cancelled,
	# deleted, or excluded) before re-attaching the still-current ones —
	# whatever's left below is only ever updated in place, never replaced.
	doc.set("reference_summary", [row for row in doc.reference_summary if row.reference in fresh_reference_names])

	for ref in references:
		row = existing_rows_by_reference.get(ref.name)
		if row:
			row.reference_name = ref.reference_name
			row.reference_contact = ref.reference_contact
			row.average_rating = ref.average_rating
			row.result_method_resolved = ref.result_method_resolved
			row.outcome = ref.outcome
		else:
			doc.append("reference_summary", {
				"reference": ref.name,
				"reference_name": ref.reference_name,
				"reference_contact": ref.reference_contact,
				"average_rating": ref.average_rating,
				"result_method_resolved": ref.result_method_resolved,
				"outcome": ref.outcome,
			})

	doc.outcome = result
	doc.result_method_resolved = method
	# This save is an internal side effect of touching a Reference, not
	# someone actually looking at the Background Check itself — without
	# this flag, the flagged/failed Compliance Checks warning below would
	# fire on every single Reference save/submit/cancel, showing up as if
	# it were feedback about the Reference just acted on.
	doc.flags.ignore_compliance_check_warning = True
	doc.save(ignore_permissions=True)


def _compute_aggregate(background_check_name, exclude_name=None):
	"""Pure computation, no persistence — returns (result, method,
	references) for the given Background Check right now. Shared by
	recompute_overall_result() (which persists the result) and
	check_result_is_fresh() (which only compares against it, to detect a
	stale Result before letting the document submit)."""
	method = frappe.db.get_single_value("Vendor Lifecycle Settings", "background_check_result_method") or "Each Reference Must Pass"

	filters = {"background_check": background_check_name, "docstatus": ["!=", 2]}
	if exclude_name:
		filters["name"] = ["!=", exclude_name]
	references = frappe.get_all(
		"Vendor Background Check Reference",
		filters=filters,
		fields=[
			"name", "reference_name", "reference_contact", "outcome", "average_rating",
			"result_method_resolved", "docstatus",
		],
	)
	submitted_references = [ref for ref in references if ref.docstatus == 1]

	if any(ref.docstatus == 0 for ref in references):
		# A draft Reference means the check isn't actually complete yet —
		# stays "Needs Review" regardless of what the already-submitted
		# ones show, rather than resolving early off a partial picture.
		return "Needs Review", method, references

	result = _compute_pass_count_method(method, submitted_references)
	return result, method, references


def _compute_pass_count_method(method, references):
	reference_results = [ref.outcome for ref in references]
	if not reference_results or "Needs Review" in reference_results:
		return "Needs Review"

	if method == "Each Reference Must Pass":
		return "Passed" if all(r == "Passed" for r in reference_results) else "Failed"
	else:  # "Majority of References" — a tie counts as Failed.
		passed_count = reference_results.count("Passed")
		failed_count = reference_results.count("Failed")
		return "Passed" if passed_count > failed_count else "Failed"
