# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

"""Resolves an inbound vendor reply to a Vendor Deboarding Checklist's own
clearance-certificate email back onto the Signed Clearance Certificate
field.

Same mechanism (and same interim-design caveats) as
vendor_lifecycle.signoff_reply — Frappe's own inbound-mail pull threads a
reply back to the Communication it was sent in reply to via the
In-Reply-To header, inheriting that parent's reference_doctype/
reference_name automatically. Unlike Sign-off, there's only ever one
document in flight here (the clearance certificate), so no
document-type tag is needed — signed_clearance_certificate already
being set is itself the idempotency guard.

Breaks the same way Sign-off's own version does — a forward instead of a
reply, or more than one attachment — both handled by refusing to guess:
a Comment note is left on the document, and the Creator + Vendor
Lifecycle Manager are emailed (see vendor_creation.notify_manual_attach_
needed) to attach the file by hand.

Runs as a doc_events "on_update" hook on Communication, alongside
signoff_reply's own handler — an unhandled exception here must never be
allowed to abort the scheduled email_account.pull() job for the whole
site, so every path below is defensive and logs rather than raises.
"""

import frappe

from vendor_lifecycle.vendor_lifecycle.vendor_creation import notify_manual_attach_needed


def handle_checklist_clearance_reply(doc, method=None):
	try:
		_handle_checklist_clearance_reply(doc)
	except Exception:
		frappe.log_error(
			title="Vendor Deboarding Checklist: failed to process inbound clearance reply",
			message=frappe.get_traceback(),
		)


def _handle_checklist_clearance_reply(doc):
	if doc.communication_type != "Communication" or doc.communication_medium != "Email":
		return
	if doc.sent_or_received != "Received":
		return
	if doc.reference_doctype != "Vendor Deboarding Checklist" or not doc.reference_name:
		return
	checklist = frappe.get_doc("Vendor Deboarding Checklist", doc.reference_name)
	if checklist.docstatus != 0:
		return
	if checklist.signed_clearance_certificate:
		# Already received — idempotency guard for the success path.
		return

	if not doc.in_reply_to:
		# The vendor forwarded instead of replying, which loses Frappe's
		# own In-Reply-To threading — nothing to auto-resolve from. Same
		# idempotency guard as the "too many attachments" case below.
		already_noted = frappe.db.exists("Comment", {
			"reference_doctype": "Vendor Deboarding Checklist",
			"reference_name": checklist.name,
			"content": ["like", f"%{doc.name}%"],
		})
		if already_noted:
			return
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Vendor Deboarding Checklist",
			"reference_name": checklist.name,
			"content": frappe._(
				"An email ({0}) arrived for this Checklist but couldn't be automatically matched (it looks"
				" like a forward, not a reply) — attach the signed clearance certificate manually."
			).format(doc.name),
		}).insert(ignore_permissions=True)
		notify_manual_attach_needed(
			"Vendor Deboarding Checklist", checklist.name, checklist.owner,
			frappe._("An inbound email couldn't be automatically matched (it looks like a forward, not a reply)."),
		)
		return

	if not _sender_is_verified(doc.sender, checklist):
		# Same idempotency guard as the "forward, not a reply" and "more
		# than one attachment" cases below — on_update fires every time
		# this Communication is saved, not just once, so without this a
		# single rejected reply logs a duplicate comment on every re-save
		# (confirmed happening in practice: the same rejection was logged
		# twice for one inbound email).
		already_noted = frappe.db.exists("Comment", {
			"reference_doctype": "Vendor Deboarding Checklist",
			"reference_name": checklist.name,
			"content": ["like", f"%{doc.name}%"],
		})
		if already_noted:
			return
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Vendor Deboarding Checklist",
			"reference_name": checklist.name,
			"content": frappe._(
				"Ignored a reply ({0}) from an unverified sender ({1}) — it does not match the Supplier's"
				" contact email or Additional Email on this Checklist."
			).format(doc.name, doc.sender),
		}).insert(ignore_permissions=True)
		return

	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Communication", "attached_to_name": doc.name},
		fields=["name", "file_url"],
	)
	if not files:
		return
	if len(files) > 1:
		# Idempotency guard for this path — the file is deliberately left
		# alone, so a fresh scan would otherwise re-post this same note on
		# every future pull cycle.
		already_noted = frappe.db.exists("Comment", {
			"reference_doctype": "Vendor Deboarding Checklist",
			"reference_name": checklist.name,
			"content": ["like", f"%{doc.name}%"],
		})
		if already_noted:
			return
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Vendor Deboarding Checklist",
			"reference_name": checklist.name,
			"content": frappe._(
				"A reply ({0}) was received with more than one attachment — attach the signed clearance"
				" certificate manually."
			).format(doc.name),
		}).insert(ignore_permissions=True)
		notify_manual_attach_needed(
			"Vendor Deboarding Checklist", checklist.name, checklist.owner,
			frappe._("A reply was received with more than one attachment — the correct file couldn't be guessed."),
		)
		return

	file_row = files[0]

	# Re-home the File onto the Checklist's own Attach field instead of
	# copying its content — it keeps showing up under the Checklist's
	# attachments, and the Communication (with the email content/timeline
	# entry) is untouched.
	frappe.db.set_value(
		"File",
		file_row.name,
		{
			"attached_to_doctype": "Vendor Deboarding Checklist",
			"attached_to_name": checklist.name,
			"attached_to_field": "signed_clearance_certificate",
		},
	)
	checklist.db_set("signed_clearance_certificate", file_row.file_url, update_modified=False)

	# db_set() above bypasses doc_events, so the Checklist's own
	# on_update() never sees this change — notify explicitly instead.
	checklist.notify_clearance_certificate_received()

	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Comment",
		"reference_doctype": "Vendor Deboarding Checklist",
		"reference_name": checklist.name,
		"content": frappe._(
			"Signed clearance certificate received via email reply from {0} and attached automatically."
		).format(doc.sender),
	}).insert(ignore_permissions=True)


def _sender_is_verified(sender, checklist):
	# Reuses _clearance_recipients() itself, rather than its own separate
	# list of sources — anyone the clearance email actually went out to
	# (KYC Official Email, Supplier Primary Contact, or Additional Email)
	# must also be recognised as a verified sender when they reply, or a
	# legitimate recipient's reply gets wrongly rejected as unverified.
	if not sender:
		return False
	sender = sender.strip().lower()
	candidates = {email.strip().lower() for email in checklist._clearance_recipients()}
	return sender in candidates
