# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

"""Resolves an inbound vendor reply to a Vendor Sign-off email back to the
right Attach field (Signed Contract / Signed Code of Conduct).

Interim design, per explicit product decision: send_signoff_email() sends
one email per document instead of one combined email, purely so each
outbound Communication can be tagged with which document it concerns
(vendor_lifecycle_signoff_document_type). Frappe's own inbound-mail pull
threads a reply back to the Communication it was sent in reply to via the
In-Reply-To header (see frappe.email.receive.InboundMail), inheriting that
parent's reference_doctype/reference_name automatically — so by the time
this hook runs, an inbound Communication on "Vendor Sign Off" already
carries in_reply_to pointing at our own outbound Communication. This module
only has to read that parent's tag, verify the sender, and copy the single
attachment across.

This is a stopgap, not the final design — it breaks if a vendor forwards
instead of replies (breaks Frappe's own threading), or replies with more
than one attachment. Both cases are handled below by refusing to guess:
a Comment note is left on the document, and the Creator + Vendor
Lifecycle Manager are emailed (see vendor_creation.notify_manual_attach_
needed) to attach the file by hand.

Runs as a doc_events "on_update" hook on Communication, which fires during
the scheduled email_account.pull() job — an unhandled exception here must
never be allowed to abort that job for the whole site, so every path below
is defensive and logs rather than raises.
"""

import frappe

from vendor_lifecycle.vendor_lifecycle.vendor_creation import notify_manual_attach_needed

DOCUMENT_TYPE_FIELD = {
	"Contract": "signed_contract",
	"Code of Conduct": "code_of_conduct_document",
}


def handle_signoff_reply(doc, method=None):
	try:
		_handle_signoff_reply(doc)
	except Exception:
		frappe.log_error(
			title="Vendor Sign Off: failed to process inbound reply",
			message=frappe.get_traceback(),
		)


def _handle_signoff_reply(doc):
	if doc.communication_type != "Communication" or doc.communication_medium != "Email":
		return
	if doc.sent_or_received != "Received":
		return
	if doc.reference_doctype != "Vendor Sign Off" or not doc.reference_name:
		return

	sign_off = frappe.get_doc("Vendor Sign Off", doc.reference_name)
	if sign_off.docstatus != 0:
		return

	if not doc.in_reply_to:
		# The vendor forwarded instead of replying, which loses Frappe's
		# own In-Reply-To threading — nothing to resolve the document type
		# from. Comment-content guard since there's no document-type tag
		# to check yet at this point.
		already_noted = frappe.db.exists("Comment", {
			"reference_doctype": "Vendor Sign Off",
			"reference_name": sign_off.name,
			"content": ["like", f"%{doc.name}%"],
		})
		if already_noted:
			return
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Vendor Sign Off",
			"reference_name": sign_off.name,
			"content": frappe._(
				"An email ({0}) arrived for this Sign-off but couldn't be automatically matched (it looks"
				" like a forward, not a reply) — attach the signed document manually."
			).format(doc.name),
		}).insert(ignore_permissions=True)
		notify_manual_attach_needed(
			"Vendor Sign Off", sign_off.name, sign_off.owner,
			frappe._("An inbound email couldn't be automatically matched (it looks like a forward, not a reply)."),
		)
		return

	# Already resolved on an earlier on_update of this same Communication
	# (a File attachment is saved in a second pass after insert) — this
	# also doubles as this handler's idempotency guard.
	if doc.vendor_lifecycle_signoff_document_type:
		return

	document_type = frappe.db.get_value("Communication", doc.in_reply_to, "vendor_lifecycle_signoff_document_type")
	if not document_type or document_type not in DOCUMENT_TYPE_FIELD:
		return

	if not _sender_is_verified(doc.sender, sign_off):
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Vendor Sign Off",
			"reference_name": sign_off.name,
			"content": frappe._(
				"Ignored a reply from an unverified sender ({0}) — it does not match the Supplier Email or"
				" Additional Email on this Sign-off."
			).format(doc.sender),
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
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Vendor Sign Off",
			"reference_name": sign_off.name,
			"content": frappe._(
				"A reply for the {0} was received with more than one attachment — attach the correct"
				" signed file manually."
			).format(document_type),
		}).insert(ignore_permissions=True)
		frappe.db.set_value("Communication", doc.name, "vendor_lifecycle_signoff_document_type", document_type)
		notify_manual_attach_needed(
			"Vendor Sign Off", sign_off.name, sign_off.owner,
			frappe._(
				"A reply for the {0} was received with more than one attachment — the correct file couldn't"
				" be guessed."
			).format(document_type),
		)
		return

	file_row = files[0]
	fieldname = DOCUMENT_TYPE_FIELD[document_type]

	# Re-home the File onto the Sign-off's own Attach field instead of
	# copying its content — it keeps showing up under Vendor Sign Off's
	# attachments, and the Communication (with the email content/timeline
	# entry) is untouched.
	frappe.db.set_value(
		"File",
		file_row.name,
		{"attached_to_doctype": "Vendor Sign Off", "attached_to_name": sign_off.name, "attached_to_field": fieldname},
	)
	sign_off.db_set(fieldname, file_row.file_url, update_modified=False)
	frappe.db.set_value("Communication", doc.name, "vendor_lifecycle_signoff_document_type", document_type)

	# db_set() above bypasses doc_events, so Vendor Sign Off's own
	# on_update() never sees this change — notify explicitly instead.
	sign_off.notify_document_received(document_type)

	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Comment",
		"reference_doctype": "Vendor Sign Off",
		"reference_name": sign_off.name,
		"content": frappe._("{0} received via email reply from {1} and attached automatically.").format(
			document_type, doc.sender
		),
	}).insert(ignore_permissions=True)


def _sender_is_verified(sender, sign_off):
	if not sender:
		return False
	sender = sender.strip().lower()
	candidates = {
		(sign_off.supplier_email or "").strip().lower(),
		(sign_off.additional_email or "").strip().lower(),
	}
	candidates.discard("")
	return sender in candidates
