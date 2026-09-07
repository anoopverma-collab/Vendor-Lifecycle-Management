# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, nowdate

CADENCE_DAYS = {
	"Monthly": 30,
	"Quarterly": 90,
	"Half-Yearly": 180,
	"Yearly": 365,
}

FIRST_REMINDER_AFTER_DAYS = 7
REMINDER_REPEAT_DAYS = 30

# Priority-based escalation cadence for a stale support ticket — a High
# priority ticket that's been sitting untouched deserves a much faster
# nudge to staff than a Low one. (days after opened_on, then repeat every
# this many days for as long as it stays Open/Reopened.)
SUPPORT_TICKET_ESCALATION_DAYS = {
	"High": 1,
	"Medium": 3,
	"Low": 7,
}


def create_pending_satisfaction_surveys():
	"""Daily scheduler job: for every enabled, non-frozen Supplier without a
	survey inside the configured cadence window, create a new (draft) one.
	Deliberately not scoped to vendor_lifecycle_status — this only cares
	whether the Supplier is currently enabled and not on hold, regardless of
	whether it ever went through this app's own onboarding pipeline.
	disable_satisfaction_survey is a per-vendor opt-out on top of that — an
	internal user can still create one by hand for such a vendor, this only
	stops the scheduler."""
	settings = frappe.get_single("Vendor Lifecycle Settings")
	if not settings.enable_satisfaction_surveys:
		return []

	cutoff = add_days(nowdate(), -CADENCE_DAYS.get(settings.survey_cadence, 90))
	# last_satisfaction_survey_date (kept in sync by VendorSatisfactionSurvey.
	# after_insert) is the actual source of truth here — one query for every
	# candidate vendor instead of a separate exists() check per vendor.
	active_vendors = frappe.get_all(
		"Supplier",
		filters={"disabled": 0, "on_hold": 0, "disable_satisfaction_survey": 0},
		fields=["name", "last_satisfaction_survey_date"],
	)

	created = []
	for vendor in active_vendors:
		if vendor.last_satisfaction_survey_date and getdate(vendor.last_satisfaction_survey_date) >= getdate(cutoff):
			continue

		try:
			doc = frappe.get_doc({
				"doctype": "Vendor Satisfaction Survey",
				"vendor": vendor.name,
				"period": settings.survey_cadence,
				"is_system_generated": 1,
			}).insert(ignore_permissions=True)
			created.append(doc.name)
		except frappe.ValidationError:
			# No usable Rating Template for this vendor (see before_insert)
			# — skip it rather than let one vendor's missing template abort
			# survey creation for every other vendor in this run.
			frappe.log_error(title="Satisfaction survey skipped: no Rating Template", message=vendor.name)
			frappe.db.rollback()

	return created


def send_satisfaction_survey_reminders():
	"""Daily scheduler job: nudge whoever hasn't completed (submitted) a
	Satisfaction Survey yet — first 7 days after it was created, then every
	30 days after that for as long as it's still a draft."""
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_satisfaction_survey.vendor_satisfaction_survey import (
		DEFAULT_SATISFACTION_SURVEY_REMINDER_EMAIL_TEMPLATE,
		get_survey_portal_link,
		get_survey_vendor_contact,
	)
	from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
		resolve_vendor_lifecycle_company_name,
		send_vendor_lifecycle_email,
		vendor_lifecycle_emails_enabled,
	)

	settings = frappe.get_single("Vendor Lifecycle Settings")
	if not vendor_lifecycle_emails_enabled(settings):
		return []

	today = getdate(nowdate())
	pending = frappe.get_all(
		"Vendor Satisfaction Survey",
		filters={"docstatus": 0},
		fields=["name", "vendor", "period", "survey_date", "creation", "last_reminder_sent"],
	)

	reminded = []
	for survey in pending:
		due_on = (
			add_days(getdate(survey.last_reminder_sent), REMINDER_REPEAT_DAYS)
			if survey.last_reminder_sent
			else add_days(getdate(survey.creation), FIRST_REMINDER_AFTER_DAYS)
		)
		if due_on > today:
			continue

		try:
			contact = get_survey_vendor_contact(survey.vendor)
			if not contact.get("official_email"):
				continue
			# Vendor Lifecycle Manager is CC'd automatically by
			# send_vendor_lifecycle_email() (vendor_lifecycle_cc_list) —
			# no separate recipient list needed for that.
			send_vendor_lifecycle_email(
				doctype="Vendor Satisfaction Survey",
				name=survey.name,
				template_name=DEFAULT_SATISFACTION_SURVEY_REMINDER_EMAIL_TEMPLATE,
				context={
					"firm_name": contact.get("firm_name"),
					"contact_person_name": contact.get("contact_person_name"),
					"company_name": resolve_vendor_lifecycle_company_name(),
					"survey_name": survey.name,
					"period": survey.period,
					"survey_date": frappe.utils.formatdate(survey.survey_date),
					"survey_link": get_survey_portal_link(survey.name),
				},
				recipients=[contact["official_email"]],
			)
			frappe.db.set_value("Vendor Satisfaction Survey", survey.name, "last_reminder_sent", today)
			reminded.append(survey.name)
		except Exception:
			frappe.log_error(
				title="Vendor Satisfaction Survey: failed to send reminder email", message=frappe.get_traceback()
			)

	return reminded


def send_support_ticket_escalations():
	"""Daily scheduler job: nudge the internal team about a support ticket
	that's sitting untouched — only while it's Open or Reopened (paused
	once someone's replied and it moves to In Progress, or once it's
	Resolved/Closed). Cadence depends on Priority (see
	SUPPORT_TICKET_ESCALATION_DAYS) — High gets escalated far sooner and
	more often than Low."""
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_support_ticket.vendor_support_ticket import (
		DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE,
		get_ticket_portal_link,
		get_ticket_vendor_contact,
	)
	from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
		resolve_vendor_lifecycle_company_name,
		send_vendor_lifecycle_email,
		vendor_lifecycle_emails_enabled,
		vendor_lifecycle_manager_emails,
	)

	settings = frappe.get_single("Vendor Lifecycle Settings")
	if not vendor_lifecycle_emails_enabled(settings):
		return []

	recipients = vendor_lifecycle_manager_emails()
	if not recipients:
		return []

	today = getdate(nowdate())
	stale_candidates = frappe.get_all(
		"Vendor Support Ticket",
		filters={"status": ["in", ("Open", "Reopened")]},
		fields=["name", "vendor", "subject", "priority", "opened_on", "last_reminder_sent"],
	)

	escalated = []
	for ticket in stale_candidates:
		interval = SUPPORT_TICKET_ESCALATION_DAYS.get(ticket.priority, 3)
		due_on = (
			add_days(getdate(ticket.last_reminder_sent), interval)
			if ticket.last_reminder_sent
			else add_days(getdate(ticket.opened_on), interval)
		)
		if due_on > today:
			continue

		try:
			contact = get_ticket_vendor_contact(ticket.vendor)
			send_vendor_lifecycle_email(
				doctype="Vendor Support Ticket",
				name=ticket.name,
				template_name=DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE,
				context={
					"firm_name": contact.get("firm_name"),
					"company_name": resolve_vendor_lifecycle_company_name(),
					"ticket_name": ticket.name,
					"subject": ticket.subject,
					"priority": ticket.priority,
					"ticket_link": get_ticket_portal_link(ticket.name),
				},
				recipients=recipients,
			)
			frappe.db.set_value("Vendor Support Ticket", ticket.name, "last_reminder_sent", today)
			escalated.append(ticket.name)
		except Exception:
			frappe.log_error(
				title="Vendor Support Ticket: failed to send escalation email", message=frappe.get_traceback()
			)

	return escalated


def auto_disable_expired_temporary_enables():
	"""Runs at 2 AM daily (see hooks.py's "cron" scheduler_events — the
	generic "daily" bucket doesn't let a job pick its own time). Any
	Vendor Deboarding Checklist still flagged is_temporarily_enabled once
	its TEMPORARY_ENABLE_DAYS grace window has actually elapsed gets its
	Supplier disabled again and the flag cleared — ready to be temporarily
	enabled again later if needed. A flag that's already unticked is
	skipped outright by the filter itself; one that's ticked but still
	within its window is left alone."""
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_checklist.vendor_deboarding_checklist import (
		TEMPORARY_ENABLE_DAYS,
	)

	cutoff = add_to_date(now_datetime(), days=-TEMPORARY_ENABLE_DAYS)
	expired = frappe.get_all(
		"Vendor Deboarding Checklist",
		filters={"is_temporarily_enabled": 1, "temporarily_enabled_on": ["<=", cutoff]},
		fields=["name", "supplier"],
	)

	for row in expired:
		frappe.db.set_value("Supplier", row.supplier, {
			"disabled": 1,
			"vendor_lifecycle_status": "Disabled",
		})
		frappe.db.set_value("Vendor Deboarding Checklist", row.name, "is_temporarily_enabled", 0)

	return [row.name for row in expired]


def send_checklist_assignment_reminders():
	"""Daily scheduler job: every ASSIGNMENT_REMINDER_DAYS, nudge whoever
	hasn't finished their assigned checklist item(s) yet — only while the
	Checklist is still Draft (stops once submitted or cancelled)."""
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_checklist.vendor_deboarding_checklist import (
		ASSIGNMENT_REMINDER_DAYS,
		DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE,
		OPEN_ITEM_STATUSES,
	)

	cutoff = add_to_date(now_datetime(), days=-ASSIGNMENT_REMINDER_DAYS)
	checklist_names = frappe.get_all("Vendor Deboarding Checklist", filters={"docstatus": 0}, pluck="name")

	reminded = []
	for name in checklist_names:
		try:
			checklist = frappe.get_doc("Vendor Deboarding Checklist", name)
		except frappe.DoesNotExistError:
			continue

		due_users = set()
		for row in checklist.checklist_items:
			if row.status not in OPEN_ITEM_STATUSES:
				continue
			if row.last_notified_on and get_datetime(row.last_notified_on) > cutoff:
				continue
			for user in (row.assigned_to_1, row.assigned_to_2):
				if user:
					due_users.add(user)

		for user_email in due_users:
			try:
				if checklist._send_task_email(user_email, DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE):
					reminded.append((name, user_email))
			except Exception:
				frappe.log_error(
					title="Vendor Deboarding Checklist: failed to send task reminder",
					message=frappe.get_traceback(),
				)

	return reminded


def send_deboarding_checklist_followups():
	"""Daily scheduler job: every CLEARANCE_FOLLOWUP_DAYS, nudge the vendor
	who hasn't sent back the signed clearance certificate yet — only
	while the Checklist is still Draft, and only once the certificate
	email has actually been sent at least once (last_reminder_sent set)."""
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_checklist.vendor_deboarding_checklist import (
		CLEARANCE_FOLLOWUP_DAYS,
	)

	cutoff = add_days(nowdate(), -CLEARANCE_FOLLOWUP_DAYS)
	candidates = frappe.get_all(
		"Vendor Deboarding Checklist",
		filters={
			"docstatus": 0,
			"last_reminder_sent": ["<=", cutoff],
			"signed_clearance_certificate": ["in", ["", None]],
		},
		pluck="name",
	)

	sent = []
	for name in candidates:
		try:
			checklist = frappe.get_doc("Vendor Deboarding Checklist", name)
			if checklist._send_clearance_followup_email():
				sent.append(name)
		except Exception:
			frappe.log_error(
				title="Vendor Deboarding Checklist: failed to send clearance follow-up",
				message=frappe.get_traceback(),
			)

	return sent


def send_signoff_followups():
	"""Daily scheduler job: every SIGNOFF_FOLLOWUP_DAYS, nudge the vendor
	who hasn't sent back a required signed document yet — only while the
	Sign-off is still Draft (and not marked Failed), and only once the
	Sign-off email has actually been sent at least once."""
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_sign_off.vendor_sign_off import SIGNOFF_FOLLOWUP_DAYS

	cutoff = add_days(nowdate(), -SIGNOFF_FOLLOWUP_DAYS)
	candidates = frappe.get_all(
		"Vendor Sign Off",
		filters={"docstatus": 0, "sign_off_failed": 0, "last_reminder_sent": ["<=", cutoff]},
		pluck="name",
	)

	sent = []
	for name in candidates:
		try:
			sign_off = frappe.get_doc("Vendor Sign Off", name)
			if sign_off._missing_documents() and sign_off._send_signoff_followup_email():
				sent.append(name)
		except Exception:
			frappe.log_error(title="Vendor Sign Off: failed to send follow-up", message=frappe.get_traceback())

	return sent
