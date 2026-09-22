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
		{"label": _("Ticket"), "fieldname": "ticket", "fieldtype": "Link", "options": "Vendor Support Ticket", "width": 130},
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Vendor Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 180},
		{"label": _("Subject"), "fieldname": "subject", "fieldtype": "Data", "width": 200},
		{"label": _("Priority"), "fieldname": "priority", "fieldtype": "Data", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Opened On"), "fieldname": "opened_on", "fieldtype": "Date", "width": 100},
		{"label": _("Days Open"), "fieldname": "days_open", "fieldtype": "Int", "width": 90},
	]


def get_data(filters: dict | None = None) -> list[dict]:
	filters = filters or {}
	conditions = []
	params = {}
	if filters.get("status"):
		conditions.append("vst.status = %(status)s")
		params["status"] = filters["status"]
	if filters.get("vendor"):
		conditions.append("vst.vendor = %(vendor)s")
		params["vendor"] = filters["vendor"]
	if filters.get("ticket"):
		conditions.append("vst.name = %(ticket)s")
		params["ticket"] = filters["ticket"]
	if filters.get("priority"):
		conditions.append("vst.priority = %(priority)s")
		params["priority"] = filters["priority"]
	# Every ticket, regardless of status, unless narrowed down above — a
	# Closed/Resolved/Invalid ticket still has a real "Days Open" (however
	# long it stayed open before that), so there's no reason to hide it by
	# default.
	where_clause = " and ".join(conditions) if conditions else "1=1"

	rows = frappe.db.sql(
		f"""
		select vst.name as ticket, vst.vendor, s.supplier_name, vst.subject, vst.priority, vst.status,
			vst.opened_on, vst.closed_on, vst.resolved_on
		from `tabVendor Support Ticket` vst
		left join `tabSupplier` s on s.name = vst.vendor
		where {where_clause}
		order by vst.opened_on asc
		""",
		params,
		as_dict=True,
	)

	today = getdate(nowdate())
	for row in rows:
		if not row.opened_on:
			row.days_open = None
		else:
			# Closed On wins if both are set (a ticket resolved and later
			# actually closed keeps counting until the close, not frozen
			# early at resolution) — Resolved On is the fallback freeze
			# point otherwise, so a Resolved-but-not-yet-closed ticket
			# doesn't look like it's still aging every day either. "today"
			# is the last resort, for a ticket that's still genuinely open.
			end_date = getdate(row.closed_on or row.resolved_on) if (row.closed_on or row.resolved_on) else today
			row.days_open = (end_date - getdate(row.opened_on)).days
		row.pop("closed_on", None)
		row.pop("resolved_on", None)

	more_than_days_opened = filters.get("more_than_days_opened")
	if more_than_days_opened not in (None, ""):
		rows = [r for r in rows if r.days_open is not None and r.days_open > int(more_than_days_opened)]

	return rows
