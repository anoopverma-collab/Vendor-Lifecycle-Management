# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	get_kyc_vendor_contact,
	resolve_vendor_lifecycle_company_name,
	send_vendor_lifecycle_email,
	vendor_lifecycle_manager_emails,
)

OPEN_PO_STATUSES = ["Draft", "To Receive and Bill", "To Bill", "To Receive"]

# Same hardcoded pair Vendor KYC uses for its own Freeze/Unfreeze Supplier
# button (see SUPPLIER_FREEZE_TOGGLE_ROLES in vendor_kyc.py).
SUPPLIER_FREEZE_TOGGLE_ROLES = {"System Manager", "Vendor Lifecycle Manager"}

DEFAULT_DEBOARDING_REQUEST_CREATED_EMAIL_TEMPLATE = "Vendor Deboarding Request Created"
DEFAULT_DEBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE = "Vendor Deboarding Request Rejected"
DEFAULT_DEBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE = "Vendor Deboarding Request Approved"


class VendorDeboardingRequest(Document):
	def before_insert(self):
		# Always the actual creation date, never user-set — read-only on the
		# form for exactly this reason.
		self.request_date = frappe.utils.nowdate()

		if not self.ratings:
			for row in get_deboarding_rating_criteria():
				self.append("ratings", {"criteria": row.name})

	def validate(self):
		self._enforce_rejected_is_frozen()
		self._validate_ratings_scored()

	def _enforce_rejected_is_frozen(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and before.status == "Rejected":
			frappe.throw(frappe._("This Vendor Deboarding Request has been rejected and can no longer be edited."))

	def _validate_ratings_scored(self):
		# Every row must be rated before ANY save, not just before submit —
		# reject() explicitly opts out via ignore_mandatory (same flag Vendor
		# KYC's own reject() uses), since a rejected request was never rated
		# and shouldn't need to be.
		if self.flags.ignore_mandatory:
			return
		if any(not row.score for row in self.ratings):
			frappe.throw(
				frappe._("Every row in the Ratings table must be rated before saving."), frappe.MandatoryError
			)

	def before_submit(self):
		if self.status == "Rejected":
			frappe.throw(frappe._("A rejected Vendor Deboarding Request cannot be submitted."))

	def after_insert(self):
		self._notify_created()

	def on_submit(self):
		# Submitting the request itself no longer disables anything — that
		# only happens once the linked Vendor Deboarding Checklist is
		# submitted (see VendorDeboardingChecklist.on_submit).
		self.db_set("status", "Approved")
		self._notify_approved()

	@frappe.whitelist()
	def reject(self):
		"""Manual reject path, same mechanism (and same lack of an explicit
		role check — governed only by the doctype's own write permission)
		as Vendor KYC's own reject(): only before submission, a plain save
		(not a submit) so the document stays at docstatus 0 forever, and
		_enforce_rejected_is_frozen() above is what actually locks it once
		Rejected."""
		if self.docstatus != 0:
			frappe.throw(frappe._("Only a Vendor Deboarding Request that hasn't been submitted yet can be rejected."))
		if self.status == "Rejected":
			return
		self.flags.ignore_mandatory = True
		self.status = "Rejected"
		self.save()
		self._notify_rejected()

	def can_toggle_supplier_freeze(self):
		"""Same hardcoded role check as Vendor KYC's own version — this is
		the real security boundary, not just a UI convenience (also called
		from inside toggle_supplier_freeze() itself below)."""
		return bool(SUPPLIER_FREEZE_TOGGLE_ROLES & set(frappe.get_roles()))

	@frappe.whitelist()
	def get_supplier_freeze_button_info(self):
		if self.docstatus != 1 or not self.vendor or not self.can_toggle_supplier_freeze():
			return {"show": False}
		is_frozen = frappe.db.get_value("Supplier", self.vendor, "is_frozen")
		return {"show": True, "is_frozen": bool(is_frozen)}

	@frappe.whitelist()
	def toggle_supplier_freeze(self):
		"""Only offered once the request is Approved — before that,
		submitting has no automatic effect on the vendor at all, so this
		manual toggle (same mechanism as Vendor KYC's own) shouldn't be
		possible either. Once submitted, this is the only way to act on the
		Supplier before its Checklist is submitted."""
		if self.docstatus != 1:
			frappe.throw(frappe._("This Vendor Deboarding Request must be submitted first."))
		if not self.can_toggle_supplier_freeze():
			frappe.throw(
				frappe._("You don't have permission to freeze or unfreeze this Supplier."), frappe.PermissionError
			)
		is_frozen = frappe.db.get_value("Supplier", self.vendor, "is_frozen")
		new_value = 0 if is_frozen else 1
		frappe.db.set_value("Supplier", self.vendor, "is_frozen", new_value)
		return {"is_frozen": new_value}

	def _notify_created(self):
		try:
			self._notify_created_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Deboarding Request: failed to send creation email", message=frappe.get_traceback()
			)

	def _notify_created_unsafe(self):
		recipients = vendor_lifecycle_manager_emails()
		if not recipients:
			return
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_DEBOARDING_REQUEST_CREATED_EMAIL_TEMPLATE,
			context=self._email_context(),
			recipients=recipients,
		)

	def _notify_rejected(self):
		try:
			self._notify_rejected_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Deboarding Request: failed to send rejected email", message=frappe.get_traceback()
			)

	def _notify_rejected_unsafe(self):
		# Goes to whoever raised the request, not the Vendor Lifecycle
		# Manager (who already knows — they're the one who rejected it, or
		# was CC'd on the original creation email).
		creator_email = frappe.db.get_value("User", self.owner, "email") or self.owner
		if not creator_email:
			return
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_DEBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE,
			context=self._email_context(),
			recipients=[creator_email],
		)

	def _notify_approved(self):
		try:
			self._notify_approved_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Deboarding Request: failed to send approved email", message=frappe.get_traceback()
			)

	def _notify_approved_unsafe(self):
		# The only vendor-facing email in this whole doctype — submitting
		# the request is the actual "you're being deboarded" decision, so
		# the vendor is told now, not later when the Checklist disables
		# them without warning.
		contact = self._vendor_contact()
		recipients = self._notification_recipients(contact)
		if not recipients:
			return
		context = self._email_context()
		context.update(contact)
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_DEBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE,
			context=context,
			recipients=recipients,
		)

	def _vendor_contact(self):
		kyc = frappe.db.get_value("Supplier", self.vendor, "vendor_kyc")
		return get_kyc_vendor_contact(kyc) if kyc else {}

	def _notification_recipients(self, contact):
		# Two possible sources: the KYC's own Official Email, and the
		# Supplier's Primary Contact (email_id, kept in sync by ERPNext
		# core) — a Supplier with no linked KYC (or a KYC missing Official
		# Email) would otherwise silently never be notified at all, with
		# no error and no log. Same 3rd-source pattern as Vendor Deboarding
		# Checklist's own _clearance_recipients(), minus Additional Email
		# since this doctype has no such field. Deduplicated
		# case-insensitively in case both sources hold the same address.
		candidates = [
			contact.get("official_email"),
			frappe.db.get_value("Supplier", self.vendor, "email_id") if self.vendor else None,
		]

		seen = set()
		recipients = []
		for email in candidates:
			email = (email or "").strip()
			if not email:
				continue
			key = email.lower()
			if key in seen:
				continue
			seen.add(key)
			recipients.append(email)

		return recipients

	def _email_context(self):
		firm_name = frappe.db.get_value("Supplier", self.vendor, "supplier_name") or self.vendor
		return {
			"company_name": resolve_vendor_lifecycle_company_name(),
			"request_name": self.name,
			"firm_name": firm_name,
			"vendor": self.vendor,
			"reason": self.reason,
			"request_date": frappe.utils.formatdate(self.request_date),
			"is_resolvable": self.is_resolvable,
			"request_link": frappe.utils.get_url_to_form(self.doctype, self.name),
		}

	@frappe.whitelist()
	def get_open_transactions(self):
		return get_open_transaction_counts(self.vendor)


@frappe.whitelist()
def get_default_ratings():
	"""Called from the client the moment a new Vendor Deboarding Request
	form is opened, so the Ratings table appears — and can be scored —
	immediately, rather than only after the first save. before_insert()
	still does the same population server-side, as a fallback for any
	document created outside the UI (API, data import, etc.)."""
	return [{"criteria": row.name} for row in get_deboarding_rating_criteria()]


def get_deboarding_rating_criteria():
	"""Ordered [{"name": ...}] list to render as Ratings rows on a new
	Deboarding Request, resolved from the site-wide Default Deboarding
	Rating Template in Vendor Lifecycle Settings. Empty (not an error) if
	no template is set, or the resolved template has no (enabled) rows —
	unlike Satisfaction Survey, ratings here are supplementary context, not
	the whole point of the document."""
	template_name = frappe.db.get_single_value("Vendor Lifecycle Settings", "default_deboarding_rating_template")
	if not template_name:
		return []

	template = frappe.get_cached_doc("Deboarding Rating Template", template_name)
	ordered_criteria_names = [row.criteria for row in template.criteria]
	if not ordered_criteria_names:
		return []

	rows = frappe.get_all(
		"Deboarding Rating Criteria",
		filters={"name": ["in", ordered_criteria_names], "disabled": 0},
		fields=["name"],
	)
	position = {name: idx for idx, name in enumerate(ordered_criteria_names)}
	rows.sort(key=lambda row: position.get(row.name, len(position)))
	return rows


def get_open_transaction_counts(vendor):
	"""Numbers only, for the Request's own quick-glance summary — the full
	itemized list lives on the Vendor Deboarding Checklist instead (see
	get_open_transaction_details below)."""
	return {
		"open_po_count": frappe.db.count(
			"Purchase Order", filters={"supplier": vendor, "docstatus": 1, "status": ["in", OPEN_PO_STATUSES]}
		),
		"unpaid_pi_count": frappe.db.count(
			"Purchase Invoice", filters={"supplier": vendor, "docstatus": 1, "outstanding_amount": [">", 0]}
		),
		"advance_count": frappe.db.count(
			"Payment Entry",
			filters={
				"party_type": "Supplier", "party": vendor, "docstatus": 1, "unallocated_amount": [">", 0],
			},
		),
	}


def get_open_transaction_details(vendor):
	"""The itemized version of get_open_transaction_counts — used by the
	Checklist's own colorful full transaction list."""
	return {
		"open_purchase_orders": frappe.get_all(
			"Purchase Order",
			filters={"supplier": vendor, "docstatus": 1, "status": ["in", OPEN_PO_STATUSES]},
			fields=["name", "status", "grand_total"],
		),
		"unpaid_purchase_invoices": frappe.get_all(
			"Purchase Invoice",
			filters={"supplier": vendor, "docstatus": 1, "outstanding_amount": [">", 0]},
			fields=["name", "outstanding_amount"],
		),
		"advance_payments": frappe.get_all(
			"Payment Entry",
			filters={
				"party_type": "Supplier", "party": vendor, "docstatus": 1, "unallocated_amount": [">", 0],
			},
			fields=["name", "posting_date", "unallocated_amount"],
		),
	}
