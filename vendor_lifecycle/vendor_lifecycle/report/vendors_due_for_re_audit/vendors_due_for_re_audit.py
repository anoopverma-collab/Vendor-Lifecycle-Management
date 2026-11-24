# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters: dict | None = None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns() -> list[dict]:
	return [
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 200},
		{"label": _("Vendor Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 200},
		{
			"label": _("Latest Compliance Audit"),
			"fieldname": "audit",
			"fieldtype": "Link",
			"options": "Vendor Compliance Audit",
			"width": 180,
		},
		{"label": _("Audit Date"), "fieldname": "audit_date", "fieldtype": "Date", "width": 100},
		{"label": _("Valid Until"), "fieldname": "valid_until", "fieldtype": "Date", "width": 100},
		{"label": _("Days Remaining"), "fieldname": "days_remaining", "fieldtype": "Int", "width": 120},
	]


def get_data(filters: dict | None = None) -> list[dict]:
	filters = filters or {}
	# The Supplier's own compliance_audit_valid_until is the real expiry —
	# it's what a pass (or force-override) actually set, including the
	# Audit Date + validity months fallback when the audit's own Valid Until
	# was left blank. One row per vendor.
	supplier_filters = {"compliance_audit_valid_until": ["is", "set"]}
	if filters.get("vendor"):
		supplier_filters["name"] = filters["vendor"]
	if filters.get("valid_until"):
		supplier_filters["compliance_audit_valid_until"] = ["<=", filters["valid_until"]]
	suppliers = frappe.get_all(
		"Supplier",
		filters=supplier_filters,
		fields=["name as vendor", "supplier_name", "compliance_audit_valid_until as valid_until"],
		order_by="compliance_audit_valid_until asc",
	)
	if not suppliers:
		return []

	# Each vendor's most recent submitted audit that gave it that validity.
	latest_audit = {}
	for audit in frappe.get_all(
		"Vendor Compliance Audit",
		filters={"vendor": ["in", [row.vendor for row in suppliers]], "docstatus": 1},
		or_filters={"outcome": "Passed", "force_overridden": 1},
		fields=["name", "vendor", "audit_date"],
		order_by="audit_date desc, creation desc",
	):
		latest_audit.setdefault(audit.vendor, audit)

	rows = []
	for row in suppliers:
		audit = latest_audit.get(row.vendor)
		row.audit = audit.name if audit else None
		row.audit_date = audit.audit_date if audit else None
		rows.append(row)

	today = getdate(nowdate())
	for row in rows:
		row.days_remaining = (getdate(row.valid_until) - today).days if row.valid_until else None

	max_days_remaining = filters.get("days_remaining")
	if max_days_remaining not in (None, ""):
		rows = [r for r in rows if r.days_remaining is not None and r.days_remaining <= int(max_days_remaining)]
	if filters.get("expired"):
		rows = [r for r in rows if r.days_remaining is not None and r.days_remaining < 0]
	if filters.get("due_soon"):
		rows = [r for r in rows if r.days_remaining is not None and 0 <= r.days_remaining <= 30]
	if filters.get("ignore_expired"):
		# The complement of Expired Only — lets "Days Remaining (at most)"
		# express "expiring within N days, but not already expired" too,
		# which it can't do on its own (it includes negatives by design).
		rows = [r for r in rows if r.days_remaining is None or r.days_remaining >= 0]

	return rows
