# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vendor_lifecycle.vendor_lifecycle.permissions import get_active_supplier_portal_vendors, is_internal_user
from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	get_kyc_vendor_contact,
	resolve_vendor_lifecycle_company_name,
	send_vendor_lifecycle_email,
	vendor_lifecycle_manager_emails,
)

DEFAULT_SUPPORT_TICKET_CREATED_EMAIL_TEMPLATE = "Vendor Support Ticket Created"
DEFAULT_SUPPORT_TICKET_NEW_TICKET_ALERT_EMAIL_TEMPLATE = "Vendor Support Ticket New Ticket Alert"
DEFAULT_SUPPORT_TICKET_RESOLVED_EMAIL_TEMPLATE = "Vendor Support Ticket Resolved"
DEFAULT_SUPPORT_TICKET_REOPENED_EMAIL_TEMPLATE = "Vendor Support Ticket Reopened"
DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE = "Vendor Support Ticket Escalation"

ATTACHMENT_FIELDS = ("attachment", "attachment_2")


class VendorSupportTicket(Document):
	def before_insert(self):
		if not self.vendor and not is_internal_user():
			vendors = get_active_supplier_portal_vendors(frappe.session.user)
			if len(vendors) == 1:
				self.vendor = vendors[0]
			# len > 1: the portal page's picker is expected to have set
			# self.vendor explicitly already — validate() below still
			# checks it belongs to this user's own active vendors either
			# way, so nothing bad happens if it didn't.
		# opened_on is permlevel 1 (Supplier has read-only there — see the
		# other transitions below, all deliberately kept off .save() for
		# the same reason) — but validate_higher_perm_levels() runs right
		# after before_insert on EVERY insert, including this one, and
		# silently resets any permlevel-1 field back to blank/default for
		# a user without write access at that level. Without this flag, a
		# vendor filing their own ticket from the portal would always get
		# a blank Opened On. status isn't affected the same way only by
		# coincidence — "Open" is already its own JSON default.
		self.flags.ignore_permlevel_for_fields = ["opened_on"]
		self.opened_on = now_datetime()
		self.status = "Open"

	def validate(self):
		if is_internal_user():
			return
		if self.vendor not in get_active_supplier_portal_vendors(frappe.session.user):
			frappe.throw(frappe._("You can only raise a ticket for your own active vendor account."))
		if self.status == "Closed" and self.has_value_changed("description"):
			frappe.throw(frappe._("This ticket is closed — the description can no longer be edited."))

	def after_insert(self):
		self._notify_created()
		self._notify_managers_new_ticket()

	@frappe.whitelist()
	def set_resolution_rating(self, resolution_rating):
		# Persists the moment the vendor picks a star rating on the portal,
		# rather than waiting for the separate Close Ticket click —
		# resolution_rating is permlevel 1 (Supplier is read-only there),
		# so this has to go through db_set() like every other transition
		# here rather than a plain .save(), which would just get silently
		# reverted back to whatever's already in the DB.
		if self.status != "Resolved":
			frappe.throw(frappe._("The ticket must be Resolved before it can be rated."))
		self.db_set("resolution_rating", resolution_rating)

	@frappe.whitelist()
	def start_progress(self):
		# The only way this status is ever set — same reasoning as every
		# other transition here, kept behind a method rather than a free
		# Select so only a genuine "someone's picked this up" action can
		# set it.
		if not is_internal_user():
			frappe.throw(frappe._("Only the internal team can start progress on a ticket."))
		if self.status not in ("Open", "Reopened"):
			frappe.throw(frappe._("Only an Open or Reopened ticket can be moved to In Progress."))
		self.db_set("status", "In Progress")
		self.add_comment("Info", frappe._("Status changed to In Progress"))

	@frappe.whitelist()
	def mark_resolved(self, resolution=None):
		if not is_internal_user():
			frappe.throw(frappe._("Only the internal team can mark a ticket resolved."))
		self.db_set("resolution", resolution or self.resolution)
		self.db_set("status", "Resolved")
		self.db_set("resolved_by", frappe.session.user)
		self.add_comment("Info", frappe._("Marked Resolved by {0}").format(frappe.session.user))
		self._notify_resolved()

	@frappe.whitelist()
	def mark_invalid(self, reason=None):
		# A terminal state alongside Closed, for a ticket that was never a
		# genuine issue — skips the Resolved -> vendor-rates-and-closes
		# flow entirely, since there's nothing for the vendor to rate.
		if not is_internal_user():
			frappe.throw(frappe._("Only the internal team can mark a ticket invalid."))
		self.db_set("resolution", reason or self.resolution)
		self.db_set("status", "Invalid")
		self.db_set("closed_on", now_datetime())
		self.db_set("closed_by", frappe.session.user)
		self.add_comment("Info", frappe._("Marked Invalid by {0}").format(frappe.session.user))

	@frappe.whitelist()
	def close_ticket(self, resolution_rating=None):
		# Deliberately the vendor's own call, not staff's — only the vendor
		# is in a position to say they're done with this, and once closed
		# it's final (see reopen_ticket below). Can be closed from any
		# status, not just Resolved — the portal confirms with the vendor
		# before calling this, since there's no server-side undo.
		if is_internal_user():
			frappe.throw(frappe._("A ticket can only be closed by the vendor, from the Supplier Portal."))
		# The portal only shows/requires the rating selector when the
		# ticket is Resolved — enforced here too (not just client-side),
		# so a direct API call can't close a Resolved ticket with no
		# rating on record. resolution_rating may already be set on the
		# doc from an earlier set_resolution_rating() call, independent
		# of whatever this particular call passed in.
		if self.status == "Resolved" and resolution_rating is None and not self.resolution_rating:
			frappe.throw(frappe._("Rate the resolution before closing this ticket."))
		self.db_set("status", "Closed")
		self.db_set("closed_on", now_datetime())
		self.db_set("closed_by", frappe.session.user)
		if resolution_rating is not None:
			self.db_set("resolution_rating", resolution_rating)
		self.add_comment("Info", frappe._("Closed by the vendor"))

	@frappe.whitelist()
	def set_attachment_field(self, fieldname, file_url):
		"""Called by the portal (www/vst_new, www/vst_detail) right after
		upload_file() — which is itself called with `fieldname` already
		set, so the File it creates is correctly homed to this field from
		the start. A plain frappe.client.save() here instead would create
		a *second* File record for the same upload (Frappe's own
		Attach-field save-time sync always creates a fresh File rather
		than recognizing one that's already correctly attached), leaving
		the first one permanently orphaned — confirmed happening in
		practice for every attachment on both portal pages. db_set()
		bypasses that document-save sync path entirely."""
		if fieldname not in ATTACHMENT_FIELDS:
			frappe.throw(frappe._("Invalid attachment field."))
		self.db_set(fieldname, file_url)

	@frappe.whitelist()
	def reopen_ticket(self):
		# Closed is deliberately terminal — Close is the vendor's own final
		# word that the resolution satisfied them, so it can't be undone.
		# Resolved and Invalid can still be reopened (the vendor disagrees
		# with the resolution, or staff got the Invalid call wrong).
		if self.status not in ("Resolved", "Invalid"):
			frappe.throw(frappe._("Only a Resolved or Invalid ticket can be reopened — a Closed ticket is final."))
		self.db_set("status", "Reopened")
		self.db_set("closed_on", None)
		self.add_comment("Info", frappe._("Reopened by {0}").format(frappe.session.user))
		self._notify_reopened()

	def _notify_created(self):
		try:
			self._notify_created_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Support Ticket: failed to send creation email", message=frappe.get_traceback()
			)

	def _notify_created_unsafe(self):
		contact = get_ticket_vendor_contact(self.vendor)
		if not contact.get("official_email"):
			return
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_SUPPORT_TICKET_CREATED_EMAIL_TEMPLATE,
			context=_ticket_email_context(self, contact),
			recipients=[contact["official_email"]],
		)

	def _notify_managers_new_ticket(self):
		# A dedicated, internally-worded alert — separate from the vendor's
		# own confirmation email above (which also CCs managers, but that
		# copy reads as "Dear vendor..." and isn't a proper heads-up).
		try:
			recipients = vendor_lifecycle_manager_emails()
			if not recipients:
				return
			contact = get_ticket_vendor_contact(self.vendor)
			send_vendor_lifecycle_email(
				doctype=self.doctype,
				name=self.name,
				template_name=DEFAULT_SUPPORT_TICKET_NEW_TICKET_ALERT_EMAIL_TEMPLATE,
				context=_ticket_email_context(self, contact),
				recipients=recipients,
			)
		except Exception:
			frappe.log_error(
				title="Vendor Support Ticket: failed to send new-ticket manager alert",
				message=frappe.get_traceback(),
			)

	def _notify_resolved(self):
		try:
			self._notify_resolved_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Support Ticket: failed to send resolved email", message=frappe.get_traceback()
			)

	def _notify_resolved_unsafe(self):
		contact = get_ticket_vendor_contact(self.vendor)
		if not contact.get("official_email"):
			return
		context = _ticket_email_context(self, contact)
		context["resolution"] = self.resolution
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_SUPPORT_TICKET_RESOLVED_EMAIL_TEMPLATE,
			context=context,
			recipients=[contact["official_email"]],
		)

	def _notify_reopened(self):
		try:
			self._notify_reopened_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Support Ticket: failed to send reopened email", message=frappe.get_traceback()
			)

	def _notify_reopened_unsafe(self):
		# Staff need to know a ticket they thought was done is back —
		# there's no vendor-facing side to this one. Whoever last marked it
		# Resolved and/or Closed personally gets pulled back in alongside
		# the general Manager list, since they're the one whose fix didn't
		# hold.
		recipients = vendor_lifecycle_manager_emails()
		for user in (self.resolved_by, self.closed_by):
			if not user:
				continue
			email = frappe.db.get_value("User", user, "email") or user
			if email not in recipients:
				recipients.append(email)
		if not recipients:
			return
		contact = get_ticket_vendor_contact(self.vendor)
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_SUPPORT_TICKET_REOPENED_EMAIL_TEMPLATE,
			context=_ticket_email_context(self, contact),
			recipients=recipients,
		)


def _ticket_email_context(ticket, contact):
	return {
		"firm_name": contact.get("firm_name"),
		"contact_person_name": contact.get("contact_person_name"),
		"company_name": resolve_vendor_lifecycle_company_name(),
		"ticket_name": ticket.name,
		"subject": ticket.subject,
		"priority": ticket.priority,
		"ticket_link": get_ticket_portal_link(ticket.name),
	}


def get_ticket_portal_link(name):
	# The Desk form link is useless here — the vendor is a portal-only user
	# with no Desk access at all.
	return f"{frappe.utils.get_url()}/vendor-support-tickets/{name}"


def get_ticket_vendor_contact(vendor):
	"""Same KYC-first, Supplier-fallback contact resolution Satisfaction
	Survey uses (see get_survey_vendor_contact) — Support Tickets are open
	to any enabled vendor, not only ones with a Vendor KYC on file."""
	kyc_name = frappe.db.get_value(
		"Vendor KYC", {"supplier": vendor, "docstatus": 1}, "name", order_by="creation desc"
	)
	if kyc_name:
		contact = get_kyc_vendor_contact(kyc_name)
		if contact.get("official_email"):
			return contact

	supplier = frappe.db.get_value("Supplier", vendor, ["supplier_name", "email_id"], as_dict=True) or {}
	return {
		"official_email": supplier.get("email_id"),
		"contact_person_name": None,
		"firm_name": supplier.get("supplier_name"),
	}
