# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.stage_sequencing import get_available_stages
from vendor_lifecycle.vendor_lifecycle.state_validation import (
	require_state_for_india,
	validate_indian_state_spelling,
	validate_state_master_matches_country,
)
from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	SUPPLIER_FIELD_MAP,
	maybe_create_vendor,
	resolve_vendor_lifecycle_company_name,
	send_vendor_lifecycle_email,
)

DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Passed"
DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Failed"

# Hardcoded, by explicit product decision — no longer a Vendor Lifecycle
# Settings field (see can_toggle_supplier_freeze below).
SUPPLIER_FREEZE_TOGGLE_ROLES = {"System Manager", "Vendor Lifecycle Manager"}

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# Used only as a fallback when india_compliance isn't installed — format-only,
# no checksum. india_compliance's own validate_gstin() is preferred whenever
# it's available, since it also validates the check digit.
GSTIN_FORMAT_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

BANK_DETAIL_FIELDS = ["bank", "bank_account_no", "ifsc_branch_code", "bank_account_name"]
ADDRESS_DETAIL_FIELDS = ["address_line_1", "city", "state", "country", "pincode"]
# A field literally named "country" silently inherits the site's global
# default (frappe.db.get_default("country")) on every new document, so it's
# excluded from the set that *triggers* the group-mandatory rule below —
# otherwise the address section would look "started" on every single new
# Vendor KYC, whether or not anyone actually touched it.
ADDRESS_TRIGGER_FIELDS = ["address_line_1", "city", "state", "pincode"]


class VendorKYC(Document):
	def validate(self):
		self._enforce_rejected_is_frozen()
		self._enforce_creation_source()
		self._enforce_onboarding_request_mandatory()
		self._enforce_request_submitted()
		self._enforce_onboarding_request_immutable()
		self._check_duplicate_kyc_for_request()
		self._clear_unused_verifier_field()
		# Rejecting is deliberately allowed on an incomplete KYC — that's the
		# whole point of being able to reject one instead of forcing it to be
		# filled in first — so these two content-completeness checks stand
		# down for that one save. See reject() below.
		if not self.flags.get("rejecting"):
			self._validate_pan_gstin()
			self._enforce_group_mandatory()
			self._enforce_bank_account_no_mandatory()
		self._validate_state()
		self._validate_establishment_date()

	def _validate_establishment_date(self):
		if self.establishment_date and frappe.utils.getdate(self.establishment_date) > frappe.utils.getdate():
			frappe.throw(frappe._("Establishment Date cannot be in the future."))

	def _clear_unused_verifier_field(self):
		# Only one of internal Verified By / external agency ever applies at
		# a time — enforced here too, not just by the client-side toggle, so
		# a stale value from before a switch (or one set via the API
		# directly) can never linger in the now-irrelevant field.
		if self.verified_by_external_agency:
			self.verified_by = []
		else:
			self.external_verification_agency = None
			self.external_agency_contact_person = None
			self.external_agency_contact_number = None
			self.external_agency_contact_email = None
			self.proof_of_visit = None

	def _enforce_rejected_is_frozen(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and before.status == "Rejected":
			frappe.throw(frappe._("This Vendor KYC has been rejected and can no longer be edited."))

	@frappe.whitelist()
	def reject(self):
		"""Manual reject path for when there's no Frappe Workflow configured
		on this doctype (see the "Vendor KYC Reject Button" client script,
		which checks frappe.model.has_workflow() before even offering this).
		Deliberately a plain save, not a submit — the document stays at
		docstatus 0 forever; _enforce_rejected_is_frozen() above is what
		actually locks it once status is Rejected, and before_submit() below
		refuses to let a rejected KYC be submitted."""
		if self.docstatus != 0:
			frappe.throw(frappe._("Only a Vendor KYC that hasn't been submitted yet can be rejected."))
		if self.status == "Rejected":
			return
		self.flags.rejecting = True
		# Bypasses Frappe's own core required-field checks too, not just the
		# custom PAN/bank-details rules above — a reviewer rejecting a KYC
		# shouldn't be forced to first supply a valid value for a field they
		# might be clearing out precisely because it turned out to be wrong.
		self.flags.ignore_mandatory = True
		self.status = "Rejected"
		self.save()
		self._notify_rejected()

	def _notify_rejected(self):
		try:
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE,
				context={
					"firm_name": self.firm_name,
					"contact_person_name": self.contact_person_name,
					"company_name": resolve_vendor_lifecycle_company_name(),
					"stage_name": "KYC",
				},
				recipients=[self.official_email] if self.official_email else [],
			)
		except Exception:
			frappe.log_error(title="Vendor KYC: failed to send rejected email", message=frappe.get_traceback())

	def _enforce_creation_source(self):
		# onboarding_request being set is itself sufficient proof this came
		# from a real request — Start KYC now hands the reviewer a pre-filled,
		# unsaved form (see Vendor Onboarding Request's start_kyc()) rather
		# than inserting server-side, so there's no request/response boundary
		# left to carry an in-memory flag across. The flag is kept only as a
		# convenience for internal/test callers that construct a KYC with no
		# onboarding_request at all.
		if not self.is_new() or self.flags.get("via_onboarding_request") or self.onboarding_request:
			return
		if frappe.db.get_single_value("Vendor Lifecycle Settings", "require_onboarding_request_for_kyc"):
			frappe.throw(
				frappe._(
					'Direct creation of Vendor KYC is disabled. Please use the "Start KYC" button on the relevant Vendor Onboarding Request.'
				),
				frappe.PermissionError,
			)

	def _enforce_onboarding_request_mandatory(self):
		# Onboarding Request is only mandatory when Settings requires it for
		# KYC creation — when that's off, direct creation without any request
		# reference is allowed. The reqd property isn't set statically on the
		# field for this reason; the Desk UI toggles it via the same setting
		# (see the "Vendor KYC Direct Create Notice" client script).
		if not self.is_new() or self.flags.get("via_onboarding_request"):
			return
		if frappe.db.get_single_value("Vendor Lifecycle Settings", "require_onboarding_request_for_kyc") and not self.onboarding_request:
			frappe.throw(frappe._("Onboarding Request is mandatory."), frappe.MandatoryError)

	def _enforce_request_submitted(self):
		if not self.is_new() or not self.onboarding_request:
			return
		request_docstatus = frappe.db.get_value("Vendor Onboarding Request", self.onboarding_request, "docstatus")
		if request_docstatus != 1:
			frappe.throw(
				frappe._("The linked Onboarding Request must be submitted before starting a Vendor KYC.")
			)

	def _enforce_onboarding_request_immutable(self):
		if self.is_new():
			return
		if self.has_value_changed("onboarding_request"):
			frappe.throw(frappe._("Onboarding Request cannot be changed once a Vendor KYC has been created."))

	def _check_duplicate_kyc_for_request(self):
		if not self.onboarding_request:
			return

		handling = frappe.db.get_single_value("Vendor Lifecycle Settings", "duplicate_kyc_handling") or "Stop"
		if handling == "Ignore":
			return

		# A Rejected KYC counts as "already exists" the same as any other —
		# whether a fresh attempt is allowed after a rejection is entirely
		# up to this setting, same as any other duplicate. "Stop" blocks
		# it; switching to "Ignore" or "Warn" is the only way to allow one.
		duplicate = frappe.db.get_value(
			"Vendor KYC",
			{"name": ["!=", self.name or ""], "onboarding_request": self.onboarding_request},
			"name",
		)
		if not duplicate:
			return

		message = frappe._("Another Vendor KYC ({0}) already exists for this Onboarding Request.").format(
			frappe.bold(duplicate)
		)

		if handling == "Stop":
			frappe.throw(message)
		else:
			frappe.msgprint(message, title=frappe._("Duplicate Vendor KYC"), indicator="orange")

	def _validate_pan_gstin(self):
		if self.gstin_uin:
			gstin = self.gstin_uin.strip().upper()
			if "india_compliance" in frappe.get_installed_apps():
				from india_compliance.gst_india.utils import validate_gstin

				validate_gstin(gstin, label="GSTIN")
			elif not GSTIN_FORMAT_REGEX.match(gstin):
				frappe.throw(frappe._("{0} is not a valid GSTIN.").format(frappe.bold(self.gstin_uin)))
			self.gstin_uin = gstin

			# A GSTIN's own structure embeds the PAN in it (characters 3-12) —
			# PAN Card is always derived from it, never independently kept,
			# so there's no mismatch state to catch. PAN stays a normal,
			# editable field on the form (with a client script previewing
			# this live as GSTIN is typed) — this is what actually
			# guarantees the saved value is correct, regardless of whether
			# that client-side preview ran at all (e.g. GSTIN arriving
			# pre-filled from "Start KYC" rather than being typed in).
			self.pan_card = gstin[2:12]

		# mandatory_depends_on is desk-UI-only in this Frappe version — it
		# never blocks a save via the API, so PAN/Tax ID's conditional
		# mandatory-ness (see the "Vendor KYC" doctype JSON) has to be
		# enforced here too, same as Vendor Onboarding Request already does
		# for its own conditional fields.
		if self.country == "India" and not self.pan_card:
			frappe.throw(frappe._("PAN Card is mandatory."), frappe.MandatoryError)
		if self.country != "India" and not self.tax_id:
			frappe.throw(frappe._("Tax ID is mandatory."), frappe.MandatoryError)

		if self.country == "India" and self.pan_card:
			pan = self.pan_card.strip().upper()
			if not PAN_REGEX.match(pan):
				frappe.throw(
					frappe._("{0} is not a valid PAN. Expected format: AAAAA9999A.").format(frappe.bold(self.pan_card))
				)
			self.pan_card = pan

	def _validate_state(self):
		# State is picked via state_master (a Link to the "State" master,
		# filtered by Country on the client) — state itself stays a plain
		# Data field, auto-filled from state_master's own state_name via
		# fetch_from, so this always ends up storing the bare official
		# name (e.g. "Karnataka"), never State's own compound docname
		# (e.g. "Karnataka (India)"), which would break Address creation
		# and GSTIN validation further down.
		validate_state_master_matches_country(self)
		validate_indian_state_spelling(self)

	def _enforce_group_mandatory(self):
		settings = frappe.get_single("Vendor Lifecycle Settings")

		if settings.require_complete_bank_details:
			self._throw_if_partially_filled(BANK_DETAIL_FIELDS, BANK_DETAIL_FIELDS, frappe._("Bank Details"))

		if settings.require_complete_address_details:
			self._throw_if_partially_filled(ADDRESS_DETAIL_FIELDS, ADDRESS_TRIGGER_FIELDS, frappe._("Address"))

	def _enforce_bank_account_no_mandatory(self):
		# Independent of "Require Complete Bank Details" above — Bank Account
		# No. is always required the moment Bank or Bank Account Name is
		# filled in, regardless of that setting. Without this, a Bank
		# Account record could get created with no actual account number
		# and no error at all, since neither Frappe's own Bank Account
		# doctype nor the automatic-creation code requires one.
		if (self.bank or self.bank_account_name) and not self.bank_account_no:
			frappe.throw(
				frappe._("Bank Account No. is mandatory once Bank or Bank Account Name is filled in."),
				frappe.MandatoryError,
			)

	def _throw_if_partially_filled(self, fieldnames, trigger_fieldnames, group_label):
		if not any(self.get(f) for f in trigger_fieldnames):
			return
		missing = [f for f in fieldnames if not self.get(f)]
		if not missing:
			return
		labels = [frappe.bold(self.meta.get_label(f)) for f in missing]
		frappe.throw(
			frappe._("{0}: once you fill in part of this section, the rest is required too. Missing: {1}").format(
				group_label, ", ".join(labels)
			),
			frappe.MandatoryError,
		)

	@frappe.whitelist()
	def can_fetch_gstin_details(self):
		"""Whether "Fetch Address from GSTIN" (and the fields it fills in)
		can actually work right now — used by the client script to hide
		that whole section rather than let it fail with an error only after
		being clicked."""
		if "india_compliance" not in frappe.get_installed_apps():
			return False
		from india_compliance.gst_india.utils import is_api_enabled

		return bool(is_api_enabled())

	@frappe.whitelist()
	def fetch_gstin_details(self):
		"""Look up the GSTIN-registered address via India Compliance, if it's
		installed. Only populates the read-only gstin_* fields for review —
		the reviewer decides whether to use it (via the "Address Same as
		GSTIN" checkbox or by copying it into the address fields above)."""
		if "india_compliance" not in frappe.get_installed_apps():
			frappe.throw(
				frappe._("The India Compliance app is not installed, so GSTIN address lookup isn't available.")
			)
		if not self.gstin_uin:
			frappe.throw(frappe._("Enter a GSTIN first."))

		from india_compliance.gst_india.utils.gstin_info import get_gstin_info

		info = get_gstin_info(self.gstin_uin)
		address = info.get("permanent_address") or {}

		self.gstin_address_line = address.get("address_line1") or ""
		self.gstin_city = address.get("city") or ""
		self.gstin_state = address.get("state") or ""
		self.gstin_pincode = address.get("pincode") or ""

		return {
			"business_name": info.get("business_name"),
			"address_line": self.gstin_address_line,
			"city": self.gstin_city,
			"state": self.gstin_state,
			"pincode": self.gstin_pincode,
		}

	def before_submit(self):
		if self.status == "Rejected":
			frappe.throw(frappe._("A rejected Vendor KYC cannot be submitted."))
		self._require_billing_currency()
		require_state_for_india(self)
		self._require_verification_source_complete()
		self._enforce_supplier_mandatory_fields()

	def _require_verification_source_complete(self):
		# Whichever side of the toggle applies must actually be filled in
		# before submitting — deferred to submit time (not validate()), same
		# as every other completeness check in this app, so a draft can
		# still be saved before this is decided.
		if self.verified_by_external_agency:
			missing = []
			if not self.external_verification_agency:
				missing.append(frappe._("External Verification Agency"))
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
		elif not self.verified_by:
			frappe.throw(frappe._("Verified By must have at least one user before submitting."), frappe.MandatoryError)

	def _require_billing_currency(self):
		# Independently mandatory here regardless of Supplier's own
		# default_currency field (not reqd in core ERPNext, so
		# _enforce_supplier_mandatory_fields below wouldn't catch this on
		# its own) — deferred to submit time, not validate(), so a draft
		# can still be saved before it's decided.
		if not self.billing_currency:
			frappe.throw(frappe._("Billing Currency is mandatory before submitting."), frappe.MandatoryError)

	def _enforce_supplier_mandatory_fields(self):
		supplier_meta = frappe.get_meta("Supplier")
		missing_labels = []
		for kyc_fieldname, supplier_fieldname in SUPPLIER_FIELD_MAP.items():
			supplier_field = supplier_meta.get_field(supplier_fieldname)
			if not supplier_field or not supplier_field.reqd:
				continue
			if not self.get(kyc_fieldname):
				missing_labels.append(frappe.bold(self.meta.get_label(kyc_fieldname)))

		if missing_labels:
			frappe.throw(
				frappe._(
					"These fields are mandatory on the Supplier master and must be filled in before submitting: {0}"
				).format(", ".join(missing_labels)),
				frappe.MandatoryError,
			)

	@frappe.whitelist()
	def get_available_stages(self):
		"""Which of Background Check / Compliance Audit / Sampling
		Evaluation / Sign Off can currently be created for this KYC — powers
		the "Create" dropdown's conditional options. Empty once the KYC
		itself isn't submitted yet, since none of them make sense before
		that regardless of Settings."""
		if self.docstatus != 1:
			return {"stages": [], "background_check_failed": False, "compliance_audit_failed": False}
		return get_available_stages(self.name)

	@frappe.whitelist()
	def can_toggle_supplier_freeze(self):
		"""Whether the current user can see/use the Freeze/Unfreeze Supplier
		button — hardcoded to System Manager / Vendor Lifecycle Manager, by
		explicit product decision (no longer a Vendor Lifecycle Settings
		field). Also called from inside toggle_supplier_freeze() itself
		below, so this is the real security boundary, not just a UI
		convenience."""
		return bool(SUPPLIER_FREEZE_TOGGLE_ROLES & set(frappe.get_roles()))

	@frappe.whitelist()
	def get_supplier_freeze_button_info(self):
		"""What the client script needs to decide whether to show the
		Freeze/Unfreeze Supplier button and which label to use — a single
		round trip covering both the permission check and the Supplier's
		current state."""
		if not self.supplier or not self.can_toggle_supplier_freeze():
			return {"show": False}
		is_frozen = frappe.db.get_value("Supplier", self.supplier, "is_frozen")
		return {"show": True, "is_frozen": bool(is_frozen)}

	@frappe.whitelist()
	def toggle_supplier_freeze(self):
		"""Flips is_frozen on the linked Supplier. Suppliers created through
		this app get is_frozen made read-only on their own form (see the
		Property Setter), so this button — and Sign Off's own automatic
		unfreeze — are the only ways to change it."""
		if not self.can_toggle_supplier_freeze():
			frappe.throw(
				frappe._("You don't have permission to freeze or unfreeze this Supplier."), frappe.PermissionError
			)
		if not self.supplier:
			frappe.throw(frappe._("This Vendor KYC has no linked Supplier yet."))

		is_frozen = frappe.db.get_value("Supplier", self.supplier, "is_frozen")
		new_value = 0 if is_frozen else 1
		frappe.db.set_value("Supplier", self.supplier, "is_frozen", new_value)
		return {"is_frozen": new_value}

	def on_submit(self):
		# db_set, not a plain assignment — by the time on_submit() runs,
		# Frappe has already written this document to the database for
		# this request (db_update() happens before run_post_save_methods()
		# calls on_submit()), so a bare self.status = ... here would only
		# ever change the in-memory value for the rest of this request and
		# never actually persist, leaving every submitted KYC stuck
		# showing its original default ("In Progress") forever.
		self.db_set("status", "Verified")
		maybe_create_vendor(self)
		self._notify_completed()

	def _notify_completed(self):
		try:
			next_stages = get_available_stages(self.name).get("stages") or []
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE,
				context={
					"firm_name": self.firm_name,
					"contact_person_name": self.contact_person_name,
					"company_name": resolve_vendor_lifecycle_company_name(),
					"stage_name": "KYC",
					"next_stage": " / ".join(next_stages) if next_stages else None,
				},
				recipients=[self.official_email] if self.official_email else [],
			)
		except Exception:
			frappe.log_error(title="Vendor KYC: failed to send completed email", message=frappe.get_traceback())

	def before_cancel(self):
		# Frappe's own generic check_if_doc_is_linked would only ever catch
		# the 4 submittable stage doctypes below (and only report the first
		# one it happens to find) — this instead lists every single linked
		# document across all relationships in one message, so the reviewer
		# knows exactly what to delete/unlink before trying again, not just
		# "something, somewhere, is still linked."
		#
		# Supplier/Contact/Address/Bank Account are all checked explicitly
		# here, by name, rather than relying on Frappe's own generic check —
		# their own "vendor_kyc" fields (and this KYC's own
		# supplier/contact/address/bank_account fields) are plain Data
		# fields, not real Links (deliberately, so a mistaken/test vendor
		# can actually be torn down — see the field-type change), so
		# Frappe's own link-integrity system no longer knows about these
		# relationships at all. This check is the only thing protecting a
		# real vendor's KYC from being cancelled while its own records are
		# still sitting there.
		linked = []
		for doctype in (
			"Vendor Background Check",
			"Vendor Compliance Audit",
			"Vendor Sampling Evaluation",
			"Vendor Sign Off",
		):
			linked += [
				frappe._("{0} {1}").format(doctype, name)
				for name in frappe.get_all(doctype, filters={"kyc": self.name, "docstatus": ["!=", 2]}, pluck="name")
			]
		for doctype in ("Supplier", "Contact", "Address", "Bank Account"):
			linked += [
				frappe._("{0} {1}").format(doctype, name)
				for name in frappe.get_all(doctype, filters={"vendor_kyc": self.name}, pluck="name")
			]

		if linked:
			frappe.throw(
				frappe._(
					"This Vendor KYC cannot be cancelled while these documents are still linked to it — delete"
					" or unlink them first: {0}"
				).format(", ".join(frappe.bold(name) for name in linked))
			)

		# Nothing was found above, so nothing needs to be bypassed here for
		# real — set purely so Frappe's own generic check (which would
		# otherwise re-check these same 4 relationships a second time,
		# redundantly) never has a chance to fire its own, less informative
		# error instead.
		self.ignore_linked_doctypes = (
			"Vendor Background Check",
			"Vendor Compliance Audit",
			"Vendor Sampling Evaluation",
			"Vendor Sign Off",
		)

	def on_cancel(self):
		self.db_set("status", "In Progress")

	# DEFERRED IDEA — Lifecycle Timeline graph (built, tried, pulled on
	# 2026-08-26; not scheduled again unless asked):
	# A frappe.Chart line graph shown here on this KYC after submit,
	# plotting Request Created/Accepted -> KYC Created/Submitted ->
	# Background Check/Compliance Audit/Sampling Evaluation
	# Created/Submitted/Cancelled -> Sign Off Created/Signed. First pass
	# was one colour for the whole line (green/orange/red overall). Second
	# pass, after "make it more colourful", gave each stage its own hue
	# plus a red/green override on a Failed Background
	# Check/Compliance Audit or a signed Sign Off, with each point's real
	# date folded into its axis label. User's verdict both times: "not
	# that great" — pulled rather than iterated further. Needed new
	# submitted_on/cancelled_on Datetime fields on Vendor Onboarding
	# Request, Vendor KYC, Vendor Background Check, Vendor Compliance
	# Audit, Vendor Sampling Evaluation, and submitted_on/cancelled_on
	# fields on Vendor Sign Off (which no longer has requested_on/
	# signed_on to reuse — removed later, see git history) — all removed
	# along with the graph itself. If this is picked back up,
	# consider a plainer textual/stepper timeline instead of a chart,
	# given the feedback so far.
