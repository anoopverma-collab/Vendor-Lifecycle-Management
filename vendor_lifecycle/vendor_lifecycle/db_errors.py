# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import sys

import frappe

DB_LOCK_ERRORS = (frappe.QueryDeadlockError, frappe.QueryTimeoutError)


def reraise_if_db_lock_error():
	"""Call first inside a broad `except Exception:` that keeps going (best-effort
	emails, scheduled jobs). A deadlock or lock timeout means the database has
	already rolled back the whole transaction, so carrying on would silently lose
	the caller's earlier writes — let it fail so the user or job can retry."""
	exc = sys.exc_info()[1]
	if isinstance(exc, DB_LOCK_ERRORS):
		raise exc
