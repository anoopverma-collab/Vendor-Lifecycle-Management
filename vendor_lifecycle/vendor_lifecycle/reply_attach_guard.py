# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

"""Runs an inbound-reply auto-attach handler (signoff_reply /
checklist_clearance_reply) all-or-nothing.

Each handler moves the reply's File onto the document, fills its Attach
field, comments and notifies. If any step raises partway through, a plain
try/except would keep whatever already ran (e.g. the File re-homed but the
field still empty). Instead the whole attempt runs inside a savepoint and is
rolled back on failure, then:
- an Error Log is written (linked to the document),
- a short Comment is left on the document,
- the Creator is emailed (CC Vendor Lifecycle Manager + Always CC) with the
  document and Error Log IDs,
- on the first failure only, one retry is queued in the background.

Never raises — this runs inside the scheduled email pull, which must not be
aborted for the whole site by one bad reply.
"""

import frappe

from vendor_lifecycle.vendor_lifecycle.vendor_creation import notify_attachment_save_failed

# handler_key -> (document doctype the reply must reference, handler path).
# Referenced by dotted path (not imported) since both handler modules import
# this one.
REPLY_HANDLERS = {
	"signoff": (
		"Vendor Sign Off",
		"vendor_lifecycle.vendor_lifecycle.signoff_reply._handle_signoff_reply",
	),
	"clearance": (
		"Vendor Deboarding Checklist",
		"vendor_lifecycle.vendor_lifecycle.checklist_clearance_reply._handle_checklist_clearance_reply",
	),
}

# Counts failures per (handler, Communication) — Communication on_update can
# fire more than once for one email (insert, then again once attachments are
# saved), so this keeps it to one retry and at most two reports per email.
FAILURE_COUNT_CACHE_KEY = "vendor_lifecycle_reply_attach_failures:{0}:{1}"
FAILURE_COUNT_TTL_SECONDS = 7 * 24 * 60 * 60


def run_reply_handler(handler_key, communication, is_retry=False):
	if frappe.flags.in_reply_attach_failure_report:
		# The failure email below is itself a Communication, so it fires
		# these same on_update hooks — never let reporting one failure
		# trigger (and possibly fail, and report) another.
		return
	document_doctype, handler_path = REPLY_HANDLERS[handler_key]
	save_point = f"vl_reply_attach_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(save_point)
	try:
		frappe.get_attr(handler_path)(communication)
	except Exception:
		traceback = frappe.get_traceback()
		frappe.db.rollback(save_point=save_point)
		frappe.clear_last_message()
		frappe.flags.in_reply_attach_failure_report = True
		try:
			_handle_failure(handler_key, document_doctype, communication, traceback, is_retry)
		finally:
			frappe.flags.in_reply_attach_failure_report = False
	else:
		frappe.db.release_savepoint(save_point)


def retry_reply_handler(communication_name, handler_key):
	"""Background job — the one automatic retry after a first failure."""
	if not frappe.db.exists("Communication", communication_name):
		return
	run_reply_handler(handler_key, frappe.get_doc("Communication", communication_name), is_retry=True)


def _handle_failure(handler_key, document_doctype, communication, traceback, is_retry):
	try:
		failures = _record_failure(handler_key, communication.name)
		will_retry = not is_retry and failures == 1
		document_name = _target_document(document_doctype, communication)

		error_log_name = _log_failure(document_doctype, document_name, traceback)
		if document_name and failures <= 2:
			_comment_failure(document_doctype, document_name, error_log_name, will_retry)
			notify_attachment_save_failed(
				document_doctype,
				document_name,
				frappe.db.get_value(document_doctype, document_name, "owner"),
				{
					"error_log_name": error_log_name or "",
					"error_log_link": frappe.utils.get_url_to_form("Error Log", error_log_name)
					if error_log_name
					else "",
					"sender": communication.sender or "",
					"received_on": frappe.utils.format_datetime(communication.communication_date),
					"will_retry": will_retry,
				},
			)
		if will_retry:
			frappe.enqueue(
				"vendor_lifecycle.vendor_lifecycle.reply_attach_guard.retry_reply_handler",
				queue="short",
				enqueue_after_commit=True,
				communication_name=communication.name,
				handler_key=handler_key,
			)
	except Exception:
		frappe.log_error(
			title=f"{document_doctype}: failed to report an inbound reply attach failure",
			message=frappe.get_traceback(),
		)


def _record_failure(handler_key, communication_name):
	key = FAILURE_COUNT_CACHE_KEY.format(handler_key, communication_name)
	failures = int(frappe.cache.get_value(key) or 0) + 1
	frappe.cache.set_value(key, failures, expires_in_sec=FAILURE_COUNT_TTL_SECONDS)
	return failures


def _target_document(document_doctype, communication):
	if communication.reference_doctype != document_doctype or not communication.reference_name:
		return None
	if not frappe.db.exists(document_doctype, communication.reference_name):
		return None
	return communication.reference_name


def _log_failure(document_doctype, document_name, traceback):
	# Returns None if the Error Log itself couldn't be written — the email
	# and comment still go out, saying so.
	try:
		error_log = frappe.log_error(
			title=f"{document_doctype}: failed to save file from inbound reply",
			message=traceback,
			reference_doctype=document_doctype if document_name else None,
			reference_name=document_name,
		)
		return error_log.name if error_log else None
	except Exception:
		return None


def _comment_failure(document_doctype, document_name, error_log_name, will_retry):
	log_note = (
		frappe._("Error Log {0}").format(error_log_name)
		if error_log_name
		else frappe._("error could not be logged")
	)
	if will_retry:
		content = frappe._("Emailed file couldn't be saved ({0}). Retrying once automatically.")
	else:
		content = frappe._("Emailed file couldn't be saved ({0}). Attach it manually.")
	try:
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": document_doctype,
			"reference_name": document_name,
			"content": content.format(log_note),
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"{document_doctype}: failed to comment on an inbound reply attach failure",
			message=frappe.get_traceback(),
		)
