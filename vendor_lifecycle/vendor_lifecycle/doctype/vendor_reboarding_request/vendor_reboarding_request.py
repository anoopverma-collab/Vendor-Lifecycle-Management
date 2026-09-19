# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	VENDOR_LIFECYCLE_STAGE_REBOARDING,
	VENDOR_LIFECYCLE_STATUS_DISABLED,
	resolve_vendor_lifecycle_company_name,
	send_vendor_lifecycle_email,
)

DEFAULT_REBOARDING_REQUEST_RECEIVED_EMAIL_TEMPLATE = "Vendor Reboarding Request Received"
DEFAULT_REBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE = "Vendor Reboarding Request Approved"
DEFAULT_REBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE = "Vendor Reboarding Request Rejected"
DEFAULT_REBOARDING_REQUEST_APPROVED_CREATOR_EMAIL_TEMPLATE = "Vendor Reboarding Request Approved - Creator Copy"
DEFAULT_REBOARDING_REQUEST_REJECTED_CREATOR_EMAIL_TEMPLATE = "Vendor Reboarding Request Rejected - Creator Copy"


class VendorReboardingRequest(Document):
	def before_insert(self):
		self._resolve_original_kyc()
		self._apply_current_supplier_contact()
		self._apply_current_supplier_billing_currency_and_country(supplier_name=self.vendor)
		self._resolve_original_deboarding_request()
		self._block_second_active_request()

	def _resolve_original_kyc(self):
		if not self.vendor:
			return
		# fetch_from only fires on a client-side field-change event, and
		# original_kyc is resolved here server-side (before_insert runs
		# after the client has already submitted the new doc) — so the
		# fetch_from on business_type/supplier_group would never
		# actually fire. Resolve everything explicitly instead.
		if not self.original_kyc:
			self.original_kyc = frappe.db.get_value(
				"Vendor KYC", {"supplier": self.vendor, "docstatus": 1}, "name", order_by="creation desc"
			)
		if self.original_kyc:
			kyc = frappe.db.get_value(
				"Vendor KYC",
				self.original_kyc,
				[
					"business_type",
					"supplier_group",
					"contact_person_name",
					"contact_person_number",
					"official_email",
					"address_line_1",
					"city",
					"state",
					"pincode",
					"creation",
				],
				as_dict=True,
			)
			self.business_type = kyc.business_type
			self.supplier_group = kyc.supplier_group
			self.contact_person_name = kyc.contact_person_name
			self.contact_person_number = kyc.contact_person_number
			self.official_email = kyc.official_email
			self.address_line_1 = kyc.address_line_1
			self.city = kyc.city
			self.state = kyc.state
			self.pincode = kyc.pincode
			# Country and Billing Currency are deliberately NOT sourced from
			# KYC at all (see _apply_current_supplier_billing_currency_and_
			# country below) - they must come only from the Supplier's own
			# current fields, blank if the Supplier's own fields are blank.
			# "Originally onboarded" is the KYC's own creation, not this
			# Request's — the point is how long the vendor relationship
			# has existed, which this Request had nothing to do with.
			self.originally_onboarded_on = frappe.utils.getdate(kyc.creation)

	def _apply_current_supplier_contact(self):
		# KYC's contact fields are a frozen snapshot from onboarding time —
		# possibly years out of date by the time a vendor is being
		# reconsidered. The Supplier's own current contact takes priority
		# here; KYC is only the fallback for whichever piece isn't
		# available.
		#
		# Originally read Supplier.supplier_primary_contact directly —
		# wrong: that field is only ever set by the Supplier form's own
		# quick-add dialog, and stays blank even when a Contact linked to
		# this Supplier has "Is Primary Contact" ticked directly (the more
		# general case — confirmed directly against real data, where a
		# Contact/Address was linked and marked primary but Supplier's own
		# fields were still NULL). frappe.contacts.doctype.contact.contact.
		# get_default_contact() is core Frappe's own resolution for this —
		# it checks every Contact actually linked to the party via Dynamic
		# Link, preferring one with is_primary_contact set, falling back
		# to any linked Contact otherwise.
		if not self.vendor:
			return
		from frappe.contacts.doctype.contact.contact import get_default_contact

		contact_name = get_default_contact("Supplier", self.vendor)
		if contact_name:
			contact = frappe.db.get_value(
				"Contact",
				contact_name,
				["first_name", "last_name", "mobile_no", "phone", "email_id"],
				as_dict=True,
			)
			if contact:
				self.contact_person_name = " ".join(filter(None, [contact.first_name, contact.last_name]))
				if contact.mobile_no or contact.phone:
					self.contact_person_number = contact.mobile_no or contact.phone
				if contact.email_id:
					self.official_email = contact.email_id

		self._apply_current_supplier_address(supplier_name=self.vendor)

	def _apply_current_supplier_address(self, supplier_name):
		# Same reasoning and same fix as _apply_current_supplier_contact
		# above — get_default_address() is core Frappe's own resolution,
		# checking every Address actually linked via Dynamic Link and
		# preferring one with is_primary_address set, rather than relying
		# on Supplier.supplier_primary_address (which a directly-ticked
		# Address never populates).
		from frappe.contacts.doctype.address.address import get_default_address

		address_name = get_default_address("Supplier", supplier_name)
		if not address_name:
			return
		address = frappe.db.get_value(
			"Address", address_name, ["address_line1", "city", "state", "pincode", "country"], as_dict=True
		)
		if not address:
			return
		if address.address_line1:
			self.address_line_1 = address.address_line1
		if address.city:
			self.city = address.city
		if address.state:
			self.state = address.state
		if address.pincode:
			self.pincode = address.pincode

	def _apply_current_supplier_billing_currency_and_country(self, supplier_name):
		# Both Country and Billing Currency live directly on the Supplier
		# master itself (core ERPNext fields - default_currency is
		# labelled "Billing Currency" there) - the ONLY source for these
		# two, deliberately with no KYC fallback: set unconditionally to
		# whatever the Supplier's own fields hold, blank if the Supplier's
		# own fields are blank, never inheriting anything else (not KYC,
		# not the site's Global Defaults - see ignore_user_permissions on
		# these two fields in the doctype JSON for the other half of that).
		if not supplier_name:
			self.country = None
			self.billing_currency = None
			return
		supplier = frappe.db.get_value(
			"Supplier", supplier_name, ["country", "default_currency"], as_dict=True
		) or {}
		self.country = supplier.get("country")
		self.billing_currency = supplier.get("default_currency")

	def _resolve_original_deboarding_request(self):
		if not self.vendor:
			return
		# The most recent submitted Deboarding Request is what actually
		# caused this vendor's current disable — same "most recent
		# submitted" resolution as original_kyc above.
		if not self.original_deboarding_request:
			self.original_deboarding_request = frappe.db.get_value(
				"Vendor Deboarding Request", {"vendor": self.vendor, "docstatus": 1}, "name", order_by="creation desc"
			)
		if self.original_deboarding_request:
			original = frappe.db.get_value(
				"Vendor Deboarding Request", self.original_deboarding_request, ["reason", "is_resolvable"], as_dict=True
			)
			self.deboarding_reason = original.reason
			self.is_resolvable = original.is_resolvable
			self._resolve_deboarded_date()
		self.prior_deboarding_count = frappe.db.count(
			"Vendor Deboarding Checklist",
			filters={"deboarding_request": ["in", frappe.get_all(
				"Vendor Deboarding Request", filters={"vendor": self.vendor, "docstatus": 1}, pluck="name"
			)], "docstatus": 1},
		)

	def _resolve_deboarded_date(self):
		completed_on = frappe.db.get_value(
			"Vendor Deboarding Checklist",
			{"deboarding_request": self.original_deboarding_request, "docstatus": 1},
			"completed_on",
			order_by="creation desc",
		)
		if not completed_on:
			return
		self.deboarded_on = completed_on
		# A snapshot as of when this Request was created, same as every
		# other context field here — not something that stays live-
		# updating on an already-created document.
		self.days_since_deboarded = (frappe.utils.getdate(frappe.utils.nowdate()) - frappe.utils.getdate(completed_on)).days

	def _block_second_active_request(self):
		if not self.vendor:
			return
		# Trashed and Rejected are both excluded on purpose - a discarded
		# or turned-down request shouldn't permanently occupy this
		# vendor's one-active-request slot; only a genuinely still-open
		# one (In Progress/Approval Pending/Approved) should block a new
		# attempt. Checked against workflow_state (Frappe's own canonical
		# field, set directly by apply_workflow()) rather than this app's
		# own status field, which only gets synced onto it as a side
		# effect of validate() running.
		if frappe.db.exists(
			"Vendor Reboarding Request",
			{"vendor": self.vendor, "docstatus": ["in", [0, 1]], "workflow_state": ["not in", ["Trashed", "Rejected"]]},
		):
			frappe.throw(
				frappe._(
					"A Vendor Reboarding Request already exists for this vendor (draft or submitted) —"
					" cancel it before creating another."
				)
			)

	def validate(self):
		self._sync_status_from_workflow_state()
		self._require_vendor_currently_disabled()

	def _sync_status_from_workflow_state(self):
		# Same mechanism as Vendor KYC's own — the Workflow drives Frappe's
		# hidden "workflow_state" field, not this app's own "status" Select;
		# on_submit()/on_cancel()/reject() already keep status correct for
		# the states those cover, but a transition that neither submits nor
		# cancels (In Progress -> Approval Pending, via "Send for Approval",
		# or In Progress -> Trashed, via "Trash") has no other hook watching
		# it, so status would otherwise go stale there. workflow_state is
		# blank on any site with no active workflow for this doctype, so
		# this is a no-op there rather than clobbering status with nothing.
		if self.workflow_state and self.workflow_state != self.status:
			self.status = self.workflow_state

	def _require_vendor_currently_disabled(self):
		if not self.vendor:
			return
		disabled, status = frappe.db.get_value("Supplier", self.vendor, ["disabled", "vendor_lifecycle_status"])
		if not disabled:
			frappe.throw(
				frappe._("{0} is not currently disabled — Re-boarding only applies to a deboarded vendor.").format(
					self.vendor
				)
			)
		if status != VENDOR_LIFECYCLE_STATUS_DISABLED:
			frappe.throw(
				frappe._(
					"{0} is disabled, but not through this app's own Deboarding process (its lifecycle status is"
					" {1}) — Re-boarding only applies to a vendor that was actually deboarded."
				).format(self.vendor, frappe.bold(status or frappe._("not set")))
			)

	def before_submit(self):
		if self.status == "Rejected":
			frappe.throw(frappe._("A rejected Vendor Reboarding Request cannot be submitted."))
		if self.status == "Trashed":
			frappe.throw(frappe._("A trashed Vendor Reboarding Request cannot be submitted."))

	def on_submit(self):
		# db_set, not a plain assignment - by the time on_submit() runs,
		# Frappe has already written this document to the database for
		# this request, so a bare self.status = ... here would only ever
		# change the in-memory value for the rest of this request and
		# never actually persist. Also reached via the Workflow's own
		# "Approve" action (which calls doc.submit() internally).
		self.db_set("status", "Approved")
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "vendor_lifecycle_stage", VENDOR_LIFECYCLE_STAGE_REBOARDING)
		self._notify_reboarding_email(DEFAULT_REBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE)
		self._notify_creator_email(DEFAULT_REBOARDING_REQUEST_APPROVED_CREATOR_EMAIL_TEMPLATE)

	def on_cancel(self):
		# "Cancelled" is its own real status (the Workflow's own terminal
		# state for this), same as Vendor KYC's own on_cancel().
		self.db_set("status", "Cancelled")

	def on_update(self):
		# "Approval Pending" and "Rejected" are both reached via a plain
		# save (the Workflow's "Send for Approval"/"Reject" actions never
		# submit or cancel the document) - on_update() is the one hook
		# that fires either way, so it's the only place to catch these two
		# transitions. "Approved" is handled in on_submit() instead (it's
		# always reached via an actual submit, whether through the
		# Workflow's "Approve" action or directly), so there's no overlap.
		before = self.get_doc_before_save()
		if not before or before.status == self.status:
			return
		if self.status == "Approval Pending":
			self._notify_reboarding_email(DEFAULT_REBOARDING_REQUEST_RECEIVED_EMAIL_TEMPLATE)
		elif self.status == "Rejected":
			self._notify_reboarding_email(DEFAULT_REBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE)
			self._notify_creator_email(DEFAULT_REBOARDING_REQUEST_REJECTED_CREATOR_EMAIL_TEMPLATE)

	def _notify_reboarding_email(self, template_name):
		try:
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=template_name,
				context={
					"request_name": self.name,
					"contact_person_name": self.contact_person_name,
					"company_name": resolve_vendor_lifecycle_company_name(),
					"request_date": self.request_date,
				},
				recipients=[self.official_email] if self.official_email else [],
			)
		except Exception:
			frappe.log_error(
				title=f"Vendor Reboarding Request: failed to send {template_name}", message=frappe.get_traceback()
			)

	def _notify_creator_email(self, template_name):
		# Goes to whoever raised the request, not the vendor — same
		# reasoning, and same owner-email resolution, as Vendor Deboarding
		# Request's own _notify_rejected_unsafe: the creator has no other
		# way to find out a decision was made unless they keep checking
		# the document by hand.
		try:
			creator_email = frappe.db.get_value("User", self.owner, "email") or self.owner
			if not creator_email:
				return
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=template_name,
				context={
					"request_name": self.name,
					"vendor_name": frappe.db.get_value("Supplier", self.vendor, "supplier_name") if self.vendor else None,
					"company_name": resolve_vendor_lifecycle_company_name(),
					"request_date": self.request_date,
				},
				recipients=[creator_email],
			)
		except Exception:
			frappe.log_error(
				title=f"Vendor Reboarding Request: failed to send {template_name} (creator)",
				message=frappe.get_traceback(),
			)

	@frappe.whitelist()
	def get_financial_summary(self):
		"""Lifetime, not just-open-transactions like Vendor Deboarding
		Request's own summary — this is about whether re-boarding this
		vendor is worth the effort at all, not what's currently
		outstanding. Uses base_grand_total (company currency) so amounts
		are comparable regardless of what currency any individual invoice
		was raised in."""
		return _compute_financial_summary(self.vendor)

	@frappe.whitelist()
	def get_reboarding_stage_info(self):
		"""Whether a Retry Sign-off should be offered instead of a first
		attempt — mirrors stage_sequencing.get_available_stages' own
		sign_off_is_retry logic (and Vendor Sign Off's own
		_require_no_active_signoff_unless_failed, which this must agree
		with), but scoped to this Request's own reboarding_request rather
		than kyc. Also reports, for each of the 4 stage doctypes, whether
		it's actually ready to be created yet — re-boarding doesn't
		enforce a FIXED order (see stage_sequencing.nearest_requirement's
		own re-boarding branch), but whichever stage IS configured
		mandatory for re-boarding still has to genuinely pass (or be
		force-overridden) before whatever normally comes after it can
		start, same as onboarding always required — this is what lets the
		client show "Complete X first" before even opening the form,
		instead of only failing once the user tries to save it. All 4,
		including Sign-off, stop being offered at all once reboarding_
		complete is true (see stage_sequencing.block_if_reboarding_
		completed, which enforces this same rule server-side, not just in
		the client's own button visibility)."""
		from vendor_lifecycle.vendor_lifecycle.stage_sequencing import STAGE_SEQUENCE, nearest_requirement

		reboarding_complete = frappe.db.exists(
			"Vendor Sign Off", {"reboarding_request": self.name, "docstatus": 1, "sign_off_failed": 0}
		)
		has_failed_sign_off = frappe.db.exists(
			"Vendor Sign Off", {"reboarding_request": self.name, "docstatus": 1, "sign_off_failed": 1}
		)
		blocked = frappe.db.exists(
			"Vendor Sign Off", {"reboarding_request": self.name, "docstatus": 0}
		) or frappe.db.exists(
			"Vendor Sign Off", {"reboarding_request": self.name, "docstatus": 1, "sign_off_failed": ["!=", 1]}
		)

		names = [name for name, _setting in STAGE_SEQUENCE]
		settings = frappe.get_single("Vendor Lifecycle Settings")
		missing_requirement = {}
		for doctype in names[1:]:  # skip Vendor KYC itself — never a target here
			idx = names.index(doctype)
			requirement = nearest_requirement(
				self.original_kyc, STAGE_SEQUENCE[:idx], settings, reboarding_request=self.name
			)
			missing_requirement[doctype] = None if (not requirement or requirement[1]) else requirement[0]

		return {
			"reboarding_complete": bool(reboarding_complete),
			"sign_off_available": not blocked,
			"sign_off_is_retry": not blocked and bool(has_failed_sign_off),
			"missing_requirement": missing_requirement,
		}

	@frappe.whitelist()
	def get_pipeline_progress(self):
		"""Where this re-boarding run currently stands across Background
		Check, Compliance Audit, Sampling Evaluation, and Sign Off — no KYC
		stage of its own, since re-boarding reuses the vendor's original,
		already-submitted KYC (see this doctype's own Connections links,
		which exclude Vendor KYC for the same reason). Computed fresh from
		whatever records exist right now, scoped by reboarding_request
		instead of kyc — mirrors Vendor Onboarding Request's own identical
		get_pipeline_progress(), sharing the same stage_progress_status /
		STAGE_OUTCOME_CONFIG so the two pipeline-progress widgets can never
		drift apart on what each stage doctype's own outcome means."""
		if self.docstatus != 1:
			return []

		from vendor_lifecycle.vendor_lifecycle.stage_sequencing import (
			REBOARDING_MANDATORY_SETTINGS_FIELD,
			STAGE_OUTCOME_CONFIG,
			is_sampling_mandatory_for_reboarding,
			stage_progress_status,
		)

		settings = frappe.get_single("Vendor Lifecycle Settings")
		progress = []
		for doctype, label, outcome_field, passed_value, force_field in STAGE_OUTCOME_CONFIG:
			state = stage_progress_status(
				doctype, {"reboarding_request": self.name}, outcome_field, passed_value, force_field
			)

			# A stage Settings has marked not-mandatory-for-re-boarding (and
			# that hasn't been started anyway) is flagged "Skipped" rather
			# than "Not Started" — same reasoning as Vendor Onboarding
			# Request's own identical check. Sign Off has no entry in
			# REBOARDING_MANDATORY_SETTINGS_FIELD — it's always mandatory,
			# the final stage, never shown as Skipped.
			if state == "Not Started":
				if doctype == "Vendor Sampling Evaluation":
					mandatory = is_sampling_mandatory_for_reboarding(self.business_type, settings)
				else:
					mandatory_field = REBOARDING_MANDATORY_SETTINGS_FIELD.get(doctype)
					mandatory = bool(settings.get(mandatory_field)) if mandatory_field else True
				if not mandatory:
					state = "Skipped"

			progress.append({"label": label, "state": state})

		return progress

	@frappe.whitelist()
	def reject(self):
		"""Manual fallback for a site with no Workflow configured on this
		doctype (see the JS, which checks frappe.model.has_workflow()
		before even offering the Reject button) - same mechanism as
		Vendor KYC's own reject(): only before submission, a plain save
		(not a submit) so the document stays at docstatus 0 forever.

		A silent no-op whenever a Workflow IS active — same reasoning and
		same fix as Vendor KYC's own reject(): the client-side button is
		already hidden then (the Workflow's own Reject action is the real
		one to use), so nobody reaches this via a click; without this
		guard, the "Rejected" status set below would just get silently
		overwritten back to the stale workflow_state value by
		_sync_status_from_workflow_state() in the same save."""
		from frappe.model.workflow import get_workflow_name

		if get_workflow_name(self.doctype):
			return
		if self.docstatus != 0:
			frappe.throw(frappe._("Only a Vendor Reboarding Request that hasn't been submitted yet can be rejected."))
		if self.status == "Rejected":
			return
		# A reviewer rejecting an incomplete request shouldn't be forced to
		# first supply a valid value for a field they might be clearing out
		# precisely because it turned out to be wrong - same reasoning as
		# Vendor KYC's own reject().
		self.flags.ignore_mandatory = True
		self.status = "Rejected"
		self.save()


@frappe.whitelist()
def get_reboarding_stage_info_for(reboarding_request):
	"""Module-level wrapper around get_reboarding_stage_info() above, for
	the 3 pre-Sign-off doctypes' own "next stage" convenience buttons on
	their OWN forms (Vendor Background Check, Vendor Compliance Audit,
	Vendor Sampling Evaluation) — not Vendor Reboarding Request's own
	form, so they can't call an instance method there via frm.call, and
	can't safely assume this Request happens to already be loaded into
	the browser's own locals either. Takes the name and does that lookup
	itself instead."""
	return frappe.get_doc("Vendor Reboarding Request", reboarding_request).get_reboarding_stage_info()


def _compute_financial_summary(vendor):
	if not vendor:
		return {"invoices_total": 0, "debit_notes_total": 0, "net_total": 0, "currency": frappe.db.get_default("currency")}

	# frappe.get_all()'s fields= no longer accepts a raw SQL function
	# string like "sum(x) as y" (blocked as an injection-safety measure in
	# this Frappe version) — confirmed directly by hitting that exact
	# error. frappe.db.sql with bind parameters is the correct way to do
	# an aggregate sum here.
	invoices_total = frappe.db.sql(
		"select sum(base_grand_total) from `tabPurchase Invoice` where supplier=%s and docstatus=1 and is_return=0",
		vendor,
	)[0][0] or 0

	# A return/debit note against a Supplier is a Purchase Invoice with
	# is_return=1 — confirmed directly against real data that ERPNext
	# stores its amount as already-negative, so debit_notes_signed can be
	# added straight to invoices_total to get the net; debit_notes_total
	# below is only the positive magnitude, for display.
	debit_notes_signed = frappe.db.sql(
		"select sum(base_grand_total) from `tabPurchase Invoice` where supplier=%s and docstatus=1 and is_return=1",
		vendor,
	)[0][0] or 0

	return {
		"invoices_total": invoices_total,
		"debit_notes_total": abs(debit_notes_signed),
		"net_total": invoices_total + debit_notes_signed,
		"currency": frappe.db.get_default("currency"),
	}
