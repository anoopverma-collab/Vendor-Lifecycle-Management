# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters: dict | None = None):
	columns = get_columns()
	data = get_data()
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


def get_data() -> list[dict]:
	rows = frappe.db.sql(
		"""
		select vst.name as ticket, vst.vendor, s.supplier_name, vst.subject, vst.priority, vst.status, vst.opened_on
		from `tabVendor Support Ticket` vst
		left join `tabSupplier` s on s.name = vst.vendor
		where vst.status in ('Open', 'In Progress', 'Reopened')
		order by vst.opened_on asc
		""",
		as_dict=True,
	)

	today = getdate(nowdate())
	for row in rows:
		row.days_open = (today - getdate(row.opened_on)).days if row.opened_on else None

	return rows
