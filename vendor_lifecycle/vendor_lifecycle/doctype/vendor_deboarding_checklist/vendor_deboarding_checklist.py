# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, format_datetime, now_datetime, today

from vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_request.vendor_deboarding_request import (
	block_if_stopped,
	get_open_transaction_details,
)
from vendor_lifecycle.vendor_lifecycle.permissions import require_internal_user
from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	VENDOR_LIFECYCLE_STAGE_DEBOARDED,
	VENDOR_LIFECYCLE_STAGE_DEBOARDING,
	VENDOR_LIFECYCLE_STAGE_DEBOARDING_FAILED,
	VENDOR_LIFECYCLE_STAGE_REBOARDING,
	VENDOR_LIFECYCLE_STAGE_REBOARDING_FAILED,
	get_disable_reason_for_supplier,
	get_kyc_vendor_contact,
	get_supplier_contact_email,
	require_vendor_lifecycle_email_account,
	send_vendor_lifecycle_email,
	vendor_lifecycle_cc_list,
	vendor_lifecycle_doctype_email_enabled,
	vendor_lifecycle_emails_enabled,
)
from vendor_lifecycle.vendor_lifecycle.db_errors import reraise_if_db_lock_error

# Only these two roles (or one of the two assigned users on a given row)
# may change that row's own status. Also the only roles allowed to use the
# Temporarily Enable Supplier button.
PRIVILEGED_CHECKLIST_ROLES = {"System Manager", "Vendor Lifecycle Manager"}

OPEN_ITEM_STATUSES = ("Not Started", "In Progress")
BLOCKING_ITEM_STATUSES = ("Not Started", "In Progress")
REMARK_REQUIRED_STATUSES = ("Invalid", "Unable to Complete")

TEMPORARILY_ENABLED_STATUS = "Temporarily Enabled"

# Supplier stages in which the same toggle is also offered on the Supplier
# form itself.
SUPPLIER_TEMPORARY_ENABLE_STAGES = (
	VENDOR_LIFECYCLE_STAGE_DEBOARDED,
	VENDOR_LIFECYCLE_STAGE_REBOARDING,
	VENDOR_LIFECYCLE_STAGE_REBOARDING_FAILED,
)


def get_temporary_enable_days():
	# How long a manual "Temporarily Enable Supplier" override lasts before
	# the nightly job (tasks.auto_disable_expired_temporary_enables, 2 AM)
	# auto-disables the Supplier again. Mandatory and at least 1 in Settings.
	return cint(frappe.db.get_single_value("Vendor Lifecycle Settings", "temporary_enable_days")) or 7

# Same cadence for both: a staff member who hasn't finished their assigned
# task(s), and a vendor who hasn't sent back the signed clearance
# certificate, both get nudged again after this many days.
ASSIGNMENT_REMINDER_DAYS = 7
CLEARANCE_FOLLOWUP_DAYS = 7

DEFAULT_CHECKLIST_TASK_ASSIGNED_EMAIL_TEMPLATE = "Vendor Deboarding Checklist Task Assigned"
DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE = "Vendor Deboarding Checklist Task Reminder"
DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE = "Vendor Deboarding Clearance Certificate Request"
DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE = "Vendor Deboarding Clearance Certificate Received"
DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE = "Vendor Deboarding Clearance Certificate Follow-up"


class VendorDeboardingChecklist(Document):
	def before_insert(self):
		if not self.checklist_template or not self.checklist_items:
			default = get_default_checklist()
			if not self.checklist_template:
				self.checklist_template = default["checklist_template"]
			if not self.checklist_items:
				for row in default["items"]:
					self.append("checklist_items", row)

	def validate(self):
		block_if_stopped(self)
		self._validate_only_one_checklist_per_request()
		self._validate_item_status_changes_authorized()
		if not self.mark_as_failed:
			# Both are about individual items being worked through
			# properly — irrelevant once the whole Checklist is being
			# marked Failed instead of completed normally.
			self._validate_remark_required()
			self._validate_at_least_one_assignee()
		self._validate_mark_as_failed_authorized_and_reasoned()

	def _validate_mark_as_failed_authorized_and_reasoned(self):
		if not self.mark_as_failed:
			return
		if not PRIVILEGED_CHECKLIST_ROLES & set(frappe.get_roles()):
			frappe.throw(
				frappe._("Only a Vendor Lifecycle Manager or System Manager can mark a deboarding as Failed."),
				frappe.PermissionError,
			)
		# mandatory_depends_on on the field only ever enforces client-side —
		# same reasoning as _validate_remark_required() above.
		if not self.failure_reason:
			frappe.throw(frappe._("Give a reason before marking this deboarding as Failed."), frappe.MandatoryError)

	def _validate_at_least_one_assignee(self):
		for row in self.checklist_items:
			if not row.assigned_to_1 and not row.assigned_to_2:
				frappe.throw(
					frappe._("Row #{0}: at least one of the two Assigned To fields must be filled in.").format(
						row.idx
					),
					frappe.MandatoryError,
				)

	def _validate_remark_required(self):
		# mandatory_depends_on on the child field only ever enforces
		# client-side (Frappe's own _get_missing_mandatory_fields() only
		# looks at the static reqd flag, never mandatory_depends_on) — a
		# real server-side check is needed for API/script-created rows too.
		for row in self.checklist_items:
			if row.status in REMARK_REQUIRED_STATUSES and not row.remark:
				frappe.throw(
					frappe._("Row #{0}: a Remark is required when Status is Invalid or Unable to Complete.").format(
						row.idx
					),
					frappe.MandatoryError,
				)

	def _validate_only_one_checklist_per_request(self):
		filters = {"deboarding_request": self.deboarding_request, "docstatus": ["!=", 2]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.exists("Vendor Deboarding Checklist", filters)
		if existing:
			frappe.throw(
				frappe._("Checklist {0} already exists for this Deboarding Request.").format(
					frappe.get_desk_link("Vendor Deboarding Checklist", existing)
				)
			)

	def _validate_item_status_changes_authorized(self):
		if PRIVILEGED_CHECKLIST_ROLES & set(frappe.get_roles()):
			return

		before = self.get_doc_before_save()
		before_by_name = {row.name: row for row in (before.checklist_items if before else [])}
		user = frappe.session.user

		for row in self.checklist_items:
			prior = before_by_name.get(row.name)
			prior_status = prior.status if prior else "Not Started"
			if row.status == prior_status:
				continue
			if user not in (row.assigned_to_1, row.assigned_to_2):
				frappe.throw(
					frappe._(
						"Row #{0}: only the assigned user(s), a Vendor Lifecycle Manager, or a System Manager"
						" can change a checklist item's status."
					).format(row.idx),
					frappe.PermissionError,
				)

	def before_submit(self):
		if self.mark_as_failed:
			# The whole point of this checkbox — skip the normal
			# completion/clearance gates, since a genuinely failed
			# deboarding may never be able to satisfy them.
			return
		self._validate_all_items_progressed()
		self._validate_clearance()

	def _validate_all_items_progressed(self):
		blocking = [row for row in self.checklist_items if row.status in BLOCKING_ITEM_STATUSES]
		if blocking:
			frappe.throw(
				frappe._(
					"Every checklist item must be Completed, Invalid, or Unable to Complete before submitting —"
					" {0} item(s) are still Not Started or In Progress."
				).format(len(blocking))
			)

	def _validate_clearance(self):
		if self.no_clearance_certificate:
			if not self.clearance_exception_reason:
				frappe.throw(frappe._("Give a reason the clearance certificate is missing before submitting."))
			return
		if not self.clearance_attachment:
			frappe.throw(
				frappe._(
					"Attach the clearance certificate, or tick 'No Clearance Certificate Available' and give a"
					" reason, before submitting."
				)
			)
		if not self.signed_clearance_certificate:
			frappe.throw(frappe._("Attach the signed clearance certificate before submitting."))

	def on_update(self):
		self._notify_new_assignments()
		self._notify_if_clearance_certificate_received()

	def _notify_new_assignments(self):
		try:
			self._notify_new_assignments_unsafe()
		except Exception:
			reraise_if_db_lock_error()
			frappe.log_error(
				title="Vendor Deboarding Checklist: failed to send assignment email", message=frappe.get_traceback()
			)

	def _notify_new_assignments_unsafe(self):
		# Only fires when an assignment actually changed — not on every
		# save (e.g. editing an unrelated field, or a status change with
		# no assignee change) — compared row-by-row against the doc as it
		# was before this save.
		before = self.get_doc_before_save()
		before_by_name = {row.name: row for row in (before.checklist_items if before else [])}

		newly_assigned_users = set()
		for row in self.checklist_items:
			prior = before_by_name.get(row.name)
			prior_assignees = {prior.assigned_to_1, prior.assigned_to_2} if prior else set()
			prior_assignees.discard(None)
			current_assignees = {row.assigned_to_1, row.assigned_to_2}
			current_assignees.discard(None)
			newly_assigned_users |= current_assignees - prior_assignees

		for user_email in newly_assigned_users:
			self._send_task_email(user_email, DEFAULT_CHECKLIST_TASK_ASSIGNED_EMAIL_TEMPLATE)

	def _open_rows_for_user(self, user_email):
		return [
			row
			for row in self.checklist_items
			if row.status in OPEN_ITEM_STATUSES and user_email in (row.assigned_to_1, row.assigned_to_2)
		]

	def _send_task_email(self, user_email, template_name):
		"""Shared by the immediate "newly assigned" notification and the
		7-day reminder job — always lists every currently-open task
		assigned to this person on this checklist (not just whatever
		changed), and resets the reminder clock for all of them, so an
		older row doesn't immediately trigger its own separate reminder
		the next day."""
		rows = self._open_rows_for_user(user_email)
		if not rows:
			return False

		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			return False
		# Checked explicitly (not left to send_vendor_lifecycle_email's own
		# internal check) so a False return here actually means "nothing
		# was sent" — without this, the switch being off would still
		# return True below, resetting last_notified_on as if this row's
		# reminder clock had genuinely been refreshed.
		if not vendor_lifecycle_doctype_email_enabled(self.doctype, template_name, settings):
			return False
		if not frappe.db.exists("Email Template", template_name):
			return False

		recipient = frappe.db.get_value("User", user_email, "email") or user_email
		if not recipient:
			return False

		template = frappe.get_doc("Email Template", template_name)
		context = {
			"checklist_name": self.name,
			"firm_name": frappe.db.get_value("Supplier", self.supplier, "supplier_name") or self.supplier,
			"checklist_link": frappe.utils.get_url_to_form(self.doctype, self.name),
			"tasks": [{"task": row.task, "category": row.category, "status": row.status} for row in rows],
			"task_count": len(rows),
		}
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=template_name,
			context=context,
			recipients=[recipient],
		)
		now = now_datetime()
		for row in rows:
			frappe.db.set_value("Vendor Deboarding Checklist Item", row.name, "last_notified_on", now)
		return True

	def _notify_if_clearance_certificate_received(self):
		# Covers a staff member manually attaching the signed file on the
		# Desk form. The other path it can arrive by — the vendor replying
		# by email — bypasses this (checklist_clearance_reply.py sets the
		# field via db_set, which skips doc_events) and calls
		# notify_clearance_certificate_received() directly instead.
		if not self.has_value_changed("signed_clearance_certificate") or not self.signed_clearance_certificate:
			return
		self.notify_clearance_certificate_received()

	def notify_clearance_certificate_received(self):
		try:
			self._notify_clearance_certificate_received_unsafe()
		except Exception:
			reraise_if_db_lock_error()
			frappe.log_error(
				title="Vendor Deboarding Checklist: failed to notify signed certificate received",
				message=frappe.get_traceback(),
			)

	def _notify_clearance_certificate_received_unsafe(self):
		# Same shape as Vendor Sign Off's own notify_document_received() —
		# the Checklist's Creator directly, Vendor Lifecycle Manager only
		# via CC (vendor_lifecycle_cc_list already includes them).
		creator_email = frappe.db.get_value("User", self.owner, "email") or self.owner
		if not creator_email:
			return
		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			return
		# Bypasses send_vendor_lifecycle_email() (builds its own
		# Communication directly) — see the same note on
		# send_clearance_certificate_email() above for why this needs its
		# own explicit check.
		if not vendor_lifecycle_doctype_email_enabled(
			self.doctype, DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE, settings
		):
			return
		if not frappe.db.exists("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE):
			return
		cc = vendor_lifecycle_cc_list(settings)

		template = frappe.get_doc("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE)
		context = self._build_clearance_email_context()
		subject = template.get_formatted_subject(context)
		message = template.get_formatted_response(context)

		from frappe.core.doctype.communication.email import make as make_communication

		make_communication(
			doctype=self.doctype,
			name=self.name,
			subject=subject,
			content=message,
			recipients=creator_email,
			cc=", ".join(cc) if cc else None,
			send_email=True,
		)

	def _resolve_company(self):
		return self.company or frappe.defaults.get_global_default("company")

	def vendor_contact(self):
		kyc = frappe.db.get_value("Supplier", self.supplier, "vendor_kyc")
		return get_kyc_vendor_contact(kyc) if kyc else {}

	def _clearance_recipients(self):
		# Four possible sources, in order: the KYC's own Official Email,
		# the Supplier's own email_id (only filled when a Primary Contact is
		# picked on the Supplier form), the email of the Contact actually
		# linked to the Supplier as primary (covers older suppliers whose
		# email_id was never filled), and whatever was typed into
		# Additional Email. Any of them can be blank, and two or more can
		# legitimately hold the same address — compare case-insensitively
		# so a repeated address is only ever added once.
		contact = self.vendor_contact()
		candidates = [
			contact.get("official_email"),
			frappe.db.get_value("Supplier", self.supplier, "email_id") if self.supplier else None,
			get_supplier_contact_email(self.supplier),
			self.additional_email,
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

	def _build_clearance_email_context(self):
		company = self._resolve_company()
		contact = self.vendor_contact()
		company_details = (
			frappe.db.get_value("Company", company, ["gstin", "pan"], as_dict=True) if company else {}
		) or {}
		return {
			"checklist_name": self.name,
			"firm_name": contact.get("firm_name") or self.supplier,
			"contact_person_name": contact.get("contact_person_name"),
			"company_name": company,
			"company_gstin": company_details.get("gstin"),
			"company_pan": company_details.get("pan"),
			"checklist_link": frappe.utils.get_url_to_form(self.doctype, self.name),
		}

	@frappe.whitelist()
	def send_clearance_certificate_email(self):
		"""Emails the vendor the (unsigned) clearance certificate, same
		mechanism as Vendor Sign Off's own send_signoff_email() — via
		Communication.email.make() so it threads into this document's
		timeline, and a vendor reply gets picked up automatically by
		checklist_clearance_reply.py (Frappe's own inbound-mail threading
		via In-Reply-To)."""
		if self.docstatus != 0:
			frappe.throw(frappe._("The clearance certificate email can only be sent while this Checklist is a draft."))
		if not self.clearance_attachment:
			frappe.throw(frappe._("Attach the Clearance Certificate before sending the email."))
		recipients = self._clearance_recipients()
		if not recipients:
			frappe.throw(
				frappe._(
					"No Supplier contact email is available — set one on the linked Vendor KYC, or fill in"
					" Additional Email."
				)
			)
		if not self._resolve_company():
			frappe.throw(
				frappe._(
					"Set a Company on this Checklist, or a Default Company in Global Defaults, before sending"
					" the email."
				)
			)

		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			frappe.throw(frappe._('"Use Emails" is off in Vendor Lifecycle Settings — enable it first.'))
		# This method bypasses send_vendor_lifecycle_email() entirely (it
		# needs to attach the certificate file, which that shared sender
		# doesn't support) — so the per-doctype switch has to be checked
		# explicitly here too, or "Vendor Deboarding Checklist" being off
		# would have no effect on this particular email at all.
		if not vendor_lifecycle_doctype_email_enabled(
			self.doctype, DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE, settings
		):
			frappe.throw(frappe._("Vendor Deboarding Checklist emails are off in Vendor Lifecycle Settings — enable it first."))
		if not frappe.db.exists("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE):
			frappe.throw(
				frappe._("The {0} Email Template is missing — recreate it before sending.").format(
					frappe.bold(DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE)
				)
			)
		email_account = require_vendor_lifecycle_email_account(settings)
		template = frappe.get_doc("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE)
		cc = vendor_lifecycle_cc_list(settings)

		context = self._build_clearance_email_context()
		subject = template.get_formatted_subject(context)
		message = template.get_formatted_response(context)

		file_name = frappe.db.get_value(
			"File",
			{"attached_to_doctype": self.doctype, "attached_to_name": self.name, "attached_to_field": "clearance_attachment"},
			"name",
		)
		attachments = [file_name] if file_name else []

		from frappe.core.doctype.communication.email import make as make_communication

		result = make_communication(
			doctype=self.doctype,
			name=self.name,
			content=message,
			subject=subject,
			sender=email_account.email_id,
			recipients=", ".join(recipients),
			cc=", ".join(cc) if cc else None,
			attachments=attachments,
			send_email=False,
		)
		comm = frappe.get_doc("Communication", result["name"])
		comm.db_set("email_account", email_account.name, update_modified=False)
		comm.send_email()

		self.db_set("last_reminder_sent", today())
		return {"sent_to": recipients, "cc": cc}

	def _send_clearance_followup_email(self):
		"""Core body for the clearance-certificate follow-up — never
		raises on a missing setting/template/recipient, just returns False,
		so the scheduled job (tasks.send_deboarding_checklist_followups)
		can silently skip while the manual button below still surfaces a
		clear error to whoever clicked it."""
		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			return False
		# Checked explicitly here (not left to send_vendor_lifecycle_email's
		# own internal check) so a False return actually means what the
		# caller — send_clearance_certificate_followup()'s manual button —
		# needs it to mean: "nothing was sent". Without this, the switch
		# below being off would still return True from here (the call was
		# made, just silently no-op'd inside), reporting false success.
		if not vendor_lifecycle_doctype_email_enabled(
			self.doctype, DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE, settings
		):
			return False
		if not frappe.db.exists("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE):
			return False
		recipients = self._clearance_recipients()
		if not recipients:
			return False

		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE,
			context=self._build_clearance_email_context(),
			recipients=recipients,
		)
		self.db_set("last_reminder_sent", today())
		return True

	@frappe.whitelist()
	def send_clearance_certificate_followup(self):
		if self.docstatus != 0:
			frappe.throw(frappe._("The follow-up can only be sent while this Checklist is a draft."))
		if self.signed_clearance_certificate:
			frappe.throw(frappe._("The signed clearance certificate has already been received."))
		if not self.last_reminder_sent:
			frappe.throw(frappe._("Send the clearance certificate email first."))
		if not self._send_clearance_followup_email():
			frappe.throw(
				frappe._(
					"Could not send — check Vendor Lifecycle Settings (emails enabled, the Email Template, and a"
					" valid recipient)."
				)
			)
		return {"sent": True}

	def on_submit(self):
		self.completed_on = today()
		self.db_set("completed_on", self.completed_on)

		if self.mark_as_failed:
			# Deliberately does NOT disable the Supplier — deboarding
			# didn't actually complete, so the vendor is left exactly as
			# it was. Retrying is a fresh Deboarding Request + Checklist,
			# not a resubmission of this one.
			frappe.db.set_value("Supplier", self.supplier, {
				"vendor_lifecycle_stage": VENDOR_LIFECYCLE_STAGE_DEBOARDING_FAILED,
				"vendor_lifecycle_status": "Deboarding Failed",
			})
			frappe.db.set_value("Vendor Deboarding Request", self.deboarding_request, "status", "Deboarding Failed")
			return

		was_already_disabled = bool(frappe.db.get_value("Supplier", self.supplier, "disabled"))
		frappe.db.set_value("Supplier", self.supplier, {
			"disabled": 1,
			"vendor_lifecycle_status": "Disabled",
			"vendor_lifecycle_stage": VENDOR_LIFECYCLE_STAGE_DEBOARDED,
		})
		if was_already_disabled:
			message = frappe._("Supplier {0} was already disabled before this Checklist was submitted.").format(
				self.supplier
			)
			frappe.msgprint(message)
			self.add_comment("Comment", text=message)

		frappe.db.set_value("Vendor Deboarding Request", self.deboarding_request, "status", "Vendor Disabled")

	def before_cancel(self):
		self._block_cancel_while_newer_checklist_exists()
		self._block_cancel_once_vendor_moved_on()

	def _block_cancel_while_newer_checklist_exists(self):
		# Deboarding rounds unwind newest first — an older round's checklist
		# can't be cancelled while a later round's one is still submitted.
		newer = frappe.db.get_value(
			"Vendor Deboarding Checklist",
			{"supplier": self.supplier, "docstatus": 1, "creation": [">", self.creation]},
			"name",
			order_by="creation desc",
		)
		if newer:
			frappe.throw(
				frappe._("Cancel the newer Vendor Deboarding Checklist {0} first.").format(
					frappe.utils.get_link_to_form(self.doctype, newer)
				)
			)

	def _block_cancel_once_vendor_moved_on(self):
		# Cancelling puts the vendor back into "Deboarding in Process" —
		# only sensible while this deboarding is still where the vendor is.
		stage = frappe.db.get_value("Supplier", self.supplier, "vendor_lifecycle_stage")
		if self.supplier and stage not in (VENDOR_LIFECYCLE_STAGE_DEBOARDED, VENDOR_LIFECYCLE_STAGE_DEBOARDING_FAILED):
			frappe.throw(
				frappe._("{0} has moved on to {1} — this Checklist can no longer be cancelled.").format(
					self.supplier, frappe.bold(stage)
				)
			)

	def on_cancel(self):
		block_if_stopped(self)
		# Undoes on_submit()'s side effects — every other submittable
		# doctype in this app reverts what it caused on cancel (see the
		# four onboarding stage doctypes' own _revert_disable_if_this_
		# was_the_failed_one), but this one had no on_cancel at all, so
		# cancelling a submitted Checklist left the Supplier disabled
		# forever with no way back except the unrelated Temporarily
		# Enable button.
		self._revert_disable_if_this_was_the_one()
		if self.is_temporarily_enabled:
			self.db_set("is_temporarily_enabled", 0)
			self.db_set("temporarily_enabled_on", None)

	def _revert_disable_if_this_was_the_one(self):
		if not self.supplier:
			return
		# Only one Checklist is ever active per Deboarding Request, but check
		# for another still-submitted one of the SAME request anyway — a
		# vendor deboarded, re-boarded and deboarded again also has the
		# first round's Checklist still submitted, which mustn't count.
		other_submitted_exists = frappe.db.exists(
			"Vendor Deboarding Checklist",
			{"deboarding_request": self.deboarding_request, "docstatus": 1, "name": ["!=", self.name]},
		)
		if other_submitted_exists:
			return
		# Back to "deboarding under way": the Deboarding Request goes back to
		# Approved below, so a new Checklist can be made for it.
		update = {
			"vendor_lifecycle_stage": VENDOR_LIFECYCLE_STAGE_DEBOARDING,
			"vendor_lifecycle_status": "Deboarding Initiated",
		}
		# A failed Checklist never disabled the Supplier; a passed one did —
		# lift that unless something else is also disabling it.
		if not self.mark_as_failed and not get_disable_reason_for_supplier(self.supplier):
			update["disabled"] = 0
		frappe.db.set_value("Supplier", self.supplier, update)
		if self.deboarding_request:
			frappe.db.set_value("Vendor Deboarding Request", self.deboarding_request, "status", "Approved")

	@frappe.whitelist()
	def load_checklist_from_template(self):
		if not self.checklist_template:
			frappe.throw(frappe._("Set a Checklist Template first."))

		template = frappe.get_doc("Vendor Deboarding Checklist Template", self.checklist_template)
		self.checklist_items = []
		for row in template.items:
			self.append("checklist_items", {
				"task": row.task,
				"category": row.category,
				"status": "Not Started",
			})

	@frappe.whitelist()
	def get_open_transactions(self):
		vendor = frappe.db.get_value("Vendor Deboarding Request", self.deboarding_request, "vendor")
		if not vendor:
			return {}
		return get_open_transaction_details(vendor)

	def _can_temporarily_enable(self):
		return bool(PRIVILEGED_CHECKLIST_ROLES & set(frappe.get_roles()))

	@frappe.whitelist()
	def get_temporary_enable_button_info(self):
		if (
			self.docstatus != 1
			or self.mark_as_failed
			or not self._can_temporarily_enable()
			or get_supplier_temporary_enable_checklist_name(self.supplier) != self.name
		):
			return {"show": False}
		days = get_temporary_enable_days()
		info = {
			"show": True,
			"checklist": self.name,
			"is_temporarily_enabled": bool(self.is_temporarily_enabled),
			"days": days,
		}
		if self.is_temporarily_enabled and self.temporarily_enabled_on:
			info["expires_on"] = add_to_date(self.temporarily_enabled_on, days=days)
		return info

	@frappe.whitelist()
	def toggle_temporary_enable(self):
		return self._toggle_temporary_enable(source=self.doctype)

	def _toggle_temporary_enable(self, source):
		"""A deboarding Checklist has no automatic effect on the vendor
		beyond disabling it on submit — this is the one exception: a
		manual, time-boxed re-enable, for when the vendor genuinely needs
		to transact again for a few days before deboarding actually
		completes. A single toggle, same mechanism as Vendor Deboarding
		Request's own Freeze/Unfreeze Supplier button:

		- Not currently temporarily enabled -> enable it. Auto-reverted by
		  tasks.auto_disable_expired_temporary_enables() once
		  Settings' Temporary Enable Days have passed, if nobody's disabled it
		  before then.
		- Currently temporarily enabled -> disable it early instead of
		  waiting out the rest of the window. This clears
		  is_temporarily_enabled (and its timestamp) the same way letting
		  the window expire naturally does, so that nightly sweep finds
		  nothing left to do here — no redundant "auto-disable an already-
		  disabled supplier" action, and no stale "temporarily enabled
		  until X" state left showing on this Checklist.

		Either direction can be repeated afterward for another window.
		Same toggle is offered on the Supplier form (see
		toggle_supplier_temporary_enable); `source` names which form was
		used, for the timeline comment."""
		if self.docstatus != 1:
			frappe.throw(frappe._("This Checklist must be submitted first."))
		if self.mark_as_failed:
			frappe.throw(frappe._("This deboarding was marked Failed — the Supplier was never disabled by it."))
		if not self._can_temporarily_enable():
			frappe.throw(
				frappe._("Only a Vendor Lifecycle Manager or System Manager can do this."), frappe.PermissionError
			)
		if get_supplier_temporary_enable_checklist_name(self.supplier) != self.name:
			frappe.throw(
				frappe._(
					"Only the vendor's latest Checklist can do this, while it is Deboarded or in re-boarding."
				)
			)

		if self.is_temporarily_enabled:
			frappe.db.set_value("Supplier", self.supplier, temporary_enable_supplier_update(self.supplier, enable=False))
			self.db_set("is_temporarily_enabled", 0)
			self.db_set("temporarily_enabled_on", None)
			self.add_comment(
				"Comment", text=frappe._("Supplier disabled again (from {0}).").format(frappe._(source))
			)
			return {"is_temporarily_enabled": False}

		now = now_datetime()
		frappe.db.set_value("Supplier", self.supplier, temporary_enable_supplier_update(self.supplier, enable=True))
		self.db_set("is_temporarily_enabled", 1)
		self.db_set("temporarily_enabled_on", now)
		expires_on = add_to_date(now, days=get_temporary_enable_days())
		self.add_comment(
			"Comment",
			text=frappe._("Supplier temporarily enabled until {0} (from {1}).").format(
				format_datetime(expires_on), frappe._(source)
			),
		)
		return {
			"is_temporarily_enabled": True,
			"temporarily_enabled_on": now,
			"expires_on": expires_on,
		}


def get_supplier_temporary_enable_checklist_name(supplier):
	# The Checklist that holds this Supplier's temporary-enable state: its
	# latest submitted, not-failed one. None if the Supplier isn't in one of
	# SUPPLIER_TEMPORARY_ENABLE_STAGES or has no such Checklist. Both the
	# Checklist and the Supplier button only act through this one.
	stage = frappe.db.get_value("Supplier", supplier, "vendor_lifecycle_stage")
	if stage not in SUPPLIER_TEMPORARY_ENABLE_STAGES:
		return None
	return frappe.db.get_value(
		"Vendor Deboarding Checklist",
		{"supplier": supplier, "docstatus": 1, "mark_as_failed": 0},
		"name",
		order_by="creation desc",
	)


def get_supplier_temporary_enable_checklist(supplier):
	name = get_supplier_temporary_enable_checklist_name(supplier)
	return frappe.get_doc("Vendor Deboarding Checklist", name) if name else None


def clear_temporary_enable(supplier):
	"""Drops any open temporary-enable window on this Supplier's checklists
	without touching the Supplier — called once re-boarding is decided
	(passed or failed), so a leftover window can't disable the vendor later."""
	for name in frappe.get_all(
		"Vendor Deboarding Checklist", filters={"supplier": supplier, "is_temporarily_enabled": 1}, pluck="name"
	):
		frappe.db.set_value(
			"Vendor Deboarding Checklist", name, {"is_temporarily_enabled": 0, "temporarily_enabled_on": None}
		)


def temporary_enable_supplier_update(supplier, enable, stage=None):
	# Enabled/Disabled always follows the toggle; the status only while
	# Deboarded — in re-boarding it shows the re-boarding progress instead.
	update = {"disabled": 0 if enable else 1}
	if stage is None:
		stage = frappe.db.get_value("Supplier", supplier, "vendor_lifecycle_stage")
	if stage not in (VENDOR_LIFECYCLE_STAGE_REBOARDING, VENDOR_LIFECYCLE_STAGE_REBOARDING_FAILED):
		update["vendor_lifecycle_status"] = TEMPORARILY_ENABLED_STATUS if enable else "Disabled"
	return update


def set_supplier_temporary_enable_onload(doc, method=None):
	"""Wired via hooks.py doc_events on Supplier's "onload" — attaches the
	same button info the Checklist form uses, so supplier.js can draw the
	toggle synchronously from frm.doc.__onload."""
	checklist = get_supplier_temporary_enable_checklist(doc.name)
	if not checklist:
		return
	info = checklist.get_temporary_enable_button_info()
	if info.get("show"):
		doc.set_onload("temporary_enable", info)


@frappe.whitelist()
def toggle_supplier_temporary_enable(supplier):
	# Supplier-form twin of VendorDeboardingChecklist.toggle_temporary_enable:
	# acts on the same Checklist, so both buttons always show the same state.
	checklist = get_supplier_temporary_enable_checklist(supplier)
	if not checklist:
		frappe.throw(
			frappe._("This Supplier has no submitted Deboarding Checklist, or is not in a stage that allows this.")
		)
	return checklist._toggle_temporary_enable(source="Supplier")


@frappe.whitelist()
def get_default_checklist():
	"""Called from the client the moment a new Vendor Deboarding Checklist
	form is opened, so the Checklist Template and its items appear
	immediately, rather than only after the first save. before_insert()
	still does the same resolution server-side, as a fallback for any
	document created outside the UI."""
	require_internal_user()
	template_name = frappe.db.get_single_value("Vendor Lifecycle Settings", "default_checklist_template")
	if not template_name:
		return {"checklist_template": None, "items": []}

	template = frappe.get_cached_doc("Vendor Deboarding Checklist Template", template_name)
	return {
		"checklist_template": template_name,
		# Rows appended via self.append() inside before_insert() never go
		# through _set_defaults() (that already ran earlier in the insert
		# flow) — status has to be set explicitly, not left to the
		# DocField's own "default".
		"items": [
			{"task": row.task, "category": row.category, "status": "Not Started"} for row in template.items
		],
	}
